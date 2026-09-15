"""CW5 扩展：第二种 AI 接入 — OpenAI 兼容远端模型适配器（方案 CW5 本地模型适配的扩展）。

契约：OpenAI 兼容 ``POST {base}/chat/completions``（Authorization: Bearer、
``response_format={"type":"json_object"}``、模型可配置），任何传输/解析/鉴权
失败都 fail-closed 抛 ``OpenAICompatChatError``，绝不静默返回假结果。

测试全部走本地回环假服务器（不触外部网络，符合 Phase 9 边界）；真实远端
调用由 opt-in 脚本 ``scripts/ai-planning-remote-smoke.py`` 执行（用户显式运行）。
"""

from __future__ import annotations

import json
import os
import re
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import pytest

from packages.ai_planner.catalog import PORT_CONTRACTS
from packages.ai_planner.evaluation import EVALUATION_CASES, catalog_for
from packages.ai_planner.planner import AiPlanner
from packages.llm.openai_compat_client import OpenAICompatChat, OpenAICompatChatError


def _registered_port(port_id: str, direction: str) -> dict[str, Any]:
    """A port contract exactly as the recall registry publishes it.

    The model is told to copy these eight fields verbatim, so a canned "correct
    answer" has to be built *from* the registry: writing them out by hand can
    only drift, and it did — the old ``"a"*64`` placeholder stopped matching the
    moment the registry started carrying each schema file's real digest, and the
    planner then correctly answered ``gap_report`` instead of ``draft_ready``.
    """
    return {"port_id": port_id, "direction": direction, **PORT_CONTRACTS[port_id]}


TEMPLATE_DRAFT = {
    "plan_key": "plan-remote-1",
    "nodes": [
        {"node_instance_id": "ledger-a", "capability": "audit.ledger.validate",
         "plugin_id": "audit.ledger-quality",
         "input_ports": [_registered_port("ledger", "input")],
         "output_ports": [_registered_port("candidates", "output")]},
        {"node_instance_id": "consumer-x", "capability": "quant.experiment.evaluate",
         "plugin_id": "quant.experiment-evaluator",
         "input_ports": [_registered_port("experiment", "input")],
         "output_ports": [_registered_port("evaluation", "output")]},
    ],
    "edges": [{"edge_id": "e1", "source_instance": "ledger-a", "source_port": "candidates",
               "target_instance": "consumer-x", "target_port": "experiment",
               "adapter": "candidates-to-backtest"}],
    "budget": {"max_chain_length": 4, "max_candidates": 6, "max_latency_ms": 300000},
    "seed_inputs": [["ledger-a", "ledger"]],
    "selection_reasons": "remote model draft",
}


class _Recorder:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []


def _make_server(handler_cls: type[BaseHTTPRequestHandler]) -> tuple[HTTPServer, threading.Thread, str]:
    server = HTTPServer(("127.0.0.1", 0), handler_cls)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    return server, thread, base


def _ok_handler(recorder: _Recorder, payload: dict[str, Any], *, delay: float = 0.0):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length).decode("utf-8"))
            recorder.requests.append({
                "path": self.path,
                "auth": self.headers.get("Authorization"),
                "body": body,
            })
            if delay:
                import time
                time.sleep(delay)
            raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *args: Any) -> None:
            return

    return Handler


def _error_handler(status: int, message: str):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            raw = json.dumps({"error": {"message": message}}, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *args: Any) -> None:
            return

    return Handler


def _chat(base: str, *, api_key: str = "test-key-123") -> OpenAICompatChat:
    return OpenAICompatChat(base_url=base, model="meituan/LongCat-2.0:free", api_key=api_key, timeout=2.0)


def _openai_payload(content: str) -> dict[str, Any]:
    return {"id": "chatcmpl-test", "choices": [{"index": 0, "message": {"role": "assistant", "content": content}}]}


def test_openai_compat_requires_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_COMPAT_API_KEY", raising=False)
    with pytest.raises(OpenAICompatChatError):
        OpenAICompatChat(base_url="http://127.0.0.1:1", model="m", api_key="")


