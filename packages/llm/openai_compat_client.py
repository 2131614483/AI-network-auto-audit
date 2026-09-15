"""CW5 扩展：第二种 AI 接入 — OpenAI 兼容远端模型适配器。

本地 Ollama（``ollama_client.py``）之外的第二条模型通道：任何 OpenAI 兼容的
``POST {base_url}/chat/completions`` 端点（如 commandcode.ai provider 网关）。
协议细节与本地通道一致：JSON 模式（``response_format={"type":"json_object"}``）、
temperature 0、有界超时、fail-closed——传输/解析/鉴权失败都抛
:class:`OpenAICompatChatError`，绝不静默返回假结果（24x7 自动运行的红线）。

默认端点/模型来自方案外用户指定（``https://api.commandcode.ai/provider/v1`` +
``meituan/LongCat-2.0:free``）；API key 只从环境变量读取，**禁止硬编码**。
本通道不改变 AI 组网的执行边界：它只是 AiPlanner 的可注入 DraftLLM，
草稿仍须通过同一套能力召回/数据边界/编译闸门后才产生可执行计划。
"""

from __future__ import annotations

import json
import os
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

from packages.ai.errors import AIClientError

DEFAULT_BASE_URL = os.getenv("OPENAI_COMPAT_BASE_URL") or "https://api.commandcode.ai/provider/v1"
DEFAULT_MODEL = os.getenv("OPENAI_COMPAT_MODEL") or "meituan/LongCat-2.0:free"
DEFAULT_TIMEOUT = float(os.getenv("OPENAI_COMPAT_TIMEOUT", "180"))
# 用户实测（PA Agent 切换记录）：LongCat-2.0:free 免费档 max_tokens 上限 131072，
# 高于此值会被 400 拒绝。本适配器默认不发送 max_tokens（模型取默认），
# 显式设置时按此上限收敛，避免无谓 400。
DEFAULT_MAX_TOKENS = int(os.getenv("OPENAI_COMPAT_MAX_TOKENS", "0")) or None
DEFAULT_REASONING_EFFORT = os.getenv("OPENAI_COMPAT_REASONING_EFFORT", "").strip() or None
LONGCAT_FREE_MAX_TOKENS = 131072
# 实测（PA Agent 切换记录 + 本机复测）：api.commandcode.ai 对 Python-urllib UA
# 返回 Cloudflare 1010；浏览器 UA 放行。默认必须带浏览器 UA（可 env 覆盖）。
DEFAULT_USER_AGENT = os.getenv("OPENAI_COMPAT_USER_AGENT") or (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


class OpenAICompatChatError(AIClientError):
    """The remote backend is unreachable, unauthenticated or unusable.

    Derives from :class:`~packages.ai.errors.AIClientError` so callers can catch
    the unified type; the historical name stays valid for existing call sites.
    """


def _resolve_api_key(explicit: str | None) -> str:
    if explicit:
        return explicit
    key = os.getenv("OPENAI_COMPAT_API_KEY", "").strip()
    if not key:
        raise OpenAICompatChatError(
            "OPENAI_COMPAT_API_KEY is not set; refusing to call a remote model without credentials"
        )
    return key


class OpenAICompatChat:
    """Minimal OpenAI-compatible chat adapter (``/chat/completions``, non-streaming).

    ``base_url`` must stop at the provider root (e.g. ``https://api.commandcode.ai/provider/v1``);
    the client appends ``/chat/completions`` — a full path would double-prefix and 404.
    Proxy: ``proxy`` / ``OPENAI_COMPAT_PROXY`` (e.g. ``http://127.0.0.1:7890``) forces an
    explicit proxy; without it the standard environment proxies are respected.
    """

    def __init__(
        self,
        *,
        base_url: str | None = None,
        model: str | None = None,
        api_key: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        max_tokens: int | None = DEFAULT_MAX_TOKENS,
        reasoning_effort: str | None = DEFAULT_REASONING_EFFORT,
        proxy: str | None = None,
        user_agent: str | None = None,
    ) -> None:
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        if self.base_url.endswith("/chat/completions"):
            self.base_url = self.base_url[: -len("/chat/completions")]
        self.model = model or DEFAULT_MODEL
        self.api_key = _resolve_api_key(api_key)
        self.timeout = float(timeout)
        if self.timeout <= 0:
            raise OpenAICompatChatError(f"timeout must be positive, got {self.timeout}")
        self.max_tokens = max_tokens
        if self.max_tokens is not None:
            if self.max_tokens <= 0:
                raise OpenAICompatChatError(f"max_tokens must be positive, got {self.max_tokens}")
            if "LongCat" in self.model and self.max_tokens > LONGCAT_FREE_MAX_TOKENS:
                self.max_tokens = LONGCAT_FREE_MAX_TOKENS
        self.reasoning_effort = reasoning_effort
        self.user_agent = user_agent or DEFAULT_USER_AGENT
        self.proxy = proxy or os.getenv("OPENAI_COMPAT_PROXY", "").strip() or None
        self._opener = (
            build_opener(ProxyHandler({"http": self.proxy, "https": self.proxy}))
            if self.proxy
            else build_opener()
        )

    def _post(self, payload: dict[str, Any]) -> str:
        """One non-streaming POST; returns the assistant message content."""
        request = Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "Authorization": f"Bearer {self.api_key}",
                "User-Agent": self.user_agent,
            },
            method="POST",
        )
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", errors="replace")[:300]
            except OSError:
                pass
            raise OpenAICompatChatError(
                f"openai-compatible backend HTTP {exc.code}: {detail or exc.reason}"
            ) from exc
        except (URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            raise OpenAICompatChatError(f"openai-compatible backend unavailable: {exc}") from exc
        if not isinstance(body, dict):
            raise OpenAICompatChatError("openai-compatible backend returned a non-object response")
        choices = body.get("choices")
        if not isinstance(choices, list) or not choices:
            raise OpenAICompatChatError("openai-compatible backend returned no choices")
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str) or not content.strip():
            raise OpenAICompatChatError("openai-compatible backend returned an empty completion")
        return content

    def _payload(
        self, messages: list[dict[str, str]], temperature: float, *, json_mode: bool
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "temperature": temperature,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        if self.max_tokens is not None:
            payload["max_tokens"] = self.max_tokens
        if self.reasoning_effort is not None:
            payload["reasoning_effort"] = self.reasoning_effort
        return payload

    def complete(self, messages: list[dict[str, str]], *, temperature: float = 0.0) -> str:
        """One free-text completion (no ``response_format`` constraint)."""
        return self._post(self._payload(messages, temperature, json_mode=False))

    def complete_json(self, messages: list[dict[str, str]], *, temperature: float = 0.0) -> dict[str, Any]:
        """One JSON-forced completion; strict parse + shape validation."""
        content = self._post(self._payload(messages, temperature, json_mode=True))
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            raise OpenAICompatChatError("openai-compatible JSON completion did not parse") from exc
        if not isinstance(parsed, dict):
            raise OpenAICompatChatError("openai-compatible JSON completion is not an object")
        return parsed


def is_available() -> bool:
    """Credentials configured only — real reachability is verified at call time
    (no external probe from automated runs, per Phase 9 boundaries)."""
    try:
        _resolve_api_key(None)
        return True
    except OpenAICompatChatError:
        return False
