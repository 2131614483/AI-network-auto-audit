"""Unified AI provider configuration — the single source of truth.

One resolution order, first hit wins, so an existing ``.env`` keeps working
untouched::

    AI_*                            unified names (written by the AI settings page)
    -> OPENAI_COMPAT_* / OLLAMA_*   the two legacy per-client namespaces
    -> built-in default

Values are read at call time rather than import time.  That is deliberate: the
desktop settings page writes ``.env`` and updates ``os.environ`` in the running
process, and the very next model call must pick the new value up without a
restart.  Reading ten environment variables per call is free compared with the
network round trip that follows.

Secrets never leave this module in clear text: :func:`describe_chat_config`
returns ``api_key_set`` plus a masked tail, never the key itself.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from .env_store import load_env_file_once
from .errors import AIConfigurationError

PROVIDER_OPENAI_COMPAT = "openai_compat"
PROVIDER_OLLAMA = "ollama"
SUPPORTED_CHAT_PROVIDERS: tuple[str, ...] = (PROVIDER_OPENAI_COMPAT, PROVIDER_OLLAMA)
SUPPORTED_EMBED_PROVIDERS: tuple[str, ...] = (PROVIDER_OPENAI_COMPAT, PROVIDER_OLLAMA)

DEFAULT_CHAT_PROVIDER = PROVIDER_OPENAI_COMPAT
DEFAULT_EMBED_PROVIDER = PROVIDER_OLLAMA

DEFAULT_OPENAI_COMPAT_BASE_URL = "https://api.commandcode.ai/provider/v1"
DEFAULT_OPENAI_COMPAT_MODEL = "meituan/LongCat-2.0:free"
DEFAULT_OLLAMA_BASE_URL = "http://127.0.0.1:11434"
DEFAULT_OLLAMA_CHAT_MODEL = "qwen3_27b_iq3xxs_64k:latest"
DEFAULT_OLLAMA_NUM_CTX = 65536
DEFAULT_EMBED_MODEL = "qwen3-embedding:0.6b"
DEFAULT_CHAT_TIMEOUT = 180.0
DEFAULT_EMBED_TIMEOUT = 120.0
DEFAULT_EMBED_DIMENSIONS = 1024

#: Env vars the AI settings page owns.  Only these may ever be written to
#: ``.env`` by the API — every other line in the file is user-owned.
MANAGED_CHAT_ENV: tuple[str, ...] = (
    "AI_PROVIDER",
    "AI_BASE_URL",
    "AI_MODEL",
    "AI_API_KEY",
    "AI_TIMEOUT",
    "AI_MAX_TOKENS",
    "AI_REASONING_EFFORT",
    "AI_PROXY",
    "AI_USER_AGENT",
    "AI_NUM_CTX",
)
MANAGED_EMBED_ENV: tuple[str, ...] = (
    "AI_EMBED_PROVIDER",
    "AI_EMBED_BASE_URL",
    "AI_EMBED_MODEL",
    "AI_EMBED_TIMEOUT",
    "AI_EMBED_DIMENSIONS",
)
MANAGED_ENV: tuple[str, ...] = MANAGED_CHAT_ENV + MANAGED_EMBED_ENV

#: Legacy variables still honoured as fallbacks.  Listed so the settings page
#: can tell the operator *why* a field shows a value they never typed there.
LEGACY_ENV: tuple[str, ...] = (
    "AI_PLANNER_BACKEND",
    "OPENAI_COMPAT_BASE_URL",
    "OPENAI_COMPAT_MODEL",
    "OPENAI_COMPAT_API_KEY",
    "OPENAI_COMPAT_TIMEOUT",
    "OPENAI_COMPAT_MAX_TOKENS",
    "OPENAI_COMPAT_REASONING_EFFORT",
    "OPENAI_COMPAT_PROXY",
    "OPENAI_COMPAT_USER_AGENT",
    "OLLAMA_URL",
    "OLLAMA_CHAT_MODEL",
    "OLLAMA_CHAT_TIMEOUT",
    "OLLAMA_CHAT_NUM_CTX",
    "OLLAMA_EMBED_MODEL",
    "EMBEDDING_DIMENSION",
)


@dataclass(frozen=True, slots=True)
class ChatProviderConfig:
    """Resolved chat/planning provider, including where each value came from."""

    provider: str
    base_url: str
    model: str
    api_key: str | None
    timeout: float
    max_tokens: int | None
    reasoning_effort: str | None
    proxy: str | None
    user_agent: str | None
    num_ctx: int
    sources: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class EmbeddingProviderConfig:
    """Resolved embedding provider."""

    provider: str
    base_url: str
    model: str
    timeout: float
    dimensions: int
    sources: dict[str, str] = field(default_factory=dict)


def mask_secret(value: str | None) -> str | None:
    """Return a display-safe form of a secret: never the secret itself.

    ``None`` for "not configured".  A short secret is fully masked rather than
    revealing most of it through a long tail.
    """
    if not value:
        return None
    if len(value) <= 4:
        return "*" * len(value)
    return f"{'*' * 8}{value[-4:]}"


def _lookup(aliases: tuple[str, ...]) -> tuple[str | None, str]:
    """First non-empty alias wins; returns ``(value, env_name)``."""
    for name in aliases:
        raw = os.getenv(name)
        if raw is not None and raw.strip():
            return raw.strip(), name
    return None, "default"


def _positive_float(value: str, env_name: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise AIConfigurationError(f"{env_name} must be a number, got {value!r}") from exc
    if parsed <= 0:
        raise AIConfigurationError(f"{env_name} must be positive, got {parsed}")
    return parsed


def _positive_int(value: str, env_name: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise AIConfigurationError(f"{env_name} must be an integer, got {value!r}") from exc
    if parsed <= 0:
        raise AIConfigurationError(f"{env_name} must be positive, got {parsed}")
    return parsed


def _normalise_provider(raw: str, env_name: str, supported: tuple[str, ...]) -> str:
    provider = raw.strip().lower()
    if provider not in supported:
        raise AIConfigurationError(
            f"{env_name}={raw!r} is not a supported provider; expected one of {', '.join(supported)}"
        )
    return provider


def resolve_chat_provider() -> str:
    # Apply the project .env as defaults so *every* entry point (API, scripts,
    # background workers) sees the same configured channel.  Idempotent; a
    # no-op when AUDIT_NETWORK_SKIP_DOTENV is set.
    load_env_file_once()
    """Cheap provider probe that never raises on a missing key."""
    raw, env_name = _lookup(("AI_PROVIDER", "AI_PLANNER_BACKEND"))
    if raw is None:
        return DEFAULT_CHAT_PROVIDER
    return _normalise_provider(raw, env_name, SUPPORTED_CHAT_PROVIDERS)


def resolve_chat_config() -> ChatProviderConfig:
    # Apply the project .env as defaults so *every* entry point (API, scripts,
    # background workers) sees the same configured channel.  Idempotent; a
    # no-op when AUDIT_NETWORK_SKIP_DOTENV is set.
    load_env_file_once()
    """Resolve the chat/planning model channel.

    Provider-specific legacy aliases are only consulted for the matching
    provider: an ``OLLAMA_URL`` in the environment must not silently retarget
    the OpenAI-compatible channel, which is what reading it unconditionally
    would do.
    """
    provider_raw, provider_src = _lookup(("AI_PROVIDER", "AI_PLANNER_BACKEND"))
    provider = (
        DEFAULT_CHAT_PROVIDER
        if provider_raw is None
        else _normalise_provider(provider_raw, provider_src, SUPPORTED_CHAT_PROVIDERS)
    )

    if provider == PROVIDER_OLLAMA:
        base_aliases = ("AI_BASE_URL", "OLLAMA_URL")
        model_aliases = ("AI_MODEL", "OLLAMA_CHAT_MODEL")
        timeout_aliases = ("AI_TIMEOUT", "OLLAMA_CHAT_TIMEOUT")
        default_base_url = DEFAULT_OLLAMA_BASE_URL
        default_model = DEFAULT_OLLAMA_CHAT_MODEL
    else:
        base_aliases = ("AI_BASE_URL", "OPENAI_COMPAT_BASE_URL")
        model_aliases = ("AI_MODEL", "OPENAI_COMPAT_MODEL")
        timeout_aliases = ("AI_TIMEOUT", "OPENAI_COMPAT_TIMEOUT")
        default_base_url = DEFAULT_OPENAI_COMPAT_BASE_URL
        default_model = DEFAULT_OPENAI_COMPAT_MODEL

    base_url, base_src = _lookup(base_aliases)
    model, model_src = _lookup(model_aliases)
    timeout_raw, timeout_src = _lookup(timeout_aliases)
    max_tokens_raw, max_tokens_src = _lookup(("AI_MAX_TOKENS", "OPENAI_COMPAT_MAX_TOKENS"))
    effort_raw, effort_src = _lookup(("AI_REASONING_EFFORT", "OPENAI_COMPAT_REASONING_EFFORT"))
    proxy_raw, proxy_src = _lookup(("AI_PROXY", "OPENAI_COMPAT_PROXY"))
    ua_raw, ua_src = _lookup(("AI_USER_AGENT", "OPENAI_COMPAT_USER_AGENT"))
    num_ctx_raw, num_ctx_src = _lookup(("AI_NUM_CTX", "OLLAMA_CHAT_NUM_CTX"))
    key_raw, key_src = _lookup(("AI_API_KEY", "OPENAI_COMPAT_API_KEY"))

    return ChatProviderConfig(
        provider=provider,
        base_url=(base_url or default_base_url).rstrip("/"),
        model=model or default_model,
        api_key=key_raw,
        timeout=(
            DEFAULT_CHAT_TIMEOUT
            if timeout_raw is None
            else _positive_float(timeout_raw, timeout_src)
        ),
        max_tokens=(
            None if max_tokens_raw is None else _positive_int(max_tokens_raw, max_tokens_src)
        ),
        reasoning_effort=effort_raw,
        proxy=proxy_raw,
        user_agent=ua_raw,
        num_ctx=(
            DEFAULT_OLLAMA_NUM_CTX
            if num_ctx_raw is None
            else _positive_int(num_ctx_raw, num_ctx_src)
        ),
        sources={
            "provider": provider_src,
            "base_url": base_src,
            "model": model_src,
            "timeout": timeout_src,
            "max_tokens": max_tokens_src,
            "reasoning_effort": effort_src,
            "proxy": proxy_src,
            "user_agent": ua_src,
            "num_ctx": num_ctx_src,
            "api_key": key_src,
        },
    )


def resolve_embedding_config() -> EmbeddingProviderConfig:
    # Apply the project .env as defaults so *every* entry point (API, scripts,
    # background workers) sees the same configured channel.  Idempotent; a
    # no-op when AUDIT_NETWORK_SKIP_DOTENV is set.
    load_env_file_once()
    """Resolve the embedding channel (defaults to the local Ollama model)."""
    provider_raw, provider_src = _lookup(("AI_EMBED_PROVIDER",))
    provider = (
        DEFAULT_EMBED_PROVIDER
        if provider_raw is None
        else _normalise_provider(provider_raw, provider_src, SUPPORTED_EMBED_PROVIDERS)
    )
    base_url, base_src = _lookup(("AI_EMBED_BASE_URL", "OLLAMA_URL"))
    model, model_src = _lookup(("AI_EMBED_MODEL", "OLLAMA_EMBED_MODEL"))
    timeout_raw, timeout_src = _lookup(("AI_EMBED_TIMEOUT",))
    dims_raw, dims_src = _lookup(("AI_EMBED_DIMENSIONS", "EMBEDDING_DIMENSION"))
    return EmbeddingProviderConfig(
        provider=provider,
        base_url=(base_url or DEFAULT_OLLAMA_BASE_URL).rstrip("/"),
        model=model or DEFAULT_EMBED_MODEL,
        timeout=(
            DEFAULT_EMBED_TIMEOUT
            if timeout_raw is None
            else _positive_float(timeout_raw, timeout_src)
        ),
        dimensions=(
            DEFAULT_EMBED_DIMENSIONS
            if dims_raw is None
            else _positive_int(dims_raw, dims_src)
        ),
        sources={
            "provider": provider_src,
            "base_url": base_src,
            "model": model_src,
            "timeout": timeout_src,
            "dimensions": dims_src,
        },
    )


def describe_chat_config(config: ChatProviderConfig | None = None) -> dict[str, Any]:
    """Settings-page view of the chat channel — masked key, no secret."""
    resolved = config or resolve_chat_config()
    return {
        "provider": resolved.provider,
        "base_url": resolved.base_url,
        "model": resolved.model,
        "timeout": resolved.timeout,
        "max_tokens": resolved.max_tokens,
        "reasoning_effort": resolved.reasoning_effort,
        "proxy": resolved.proxy,
        "num_ctx": resolved.num_ctx,
        "api_key_set": bool(resolved.api_key),
        "api_key_masked": mask_secret(resolved.api_key),
        "sources": dict(resolved.sources),
    }


def describe_embedding_config(config: EmbeddingProviderConfig | None = None) -> dict[str, Any]:
    """Settings-page view of the embedding channel."""
    resolved = config or resolve_embedding_config()
    return {
        "provider": resolved.provider,
        "base_url": resolved.base_url,
        "model": resolved.model,
        "timeout": resolved.timeout,
        "dimensions": resolved.dimensions,
        "sources": dict(resolved.sources),
    }
