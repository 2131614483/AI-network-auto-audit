"""Explicit current-chat relay for synthetic planning acceptance, never an AI.

The loopback server exports real OpenAI-compatible requests and waits for the
current Codex chat to submit each answer. There is no generated fallback and
no route that can execute plugins, commands or fetch external resources.
"""
from __future__ import annotations

import hmac
import json
import math
import os
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import Field

from .evidence import (
    AuditTaskBrief,
    CandidateDraft,
    ClosedModel,
    PlanningContext,
    ReviewResult,
    fingerprint,
)

_BINDINGS = ("request_id", "tenant_id", "trace_id", "session_id", "role", "round_no", "messages_sha256")
_MAX_BYTES = 131_072


class SessionModelRequest(ClosedModel):
    request_id: UUID
    tenant_id: UUID
    trace_id: UUID
    session_id: UUID
    role: Literal["audit_brain", "planner", "reviewer"]
    round_no: int = Field(strict=True, ge=1, le=8)
    messages_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    messages: list[dict[str, str]]
    model: Literal["codex-current-session"] = "codex-current-session"
    transport: Literal["manual-session-relay"] = "manual-session-relay"


class SessionModelResponse(ClosedModel):
    request_id: UUID
    tenant_id: UUID
    trace_id: UUID
    session_id: UUID
    role: Literal["audit_brain", "planner", "reviewer"]
    round_no: int = Field(strict=True, ge=1, le=8)
    messages_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    answer: dict[str, Any]
    answer_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


def _read(path: Path) -> Any:
    with path.open("rb") as stream:
        raw = stream.read(_MAX_BYTES + 1)
    if len(raw) > _MAX_BYTES:
        raise ValueError("session exchange exceeds byte budget")
    return json.loads(raw)


def _create(path: Path, value: Any) -> Path:
    raw = json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2).encode("utf-8")
    if len(raw) > _MAX_BYTES:
        raise ValueError("session exchange exceeds byte budget")
    # Publish atomically without replacing any request or prior answer.
    temporary = path.parent / (uuid4().hex + ".part")
    with temporary.open("xb") as stream:
        stream.write(raw)
    try:
        os.link(temporary, path)
    finally:
        temporary.unlink()
    return path


def _validated_answer(role: str, answer: dict[str, Any]) -> dict[str, Any]:
    models: dict[str, type[ClosedModel]] = {"audit_brain": AuditTaskBrief, "planner": CandidateDraft, "reviewer": ReviewResult}
    model = models[role]
    # Preserve the exact submitted object; schema defaults are applied later by
    # the project role, not silently added to the audited response.
    model.model_validate(answer)
    return answer


def submit_session_response(exchange_dir: Path, request_id: UUID | str, answer: dict[str, Any]) -> Path:
    root = exchange_dir.resolve(strict=True)
    request = SessionModelRequest.model_validate(_read(root / "requests" / f"{UUID(str(request_id))}.json"))
    if fingerprint(request.messages) != request.messages_sha256:
        raise ValueError("exported request content changed")
    _validated_answer(request.role, answer)
    payload = {key: request.model_dump(mode="json")[key] for key in _BINDINGS}
    response = SessionModelResponse(**payload, answer=answer, answer_sha256=fingerprint(answer))
    return _create(root / "responses" / f"{request.request_id}.json", response.model_dump(mode="json"))


