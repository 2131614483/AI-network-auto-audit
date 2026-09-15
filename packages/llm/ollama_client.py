"""Local-only Ollama chat client (CW2 full: real AI inference wired into the
audit pipeline, 64K context, fail-closed).

Model defaults to a local Qwen3 variant; the client forces ``num_ctx=65536``
(64K context, the model's native window) so long audit material stays in
window.  Every call is deterministic-ish (temperature 0), bounded by a
timeout, and raises :class:`OllamaChatError` on any transport / protocol
failure — a missing or broken local model must be visible, never silently
faked.
"""

from __future__ import annotations

import json
import os
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from packages.ai.errors import AIClientError

DEFAULT_MODEL = os.getenv("OLLAMA_CHAT_MODEL") or "qwen3_27b_iq3xxs_64k:latest"
DEFAULT_BASE_URL = os.getenv("OLLAMA_URL") or "http://127.0.0.1:11434"
# 64K context by default (the local Qwen3 27B variant natively supports it);
# raise/lower via OLLAMA_CHAT_NUM_CTX without touching code.
DEFAULT_NUM_CTX = int(os.getenv("OLLAMA_CHAT_NUM_CTX", "65536"))
DEFAULT_TIMEOUT = float(os.getenv("OLLAMA_CHAT_TIMEOUT", "180"))


class OllamaChatError(AIClientError):
    """The local model is unreachable or returned an unusable response.

    Derives from :class:`~packages.ai.errors.AIClientError` so callers can catch
    the unified type; the historical name stays valid for existing call sites.
    """


def _is_available() -> bool:
    """Cheap reachability probe: the model endpoint answers within 2 s."""
    try:
        request = Request(f"{DEFAULT_BASE_URL.rstrip('/')}/api/tags", method="GET")
        with urlopen(request, timeout=2.0) as response:
            body = json.loads(response.read().decode("utf-8"))
        return isinstance(body, dict) and isinstance(body.get("models"), list)
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError):
        return False


class OllamaChat:
    """Minimal local chat adapter (``/api/chat``, non-streaming)."""

    def __init__(
        self,
        *,
        model: str | None = None,
        base_url: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        num_ctx: int = DEFAULT_NUM_CTX,
    ) -> None:
        self.model = model or DEFAULT_MODEL
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.timeout = timeout
        self.num_ctx = int(num_ctx)

    def complete(self, messages: list[dict[str, str]], *, json_mode: bool = False,
                 temperature: float = 0.0) -> str:
        """One non-streaming completion; ``json_mode`` forces the JSON format."""
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "options": {
                "num_ctx": self.num_ctx,
                "temperature": temperature,
            },
        }
        if json_mode:
            payload["format"] = "json"
        request = Request(
            f"{self.base_url}/api/chat",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            raise OllamaChatError(f"ollama chat unavailable: {exc}") from exc
        if not isinstance(body, dict):
            raise OllamaChatError("ollama returned a non-object response")
        message = body.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str) or not content.strip():
            raise OllamaChatError("ollama returned an empty completion")
        return content

    def complete_json(self, messages: list[dict[str, str]], *, temperature: float = 0.0) -> dict[str, Any]:
        """JSON-forced completion with strict parse + shape validation."""
        raw = self.complete(messages, json_mode=True, temperature=temperature)
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise OllamaChatError("ollama JSON completion did not parse") from exc
        if not isinstance(parsed, dict):
            raise OllamaChatError("ollama JSON completion is not an object")
        return parsed
