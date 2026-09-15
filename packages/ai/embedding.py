"""Unified embedding transport.

One adapter that speaks both wire formats the project uses:

* ``ollama``        — ``POST {base_url}/api/embed`` (the local default);
* ``openai_compat`` — ``POST {base_url}/embeddings`` (any OpenAI-compatible
  provider, e.g. a cloud gateway).

Both paths enforce the same strict contract as the legacy
:class:`~packages.knowledge.retrieval.OllamaEmbedder`: the batch length must
match the input, every vector must carry exactly the configured dimension, and
every component must be finite.  Failures raise :class:`AIClientError` (a
``RuntimeError``), so callers written against the old behaviour are unaffected.
"""

from __future__ import annotations

import json
import math
import os
from collections.abc import Callable
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .config import (
    PROVIDER_OLLAMA,
    PROVIDER_OPENAI_COMPAT,
    EmbeddingProviderConfig,
    resolve_embedding_config,
)
from .errors import AIClientError

#: Browser UA default: some gateways (Cloudflare-fronted) reject Python-urllib
#: outright with HTTP 1010, matching the chat client's documented workaround.
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

UrlOpener = Callable[..., Any]


class Embedder(Protocol):
    model: str

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class UnifiedEmbedder:
    """Embedding client selected by :class:`EmbeddingProviderConfig`."""

    def __init__(
        self,
        *,
        config: EmbeddingProviderConfig | None = None,
        model: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout: float | None = None,
        dimensions: int | None = None,
        provider: str | None = None,
        user_agent: str | None = None,
        opener: UrlOpener = urlopen,
    ) -> None:
        resolved = config or resolve_embedding_config()
        self.provider = provider or resolved.provider
        self.model = model or resolved.model
        self.base_url = (base_url or resolved.base_url).rstrip("/")
        self.timeout = resolved.timeout if timeout is None else timeout
        self.dimensions = resolved.dimensions if dimensions is None else dimensions
        self.api_key = api_key if api_key is not None else os.getenv("AI_EMBED_API_KEY", "").strip()
        self.user_agent = (
            user_agent or os.getenv("AI_USER_AGENT", "").strip() or DEFAULT_USER_AGENT
        )
        self._opener = opener

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts or any(not text.strip() for text in texts):
            raise ValueError("embedding input must contain non-empty text")
        if self.provider == PROVIDER_OLLAMA:
            raw_vectors = self._embed_ollama(texts)
        elif self.provider == PROVIDER_OPENAI_COMPAT:
            raw_vectors = self._embed_openai_compat(texts)
        else:  # pragma: no cover - config validation rejects this earlier
            raise AIClientError(f"unsupported embedding provider {self.provider!r}")
        if not isinstance(raw_vectors, list) or len(raw_vectors) != len(texts):
            raise ValueError(
                f"{self.provider} returned an invalid embedding batch: "
                f"expected {len(texts)} vectors, got {len(raw_vectors) if isinstance(raw_vectors, list) else 'none'}"
            )
        vectors: list[list[float]] = []
        for raw in raw_vectors:
            if not isinstance(raw, list) or len(raw) != self.dimensions:
                raise ValueError(f"embedding must contain exactly {self.dimensions} values")
            vector = [float(value) for value in raw]
            if any(not math.isfinite(value) for value in vector):
                raise ValueError("embedding contains a non-finite value")
            vectors.append(vector)
        return vectors

    def _post(self, path: str, payload: dict[str, Any], *, auth: bool) -> Any:
        headers = {"Content-Type": "application/json; charset=utf-8", "User-Agent": self.user_agent}
        if auth:
            if not self.api_key:
                raise AIClientError(
                    "embedding API key is not set; refusing to call a remote embedder without credentials"
                )
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = Request(
            f"{self.base_url}{path}",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with self._opener(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            raise AIClientError(f"{self.provider} embedding unavailable: {exc}") from exc

    def _embed_ollama(self, texts: list[str]) -> Any:
        body = self._post("/api/embed", {"model": self.model, "input": texts}, auth=False)
        raw = body.get("embeddings") if isinstance(body, dict) else None
        if raw is None:
            raise AIClientError(f"{self.provider} embedding unavailable: response carried no vectors")
        return raw

    def _embed_openai_compat(self, texts: list[str]) -> Any:
        body = self._post("/embeddings", {"model": self.model, "input": texts}, auth=True)
        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(data, list):
            raise AIClientError(
                f"{self.provider} embedding unavailable: response carried no data array"
            )
        # OpenAI-compatible endpoints are only ordered by the ``index`` field;
        # relying on array order would silently misalign vectors with inputs.
        def by_index(item: dict[Any, Any]) -> int:
            value = item.get("index")
            return value if isinstance(value, int) else 0

        ordered = sorted((item for item in data if isinstance(item, dict)), key=by_index)
        return [item.get("embedding") for item in ordered]


def default_embedder() -> Embedder:
    """The embedder used when a caller does not inject one."""
    return UnifiedEmbedder()