def test_openai_compat_chat_json_via_local_server() -> None:
    recorder = _Recorder()
    server, thread, base = _make_server(_ok_handler(recorder, _openai_payload(json.dumps(TEMPLATE_DRAFT))))
    try:
        chat = _chat(base)
        data = chat.complete_json([{"role": "user", "content": "校验日记账并回测"}])
        assert data["plan_key"] == "plan-remote-1"
        assert len(data["nodes"]) == 2
        assert recorder.requests, "server must have received a request"
        req = recorder.requests[0]
        assert req["path"].endswith("/chat/completions")
        assert req["auth"] == "Bearer test-key-123"
        assert req["body"]["model"] == "meituan/LongCat-2.0:free"
        assert req["body"]["response_format"] == {"type": "json_object"}
        assert req["body"]["temperature"] == 0.0
        assert req["body"]["stream"] is False
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_openai_compat_http_error_maps_to_chat_error() -> None:
    server, thread, base = _make_server(_error_handler(401, "invalid api key"))
    try:
        with pytest.raises(OpenAICompatChatError) as exc:
            _chat(base).complete_json([{"role": "user", "content": "hi"}])
        assert "invalid api key" in str(exc.value) or "401" in str(exc.value)
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_openai_compat_timeout_raises() -> None:
    recorder = _Recorder()
    server, thread, base = _make_server(_ok_handler(recorder, _openai_payload("{}"), delay=3.0))
    try:
        with pytest.raises(OpenAICompatChatError):
            _chat(base, api_key="k").complete_json([{"role": "user", "content": "hi"}])
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_openai_compat_malformed_response_raises() -> None:
    server, thread, base = _make_server(_ok_handler(_Recorder(), {"choices": []}))
    try:
        with pytest.raises(OpenAICompatChatError):
            _chat(base).complete_json([{"role": "user", "content": "hi"}])
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_openai_compat_strips_trailing_chat_completions() -> None:
    """PA Agent 实测教训：base_url 写完整 /chat/completions 会双拼 404，
    客户端必须归一化到 provider 根路径。"""
    chat = OpenAICompatChat(
        base_url="https://api.commandcode.ai/provider/v1/chat/completions",
        model="meituan/LongCat-2.0:free",
        api_key="k",
    )
    assert chat.base_url == "https://api.commandcode.ai/provider/v1"
    chat2 = OpenAICompatChat(base_url="https://api.commandcode.ai/provider/v1/", api_key="k")
    assert chat2.base_url == "https://api.commandcode.ai/provider/v1"


def test_openai_compat_max_tokens_clamped_for_longcat() -> None:
    chat = OpenAICompatChat(
        base_url="http://127.0.0.1:1", model="meituan/LongCat-2.0:free", api_key="k",
        max_tokens=524288,
    )
    assert chat.max_tokens == 131072  # free-tier ceiling, avoids the 400
    other = OpenAICompatChat(
        base_url="http://127.0.0.1:1", model="some-other-model", api_key="k", max_tokens=524288,
    )
    assert other.max_tokens == 524288  # only clamp the known LongCat ceiling


def test_openai_compat_proxy_configures_opener() -> None:
    """Proxy is wired through ProxyHandler; actual routing honors the system
    bypass table at runtime (verified in the opt-in smoke script)."""
    from urllib.request import ProxyHandler

    chat = OpenAICompatChat(
        base_url="http://127.0.0.1:1", api_key="k", proxy="http://127.0.0.1:7890",
    )
    assert chat.proxy == "http://127.0.0.1:7890"
    handlers = [h for h in chat._opener.handlers if isinstance(h, ProxyHandler)]
    assert handlers, "proxy must be wired through a ProxyHandler"
    assert handlers[0].proxies["http"] == "http://127.0.0.1:7890"
    assert handlers[0].proxies["https"] == "http://127.0.0.1:7890"
    # Without an explicit proxy we intentionally preserve urllib's standard
    # environment proxy behavior.  ``build_opener()`` may still install a
    # ProxyHandler when the test host defines HTTP(S)_PROXY, so the stable
    # contract is the absence of an explicit client override, not its class.
    plain = OpenAICompatChat(base_url="http://127.0.0.1:1", api_key="k")
    assert plain.proxy is None


