"""Unit tests for the unified AI access layer (``packages.ai``).

Covers the four things that would silently break the "one gateway" promise:

* configuration resolution order (``AI_*`` -> legacy -> default) and the
  provider-specific legacy aliases;
* transport routing in :class:`~packages.ai.gateway.UnifiedAIClient`;
* the unified fault base class the old call sites rely on;
* embedding validation and the OpenAI-compatible ``index`` ordering;
* ``.env`` read/modify/write safety (user content preserved, no injection,
  no partial write).

No database, no network: every transport is injected or patched.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.error import URLError

import pytest

from packages.ai import AIClientError, AIConfigurationError
from packages.ai.config import (
    MANAGED_ENV,
    PROVIDER_OLLAMA,
    PROVIDER_OPENAI_COMPAT,
    describe_chat_config,
    mask_secret,
    resolve_chat_config,
    resolve_embedding_config,
)
from packages.ai.embedding import UnifiedEmbedder
from packages.ai.env_store import (
    EnvWriteError,
    load_env_file,
    parse_env_text,
    update_env_file,
)
from packages.ai.gateway import UnifiedAIClient, probe
from packages.llm.ollama_client import OllamaChatError
from packages.llm.openai_compat_client import OpenAICompatChatError

_LEGACY_VARS = (
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
_UNIFIED_VARS = (
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
    "AI_EMBED_PROVIDER",
    "AI_EMBED_BASE_URL",
    "AI_EMBED_MODEL",
    "AI_EMBED_TIMEOUT",
    "AI_EMBED_DIMENSIONS",
    "AI_EMBED_API_KEY",
)


@pytest.fixture()
def clean_ai_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """Isolate a test from whatever the developer's shell has exported."""
    for name in _LEGACY_VARS + _UNIFIED_VARS:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


class _Response:
    def __init__(self, payload: object) -> None:
        self.payload = payload

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode()


# --- configuration resolution ------------------------------------------------


def test_defaults_apply_when_nothing_is_configured(clean_ai_env: pytest.MonkeyPatch) -> None:
    config = resolve_chat_config()
    assert config.provider == PROVIDER_OPENAI_COMPAT
    assert config.base_url == "https://api.commandcode.ai/provider/v1"
    assert config.model == "meituan/LongCat-2.0:free"
    assert config.timeout == 180.0
    assert config.api_key is None
    assert config.sources["model"] == "default"


def test_unified_variables_win_over_legacy_names(clean_ai_env: pytest.MonkeyPatch) -> None:
    clean_ai_env.setenv("AI_MODEL", "unified-model")
    clean_ai_env.setenv("OPENAI_COMPAT_MODEL", "legacy-model")
    config = resolve_chat_config()
    assert config.model == "unified-model"
    assert config.sources["model"] == "AI_MODEL"


def test_legacy_names_still_resolve_when_unified_absent(clean_ai_env: pytest.MonkeyPatch) -> None:
    """The pre-existing .env must keep working without edits."""
    clean_ai_env.setenv("OPENAI_COMPAT_MODEL", "legacy-model")
    clean_ai_env.setenv("OPENAI_COMPAT_TIMEOUT", "600")
    config = resolve_chat_config()
    assert config.model == "legacy-model"
    assert config.timeout == 600.0
    assert config.sources["timeout"] == "OPENAI_COMPAT_TIMEOUT"


def test_ollama_url_does_not_retarget_the_cloud_channel(clean_ai_env: pytest.MonkeyPatch) -> None:
    """A stray OLLAMA_URL must not silently redirect the OpenAI-compatible channel."""
    clean_ai_env.setenv("OLLAMA_URL", "http://127.0.0.1:11434")
    assert resolve_chat_config().base_url == "https://api.commandcode.ai/provider/v1"

    clean_ai_env.setenv("AI_PROVIDER", "ollama")
    assert resolve_chat_config().base_url == "http://127.0.0.1:11434"


def test_legacy_planner_backend_selector_still_works(clean_ai_env: pytest.MonkeyPatch) -> None:
    clean_ai_env.setenv("AI_PLANNER_BACKEND", "ollama")
    config = resolve_chat_config()
    assert config.provider == PROVIDER_OLLAMA
    assert config.sources["provider"] == "AI_PLANNER_BACKEND"
    assert config.num_ctx == 65536


def test_unknown_provider_is_a_configuration_error(clean_ai_env: pytest.MonkeyPatch) -> None:
    clean_ai_env.setenv("AI_PROVIDER", "not-a-provider")
    with pytest.raises(AIConfigurationError, match="not a supported provider"):
        resolve_chat_config()


