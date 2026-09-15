"""The one AI gateway every model call in this project goes through.

:class:`UnifiedAIClient` resolves :mod:`packages.ai.config` at **call time** and
delegates to the transport for the selected provider, so:

* changing provider/model/key in the AI settings page takes effect on the next
  call — no process restart;
* chat, planning and embedding share one configuration surface;
* every failure is an :class:`~packages.ai.errors.AIClientError`, replacing the
  ``except (OpenAICompatChatError, OllamaChatError, OSError)`` triples that
  used to be repeated at each call site.

The gateway adds no execution authority: it is a transport.  A draft produced
through it still has to clear the same capability recall, data-boundary and
compiler gates as before.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from .config import PROVIDER_OLLAMA, ChatProviderConfig, resolve_chat_config
from .embedding import Embedder, UnifiedEmbedder
from .errors import AIClientError

#: Deliberately tiny: the probe must fail fast on a broken channel.
PROBE_MESSAGE: list[dict[str, str]] = [
    {"role": "system", "content": "You reply with JSON only."},
    {"role": "user", "content": 'Reply exactly {"ok": true}'},
]


@dataclass(frozen=True, slots=True)
class AIProbeResult:
    """Outcome of a settings-page connectivity probe (never raises)."""

    ok: bool
    provider: str
    model: str
    base_url: str
    detail: str
    latency_ms: int | None = None
    embedding_ok: bool | None = None
    embedding_detail: str | None = None
    embedding_model: str | None = None


class UnifiedAIClient:
    """Provider-agnostic chat client selected by configuration.

    Construction is cheap and never raises: the transport is built per call so
    the current configuration always wins.  A misconfigured channel therefore
    surfaces as an :class:`AIClientError` from the call itself, which is where
    callers already handle backend failure (fail-closed, never a faked answer).
    """

    def __init__(self, config: ChatProviderConfig | None = None, *, embedder: Embedder | None = None) -> None:
        self._config = config
        self._embedder = embedder

    @property
    def config(self) -> ChatProviderConfig:
        return self._config or resolve_chat_config()

    def _transport(self) -> Any:
        """Build the underlying client for the currently configured provider."""
        config = self.config
        if config.provider == PROVIDER_OLLAMA:
            from packages.llm.ollama_client import OllamaChat

            return OllamaChat(
                model=config.model,
                base_url=config.base_url,
                timeout=config.timeout,
                num_ctx=config.num_ctx,
            )
        from packages.llm.openai_compat_client import OpenAICompatChat

        return OpenAICompatChat(
            base_url=config.base_url,
            model=config.model,
            api_key=config.api_key,
            timeout=config.timeout,
            max_tokens=config.max_tokens,
            reasoning_effort=config.reasoning_effort,
            proxy=config.proxy,
            user_agent=config.user_agent,
        )

    def complete(self, messages: list[dict[str, str]], *, temperature: float = 0.0) -> str:
        """One free-text completion."""
        return str(self._transport().complete(messages, temperature=temperature))

    def complete_json(
        self, messages: list[dict[str, str]], *, temperature: float = 0.0
    ) -> dict[str, Any]:
        """One JSON-forced completion (the ``DraftLLM`` contract)."""
        return dict(self._transport().complete_json(messages, temperature=temperature))

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embeddings through the same configuration surface."""
        embedder = self._embedder or UnifiedEmbedder()
        return embedder.embed(texts)


def get_chat_client(config: ChatProviderConfig | None = None) -> UnifiedAIClient:
    """Factory used by call sites that previously hand-built a client."""
    return UnifiedAIClient(config)


def probe(
    config: ChatProviderConfig | None = None,
    *,
    include_embedding: bool = False,
) -> AIProbeResult:
    """Round-trip the configured channel for the settings page.

    Returns a structured result instead of raising so the UI can render the
    failure reason; a probe is a diagnostic, not a business call.
    """
    resolved = config or resolve_chat_config()
    client = UnifiedAIClient(resolved)
    started = time.monotonic()
    try:
        client.complete_json(PROBE_MESSAGE, temperature=0.0)
    except (AIClientError, OSError, ValueError) as exc:
        return AIProbeResult(
            ok=False,
            provider=resolved.provider,
            model=resolved.model,
            base_url=resolved.base_url,
            detail=str(exc),
        )
    latency_ms = int((time.monotonic() - started) * 1000)

    embedding_ok: bool | None = None
    embedding_detail: str | None = None
    embedding_model: str | None = None
    if include_embedding:
        embedder = UnifiedEmbedder()
        embedding_model = embedder.model
        try:
            embedder.embed(["probe"])
            embedding_ok = True
        except (AIClientError, OSError, ValueError) as exc:
            embedding_ok = False
            embedding_detail = str(exc)

    return AIProbeResult(
        ok=True,
        provider=resolved.provider,
        model=resolved.model,
        base_url=resolved.base_url,
        detail="ok",
        latency_ms=latency_ms,
        embedding_ok=embedding_ok,
        embedding_detail=embedding_detail,
        embedding_model=embedding_model,
    )


__all__ = [
    "AIProbeResult",
    "UnifiedAIClient",
    "get_chat_client",
    "probe",
]