class SessionModelBridge:
    """A token-protected loopback endpoint bound to one test session identity."""

    model = "codex-current-session"

    def __init__(self, exchange_dir: Path, context: PlanningContext, *, wait_seconds: float = 120) -> None:
        if isinstance(wait_seconds, bool) or not math.isfinite(wait_seconds) or not 0 < wait_seconds <= 600:
            raise ValueError("session response wait must be in (0, 600] seconds")
        self.root = exchange_dir.resolve()
        self.context = context
        self.wait_seconds = wait_seconds
        self.token = secrets.token_urlsafe(32)
        self._stopping = threading.Event()
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.base_url = ""
        for name in ("requests", "responses"):
            (self.root / name).mkdir(parents=True, exist_ok=True)

    def __enter__(self) -> SessionModelBridge:
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: Any) -> None:
                pass  # Headers and credential tokens never enter logs.

            def _send(self, status: int, payload: Any) -> None:
                raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                try:
                    self.wfile.write(raw)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    pass

            def do_POST(self) -> None:
                if self.path != "/v1/chat/completions":
                    self._send(404, {"error": "unknown relay path"})
                    return
                if not hmac.compare_digest(self.headers.get("Authorization", ""), "Bearer " + bridge.token):
                    self._send(401, {"error": "relay authentication required"})
                    return
                try:
                    size = int(self.headers.get("Content-Length", "0"))
                    if not 0 < size <= _MAX_BYTES:
                        raise ValueError("message size exceeds relay budget")
                    body = json.loads(self.rfile.read(size))
                    if not isinstance(body, dict):
                        raise ValueError("relay body must be a JSON object")
                    if body.get("model") != bridge.model or body.get("stream", False):
                        raise ValueError("only the current-session JSON channel is allowed")
                    messages = body["messages"]
                    if not isinstance(messages, list) or len(messages) != 2:
                        raise ValueError("expected one role instruction and one context")
                    system = messages[0]["content"]
                    if messages[0]["role"] != "system" or messages[1]["role"] != "user":
                        raise ValueError("unexpected role envelope")
                    role: Literal["audit_brain", "planner", "reviewer"]
                    if "You are the audit brain role." in system:
                        role = "audit_brain"
                    elif "You are the planning role." in system:
                        role = "planner"
                    elif "You are the independent reviewer role." in system:
                        role = "reviewer"
                    else:
                        raise ValueError("unknown collaboration role")
                    context = json.loads(messages[1]["content"])["untrusted_context"]
                    if not isinstance(context, dict):
                        raise ValueError("role context must be an object")
                    evidence = context.get("evidence", [])
                    if not isinstance(evidence, list) or any(not isinstance(item, dict) for item in evidence):
                        raise ValueError("evidence must be a list of objects")
                    if any(item.get("tenant_id") != str(bridge.context.tenant_id) for item in evidence):
                        raise ValueError("foreign evidence cannot enter this session relay")
                    request = SessionModelRequest(
                        request_id=uuid4(), tenant_id=bridge.context.tenant_id, trace_id=bridge.context.trace_id,
                        session_id=bridge.context.session_id, round_no=context["round_no"], role=role,
                        messages=messages, messages_sha256=fingerprint(messages),
                    )
                    exported = request.model_dump(mode="json")
                    _create(bridge.root / "requests" / f"{request.request_id}.json", exported)
                    deadline = time.monotonic() + bridge.wait_seconds
                    response_path = bridge.root / "responses" / f"{request.request_id}.json"
                    while not bridge._stopping.wait(0.05):
                        if response_path.exists():
                            response = SessionModelResponse.model_validate(_read(response_path))
                            received = response.model_dump(mode="json")
                            if any(received[key] != exported[key] for key in _BINDINGS):
                                raise ValueError("session response bindings do not match request")
                            if fingerprint(response.answer) != response.answer_sha256:
                                raise ValueError("session answer content changed")
                            content = _validated_answer(role, response.answer)
                            self._send(200, {"id": str(request.request_id), "object": "chat.completion",
                                             "model": bridge.model, "choices": [{"index": 0, "finish_reason": "stop",
                                             "message": {"role": "assistant", "content": json.dumps(content, ensure_ascii=False)}}]})
                            return
                        if time.monotonic() >= deadline:
                            break
                    self._send(504, {"error": "current session has not supplied a response; no fallback content"})
                except (KeyError, TypeError, ValueError, OSError) as exc:
                    self._send(422, {"error": f"invalid session exchange: {type(exc).__name__}"})

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self.base_url = f"http://127.0.0.1:{self._server.server_address[1]}/v1"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *args: Any) -> None:
        self._stopping.set()
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=2)