def test_invalid_numeric_values_fail_closed(clean_ai_env: pytest.MonkeyPatch) -> None:
    clean_ai_env.setenv("AI_TIMEOUT", "0")
    with pytest.raises(AIConfigurationError, match="must be positive"):
        resolve_chat_config()
    clean_ai_env.setenv("AI_TIMEOUT", "soon")
    with pytest.raises(AIConfigurationError, match="must be a number"):
        resolve_chat_config()


def test_embedding_defaults_to_the_local_model(clean_ai_env: pytest.MonkeyPatch) -> None:
    config = resolve_embedding_config()
    assert config.provider == PROVIDER_OLLAMA
    assert config.model == "qwen3-embedding:0.6b"
    assert config.base_url == "http://127.0.0.1:11434"
    assert config.dimensions == 1024


def test_embedding_can_be_pointed_at_an_openai_compatible_channel(
    clean_ai_env: pytest.MonkeyPatch,
) -> None:
    clean_ai_env.setenv("AI_EMBED_PROVIDER", "openai_compat")
    clean_ai_env.setenv("AI_EMBED_BASE_URL", "https://example.invalid/v1/")
    clean_ai_env.setenv("AI_EMBED_MODEL", "embed-large")
    clean_ai_env.setenv("AI_EMBED_DIMENSIONS", "1536")
    config = resolve_embedding_config()
    assert config.base_url == "https://example.invalid/v1"
    assert config.model == "embed-large"
    assert config.dimensions == 1536


def test_describe_chat_config_never_exposes_the_key(clean_ai_env: pytest.MonkeyPatch) -> None:
    clean_ai_env.setenv("AI_API_KEY", "sk-super-secret-value")
    described = describe_chat_config()
    assert described["api_key_set"] is True
    assert described["api_key_masked"] == "********alue"
    assert "sk-super-secret-value" not in json.dumps(described)


def test_mask_secret_handles_empty_and_short_values() -> None:
    assert mask_secret(None) is None
    assert mask_secret("") is None
    assert mask_secret("ab") == "**"
    assert mask_secret("abcdefgh") == "********efgh"


# --- fault taxonomy ----------------------------------------------------------


def test_legacy_error_types_share_the_unified_base() -> None:
    """Old call sites keep working; new ones can catch a single type."""
    assert issubclass(OpenAICompatChatError, AIClientError)
    assert issubclass(OllamaChatError, AIClientError)
    assert isinstance(OpenAICompatChatError("x"), RuntimeError)


# --- transport routing -------------------------------------------------------