def test_openai_compat_reasoning_effort_and_max_tokens_passthrough() -> None:
    recorder = _Recorder()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length).decode("utf-8"))
            recorder.requests.append({"body": body})
            raw = json.dumps(_openai_payload("{}")).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *args: Any) -> None:
            return

    server, thread, base = _make_server(Handler)
    try:
        chat = OpenAICompatChat(
            base_url=base, api_key="k", max_tokens=131072, reasoning_effort="medium",
        )
        chat.complete_json([{"role": "user", "content": "hi"}])
        assert recorder.requests
        body = recorder.requests[0]["body"]
        assert body["max_tokens"] == 131072
        assert body["reasoning_effort"] == "medium"
        assert body["response_format"] == {"type": "json_object"}
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_openai_compat_sends_browser_user_agent() -> None:
    """Cloudflare 实测（PA Agent 切换记录）：Python-urllib UA 被 1010 拦截，
    浏览器 UA 放行。适配器默认必须带浏览器 UA，且可 env 覆盖。"""
    recorder = _Recorder()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            recorder.requests.append({"ua": self.headers.get("User-Agent")})
            raw = json.dumps(_openai_payload("{}")).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *args: Any) -> None:
            return

    server, thread, base = _make_server(Handler)
    try:
        chat = OpenAICompatChat(base_url=base, api_key="k")
        chat.complete_json([{"role": "user", "content": "hi"}])
        ua = recorder.requests[0]["ua"]
        assert ua and ("Mozilla" in ua or "Chrome" in ua), "browser UA required to pass Cloudflare"
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_ai_planner_with_openai_compat_backend() -> None:
    """The remote backend plugs into the same AiPlanner gates as local Ollama."""
    recorder = _Recorder()
    server, thread, base = _make_server(_ok_handler(recorder, _openai_payload(json.dumps(TEMPLATE_DRAFT))))
    try:
        chat = _chat(base)
        planner = AiPlanner(chat)
        case = EVALUATION_CASES[0]  # s1-ledger-backtest
        outcome = planner.plan(
            goal=case.goal,
            catalog=catalog_for(case),
            authorized_sources=set(case.authorized_sources),
        )
        assert outcome.status == "draft_ready"
        assert outcome.execution_plan is not None
        assert outcome.revisions >= 0
        assert recorder.requests, "planner must have called the remote backend"
    finally:
        server.shutdown()
        thread.join(timeout=5)


# Credential-shaped literals that must never be committed.  These are regex
# *sources*, deliberately built from character classes rather than a real key:
# an earlier revision of this test embedded the live API key here to assert it
# was absent elsewhere, which is itself the leak.  Needles are derived at run
# time instead.
_CREDENTIAL_PATTERNS = (
    re.compile(r"user_[A-Za-z0-9]{40,}"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
)
_SCANNED_SUFFIXES = frozenset({".py", ".json", ".md", ".ps1", ".ts", ".tsx", ".yml", ".yaml"})
_SCANNED_ROOTS = ("packages", "apps", "scripts", "tests", "contracts", "plugins", "desktop")
_SKIPPED_DIRS = frozenset(
    {".venv", "node_modules", "out", ".data", "cankao", "__pycache__", ".git", "dist", "build"}
)


def _scan_credential_leaks(root: Path, extra_needle: str | None) -> list[str]:
    """Return ``path:line`` for every line embedding a credential literal."""
    hits: list[str] = []
    for sub in _SCANNED_ROOTS:
        base = root / sub
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in _SCANNED_SUFFIXES:
                continue
            if _SKIPPED_DIRS & set(path.parts):
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for index, line in enumerate(text.splitlines(), start=1):
                # A regex source only lists the character classes, so it cannot
                # match itself; genuine literals do match.
                if any(pattern.search(line) for pattern in _CREDENTIAL_PATTERNS):
                    hits.append(f"{path.relative_to(root)}:{index}")
                elif extra_needle and extra_needle in line:
                    hits.append(f"{path.relative_to(root)}:{index}")
    return hits


def test_no_hardcoded_credentials_in_source() -> None:
    """No source file may embed an API key — including the live one.

    The live ``OPENAI_COMPAT_API_KEY`` is used as an additional needle when it
    is set, so a re-committed key is caught even if its shape differs from the
    generic patterns.
    """
    root = Path(__file__).resolve().parents[2]
    live_key = os.getenv("OPENAI_COMPAT_API_KEY", "").strip() or None
    hits = _scan_credential_leaks(root, live_key)
    assert hits == [], f"credential literal(s) found in source: {hits}"

    # the adapter builds the Authorization header from the key at call time
    adapter_text = (root / "packages" / "llm" / "openai_compat_client.py").read_text(encoding="utf-8")
    assert "Authorization" in adapter_text and "Bearer" in adapter_text
    assert "os.getenv(\"OPENAI_COMPAT_API_KEY\"" in adapter_text or 'getenv("AI_API_KEY"' in adapter_text
    # the smoke script only reads the key from the environment
    smoke = (root / "scripts" / "ai-planning-remote-smoke.py").read_text(encoding="utf-8")
    assert "OPENAI_COMPAT_API_KEY" in smoke or "is_available" in smoke
