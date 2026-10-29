"""Explicit debug relay or configured AI API, frozen for a planning session."""
from __future__ import annotations

import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from psycopg2.extensions import parse_dsn

from packages.ai.config import ChatProviderConfig, resolve_chat_config, resolve_planning_mode
from packages.ai.errors import AIClientError, AIConfigurationError
from packages.ai.gateway import UnifiedAIClient
from packages.llm.openai_compat_client import OpenAICompatChat
from packages.paths import data_root

from .evidence import PlanningContext, fingerprint
from .session_bridge import SessionModelBridge

_LOOPBACK = {"localhost", "127.0.0.1", "::1"}


class _PlanningClient:
    def __init__(self, access: PlanningModelAccess, *, max_seconds: float, bridge: SessionModelBridge | None = None) -> None:
        self.mode = access.mode
        self.model = access.model
        self.transport = access.transport
        self.endpoint = access.endpoint
        self.config_hash = access.config_hash
        self._access = access
        self._bridge = bridge
        self._deadline = time.monotonic() + max_seconds

    def complete_json(self, messages: list[dict[str, str]], *, temperature: float = 0.0) -> dict[str, Any]:
        remaining = self._deadline - time.monotonic()
        if remaining <= 0:
            raise AIClientError("model access time budget exhausted")
        if self._bridge is not None:
            # The debug token and evidence must never use machine/cloud proxies.
            client = OpenAICompatChat(base_url=self._bridge.base_url, model=self.model, api_key=self._bridge.token,
                                      timeout=min(remaining, self._bridge.wait_seconds + 1), direct=True)
            return client.complete_json(messages, temperature=temperature)
        config = self._access.config
        if config is None:
            raise AIConfigurationError("external model configuration unavailable")
        try:
            return UnifiedAIClient(config=replace(config, timeout=min(config.timeout, remaining))).complete_json(
                messages, temperature=temperature,
            )
        except AIClientError as exc:
            # A provider error body can echo credentials or submitted context.
            # Keep it out of durable planning events; diagnostics use ai.test.
            raise AIClientError("configured AI API unavailable; test the saved API connection for details") from exc


@dataclass(frozen=True)
class PlanningModelAccess:
    mode: str
    provider: str
    model: str
    endpoint: str
    transport: str
    config_hash: str
    config: ChatProviderConfig | None = None
    debug_root: Path | None = None
    injected: Any = None

    def snapshot(self, context: PlanningContext) -> dict[str, Any]:
        result: dict[str, Any] = {"mode": self.mode, "provider": self.provider, "model": self.model,
            "endpoint": self.endpoint, "transport": self.transport, "config_hash": self.config_hash}
        if self.debug_root is not None:
            result["exchange_dir"] = str(self.debug_root / str(context.tenant_id) / str(context.session_id))
        return result

    @contextmanager
    def connect(self, context: PlanningContext, *, database_url: str, max_seconds: float) -> Iterator[Any]:
        if self.injected is not None:
            yield self.injected
            return
        if self.mode == "debug":
            if os.getenv("AUDIT_COLLABORATIVE_DEBUG_SYNTHETIC") != "1":
                raise AIConfigurationError("debug relay requires an explicit synthetic data declaration")
            database = parse_dsn(database_url)
            name = database.get("dbname", "")
            if database.get("host") not in _LOOPBACK or not (name.endswith("_test") or name.startswith("test_")):
                raise AIConfigurationError("debug relay requires a local isolated test database")
            root = Path(self.snapshot(context)["exchange_dir"])
            with SessionModelBridge(root, context, wait_seconds=min(120, max_seconds)) as bridge:
                yield _PlanningClient(self, max_seconds=max_seconds, bridge=bridge)
        else:
            yield _PlanningClient(self, max_seconds=max_seconds)


def resolve_model_access(injected: Any = None) -> PlanningModelAccess:
    """Resolve without I/O; credentials stay solely in the in-memory config."""
    if injected is not None:
        model = getattr(injected, "model", type(injected).__name__)
        digest = fingerprint({"mode": "injected", "model": model, "class": type(injected).__name__})
        return PlanningModelAccess("injected", "test-injection", model, "injected", "injected", digest, injected=injected)
    mode = resolve_planning_mode()
    if mode == "debug":
        root = Path(os.getenv("AUDIT_COLLABORATIVE_DEBUG_ROOT", str(data_root() / "ai-session-debug"))).resolve()
        digest = fingerprint({"mode": mode, "root": str(root), "synthetic": os.getenv("AUDIT_COLLABORATIVE_DEBUG_SYNTHETIC") == "1"})
        return PlanningModelAccess(mode, "current-session", "codex-current-session", "loopback://current-session",
                                   "manual-session-relay", digest, debug_root=root)
    config = resolve_chat_config()
    url = urlsplit(config.base_url)
    if (url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password
            or url.query or url.fragment or (url.scheme == "http" and url.hostname not in _LOOPBACK)):
        raise AIConfigurationError("model endpoint requires HTTPS or loopback HTTP, without credentials/query/fragment")
    if config.provider == "openai_compat" and not config.api_key:
        raise AIConfigurationError("external OpenAI-compatible API key is not configured")
    # Keep API keys and their hashes out of persistence. Credential rotation is
    # allowed; changing provider, endpoint, model or generation settings is not.
    safe = {key: value for key, value in asdict(config).items() if key not in {"api_key", "sources"}}
    digest = fingerprint({"mode": mode, "configuration": safe})
    return PlanningModelAccess(mode, config.provider, config.model, config.base_url, "configured-ai-api", digest, config=config)