def test_gateway_routes_to_the_openai_compatible_transport(
    clean_ai_env: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeOpenAI:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

        def complete_json(self, messages: list[dict[str, str]], *, temperature: float = 0.0) -> dict[str, object]:
            captured["messages"] = messages
            return {"ok": True}

        def complete(self, messages: list[dict[str, str]], *, temperature: float = 0.0) -> str:
            return "plain text"

    clean_ai_env.setenv("AI_BASE_URL", "https://api.example.invalid/v1")
    clean_ai_env.setenv("AI_MODEL", "chat-model")
    clean_ai_env.setenv("AI_API_KEY", "k")
    clean_ai_env.setenv("AI_TIMEOUT", "42")
    clean_ai_env.setattr("packages.llm.openai_compat_client.OpenAICompatChat", FakeOpenAI)

    client = UnifiedAIClient()
    assert client.complete_json([{"role": "user", "content": "hi"}]) == {"ok": True}
    assert captured["base_url"] == "https://api.example.invalid/v1"
    assert captured["model"] == "chat-model"
    assert captured["timeout"] == 42.0
    assert client.complete([{"role": "user", "content": "hi"}]) == "plain text"


def test_gateway_routes_to_the_ollama_transport(clean_ai_env: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class FakeOllama:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

        def complete_json(self, messages: list[dict[str, str]], *, temperature: float = 0.0) -> dict[str, object]:
            return {"ok": True}

        def complete(self, messages: list[dict[str, str]], *, temperature: float = 0.0) -> str:
            return "local"

    clean_ai_env.setenv("AI_PROVIDER", "ollama")
    clean_ai_env.setenv("AI_MODEL", "local-model")
    clean_ai_env.setattr("packages.llm.ollama_client.OllamaChat", FakeOllama)

    assert UnifiedAIClient().complete_json([{"role": "user", "content": "hi"}]) == {"ok": True}
    assert captured["model"] == "local-model"
    assert captured["num_ctx"] == 65536


def test_probe_reports_failure_instead_of_raising(clean_ai_env: pytest.MonkeyPatch) -> None:
    """A probe is a diagnostic: the UI must be able to render the reason."""

    class Broken:
        def __init__(self, **kwargs: object) -> None:
            pass

        def complete_json(self, messages: list[dict[str, str]], *, temperature: float = 0.0) -> dict[str, object]:
            raise OpenAICompatChatError("backend down")

    clean_ai_env.setenv("AI_API_KEY", "k")
    clean_ai_env.setattr("packages.llm.openai_compat_client.OpenAICompatChat", Broken)

    result = probe()
    assert result.ok is False
    assert "backend down" in result.detail
    assert result.latency_ms is None


def test_probe_reports_success_with_latency(clean_ai_env: pytest.MonkeyPatch) -> None:
    class Fine:
        def __init__(self, **kwargs: object) -> None:
            pass

        def complete_json(self, messages: list[dict[str, str]], *, temperature: float = 0.0) -> dict[str, object]:
            return {"ok": True}

    clean_ai_env.setenv("AI_API_KEY", "k")
    clean_ai_env.setattr("packages.llm.openai_compat_client.OpenAICompatChat", Fine)

    result = probe()
    assert result.ok is True
    assert result.detail == "ok"
    assert isinstance(result.latency_ms, int)


# --- embedding ---------------------------------------------------------------


def test_ollama_embedder_validates_dimension(clean_ai_env: pytest.MonkeyPatch) -> None:
    embedder = UnifiedEmbedder(opener=lambda *_a, **_k: _Response({"embeddings": [[0.5] * 1024]}))
    assert len(embedder.embed(["审计证据"])[0]) == 1024

    bad = UnifiedEmbedder(opener=lambda *_a, **_k: _Response({"embeddings": [[0.5] * 3]}))
    with pytest.raises(ValueError, match="1024"):
        bad.embed(["错误维度"])


def test_embedding_failure_is_an_explicit_client_error(clean_ai_env: pytest.MonkeyPatch) -> None:
    def unavailable(*_a: object, **_k: object) -> _Response:
        raise URLError("offline")

    with pytest.raises(AIClientError, match="embedding unavailable"):
        UnifiedEmbedder(opener=unavailable).embed(["离线"])


def test_embedding_rejects_a_short_batch(clean_ai_env: pytest.MonkeyPatch) -> None:
    embedder = UnifiedEmbedder(opener=lambda *_a, **_k: _Response({"embeddings": [[0.5] * 1024]}))
    with pytest.raises(ValueError, match="invalid embedding batch"):
        embedder.embed(["一", "二"])


def test_openai_compatible_embedding_orders_by_index(clean_ai_env: pytest.MonkeyPatch) -> None:
    """Array order is not guaranteed; ``index`` is the contract."""
    payload = {
        "data": [
            {"index": 1, "embedding": [2.0] * 1024},
            {"index": 0, "embedding": [1.0] * 1024},
        ]
    }
    clean_ai_env.setenv("AI_EMBED_PROVIDER", "openai_compat")
    clean_ai_env.setenv("AI_EMBED_API_KEY", "k")
    embedder = UnifiedEmbedder(opener=lambda *_a, **_k: _Response(payload))
    vectors = embedder.embed(["第一段", "第二段"])
    assert vectors[0][0] == 1.0
    assert vectors[1][0] == 2.0


def test_openai_compatible_embedding_requires_credentials(clean_ai_env: pytest.MonkeyPatch) -> None:
    clean_ai_env.setenv("AI_EMBED_PROVIDER", "openai_compat")
    embedder = UnifiedEmbedder(opener=lambda *_a, **_k: _Response({"data": []}))
    with pytest.raises(AIClientError, match="API key is not set"):
        embedder.embed(["x"])


def test_embedding_rejects_non_finite_values(clean_ai_env: pytest.MonkeyPatch) -> None:
    vector = [0.5] * 1023 + [float("inf")]
    embedder = UnifiedEmbedder(opener=lambda *_a, **_k: _Response({"embeddings": [vector]}))
    with pytest.raises(ValueError, match="non-finite"):
        embedder.embed(["x"])


# --- .env memory -------------------------------------------------------------


def test_env_parse_handles_quotes_comments_and_exports() -> None:
    parsed = parse_env_text(
        "\n".join(
            [
                "# a comment",
                "DATABASE_URL=postgresql://u:p@localhost:5432/db",
                'AI_MODEL="model with spaces"',
                "export AI_PROVIDER=ollama",
                "AI_TIMEOUT=600  # trailing note",
                "NOT_AN_ASSIGNMENT",
            ]
        )
    )
    assert parsed["DATABASE_URL"] == "postgresql://u:p@localhost:5432/db"
    assert parsed["AI_MODEL"] == "model with spaces"
    assert parsed["AI_PROVIDER"] == "ollama"
    assert parsed["AI_TIMEOUT"] == "600"
    assert "NOT_AN_ASSIGNMENT" not in parsed


def test_update_env_file_preserves_unrelated_lines(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text(
        "# keep this comment\nDATABASE_URL=postgresql://x\n\nAI_MODEL=old-model\nOTHER=1\n",
        encoding="utf-8",
    )
    update_env_file({"AI_MODEL": "new-model"}, path=env, allowed_keys=MANAGED_ENV)
    text = env.read_text(encoding="utf-8")
    assert "# keep this comment" in text
    assert "DATABASE_URL=postgresql://x" in text
    assert "OTHER=1" in text
    assert "AI_MODEL=new-model" in text
    assert "AI_MODEL=old-model" not in text
    # A snapshot is taken before every overwrite.
    assert (tmp_path / ".env.bak").read_text(encoding="utf-8") == (
        "# keep this comment\nDATABASE_URL=postgresql://x\n\nAI_MODEL=old-model\nOTHER=1\n"
    )


def test_update_env_file_creates_and_removes_keys(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("DATABASE_URL=postgresql://x\nAI_MODEL=old\n", encoding="utf-8")

    update_env_file(
        {"AI_API_KEY": "secret-key", "AI_MODEL": ""}, path=env, allowed_keys=MANAGED_ENV
    )
    parsed = parse_env_text(env.read_text(encoding="utf-8"))
    assert parsed["AI_API_KEY"] == "secret-key"
    assert "AI_MODEL" not in parsed
    assert parsed["DATABASE_URL"] == "postgresql://x"


def test_update_env_file_quotes_values_that_need_it(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    update_env_file({"AI_MODEL": "model with spaces"}, path=env, allowed_keys=MANAGED_ENV)
    assert parse_env_text(env.read_text(encoding="utf-8"))["AI_MODEL"] == "model with spaces"


def test_update_env_file_rejects_newline_injection(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    with pytest.raises(EnvWriteError, match="newline"):
        update_env_file(
            {"AI_MODEL": "ok\nDATABASE_URL=postgresql://attacker"},
            path=env,
            allowed_keys=MANAGED_ENV,
        )
    assert not env.exists()


def test_update_env_file_refuses_unmanaged_keys(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("DATABASE_URL=postgresql://x\n", encoding="utf-8")
    with pytest.raises(EnvWriteError, match="not a managed AI setting"):
        update_env_file({"DATABASE_URL": "postgresql://attacker"}, path=env, allowed_keys=MANAGED_ENV)
    assert env.read_text(encoding="utf-8") == "DATABASE_URL=postgresql://x\n"


def test_update_env_file_rejects_invalid_key_names(tmp_path: Path) -> None:
    with pytest.raises(EnvWriteError, match="invalid environment key"):
        update_env_file({"BAD KEY": "x"}, path=tmp_path / ".env")


def test_load_env_file_does_not_override_exported_variables(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # conftest opts the whole suite out of .env usage; this test exercises the
    # loader itself, so it opts back in.
    monkeypatch.delenv("AUDIT_NETWORK_SKIP_DOTENV", raising=False)
    env = tmp_path / ".env"
    env.write_text("AI_MODEL=from-file\nAI_TIMEOUT=99\n", encoding="utf-8")
    monkeypatch.setenv("AI_MODEL", "from-process")
    monkeypatch.delenv("AI_TIMEOUT", raising=False)

    applied = load_env_file(env)
    assert applied == ["AI_TIMEOUT"]
    assert os.environ["AI_MODEL"] == "from-process"
    assert os.environ["AI_TIMEOUT"] == "99"
    monkeypatch.delenv("AI_TIMEOUT", raising=False)


def test_skip_dotenv_makes_the_process_env_authoritative(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A developer's .env must never change what a test observes."""
    env = tmp_path / ".env"
    env.write_text("AI_MODEL=from-file\n", encoding="utf-8")
    monkeypatch.delenv("AI_MODEL", raising=False)
    monkeypatch.setenv("AUDIT_NETWORK_SKIP_DOTENV", "1")

    assert load_env_file(env) == []
    assert os.environ.get("AI_MODEL") is None


def test_load_env_file_is_a_noop_when_absent(tmp_path: Path) -> None:
    assert load_env_file(tmp_path / "missing.env") == []
