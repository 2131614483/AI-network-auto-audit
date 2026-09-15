"""Integration tests for the unified AI settings API.

Policy-gated, tenant-scoped, idempotent, and — critically — never touching the
real ``.env``: every write test redirects ``ai_env_store.env_file_path`` at a
temporary path.  The 0058 baseline policy set already ALLOWs the three AI CAPs
for local-dev, so positive cases rely on it; the fail-closed case deactivates
that set and restores it in ``finally`` so no other suite inherits the change.

The connectivity probe is always exercised against a patched transport: a test
must never make a real outbound model call.
"""

from __future__ import annotations

import os
from pathlib import Path
from uuid import UUID

import psycopg2
import pytest
from fastapi.testclient import TestClient

from apps.api.main import Settings, create_app
from packages.ai import AIClientError
from packages.ai import env_store as ai_env_store

DB = os.getenv(
    "AUDIT_NETWORK_TEST_DATABASE_URL",
    "postgresql://audit_app:admin@localhost:5432/audit_network_test",
)
SETTINGS = "/api/v1/ai/settings"
TEST = "/api/v1/ai/test"
BASELINE_SET = "local-plugin-topology-read"


def _tenant(connection) -> UUID:
    with connection.cursor() as cur:
        cur.execute("SELECT id FROM iam.tenants WHERE slug='local-dev'")
        tenant_id = UUID(str(cur.fetchone()[0]))
        cur.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant_id),))
        cur.fetchone()
    return tenant_id


def _set_baseline(connection, tenant_id: UUID, active: bool) -> None:
    status = "active" if active else "inactive"
    with connection.cursor() as cur:
        cur.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant_id),))
        cur.fetchone()
        cur.execute(
            "UPDATE policy.policy_sets SET status=%s WHERE tenant_id=%s AND name=%s",
            (status, tenant_id, BASELINE_SET),
        )
    connection.commit()


@pytest.fixture()
def client() -> TestClient:
    return TestClient(create_app(Settings(database_url=DB)))


@pytest.fixture()
def isolated_env_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect every settings write at a temp file, never the project ``.env``."""
    target = tmp_path / ".env"
    monkeypatch.setattr(ai_env_store, "env_file_path", lambda: target)
    return target


def test_settings_read_is_masked(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """The key is reported as a boolean plus a tail — never in clear text."""
    # Deliberately not credential-shaped: the source scanner in
    # test_cw5_second_llm_adapter.py flags anything matching a real key's shape,
    # and a synthetic fixture has no business looking like one.
    fake_key = "placeholder-not-a-real-key-1234"
    monkeypatch.setenv("AI_API_KEY", fake_key)
    with psycopg2.connect(DB) as connection:
        tenant_id = _tenant(connection)
    response = client.get(SETTINGS, headers={"X-Tenant-Id": str(tenant_id)})
    assert response.status_code == 200
    body = response.json()
    assert body["chat"]["api_key_set"] is True
    assert body["chat"]["api_key_masked"] == "********1234"
    assert fake_key not in response.text
    assert set(body["supported_chat_providers"]) == {"openai_compat", "ollama"}
    assert "AI_MODEL" in body["managed_keys"]


def test_settings_read_requires_tenant_header(client: TestClient) -> None:
    assert client.get(SETTINGS).status_code == 422


def test_settings_read_is_fail_closed_without_the_capability(client: TestClient) -> None:
    """Deactivating the baseline set denies the read, then restores it."""
    with psycopg2.connect(DB) as connection:
        tenant_id = _tenant(connection)
        _set_baseline(connection, tenant_id, active=False)
    try:
        response = client.get(SETTINGS, headers={"X-Tenant-Id": str(tenant_id)})
        # No allow rule -> approval_required -> 409 (fail-closed), never a silent pass.
        assert response.status_code == 409
        assert response.json()["detail"]["message"] == "policy gateway blocked capability"
    finally:
        with psycopg2.connect(DB) as connection:
            _set_baseline(connection, _tenant(connection), active=True)


def test_settings_write_persists_and_applies_immediately(
    client: TestClient,
    isolated_env_file: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Saving writes ``.env`` and takes effect without a restart (hot reload)."""
    isolated_env_file.write_text(
        "# operator comment\nDATABASE_URL=postgresql://keep-me\n", encoding="utf-8"
    )
    for key in ("AI_MODEL", "AI_BASE_URL", "AI_TIMEOUT", "AI_API_KEY"):
        monkeypatch.setenv(key, "placeholder-before-save")

    with psycopg2.connect(DB) as connection:
        tenant_id = _tenant(connection)
    response = client.put(
        SETTINGS,
        headers={"X-Tenant-Id": str(tenant_id), "Idempotency-Key": "ai-settings-write-1"},
        json={
            "provider": "openai_compat",
            "base_url": "https://gateway.example.invalid/v1",
            "model": "integration-model",
            "api_key": "placeholder-written-by-test-RQ4X",
            "timeout": 45,
        },
    )
    assert response.status_code == 200
    body = response.json()
    # The response reflects the values just saved, proving they are live.
    assert body["chat"]["model"] == "integration-model"
    assert body["chat"]["base_url"] == "https://gateway.example.invalid/v1"
    assert body["chat"]["timeout"] == 45
    assert body["chat"]["api_key_masked"] == "********RQ4X"
    assert "placeholder-written-by-test-RQ4X" not in response.text

    # Unrelated user content survives; only managed keys were touched.
    text = isolated_env_file.read_text(encoding="utf-8")
    assert "# operator comment" in text
    assert "DATABASE_URL=postgresql://keep-me" in text
    assert "AI_MODEL=integration-model" in text
    assert "AI_TIMEOUT=45" in text  # rendered without a trailing .0
    assert (isolated_env_file.parent / ".env.bak").exists()

    # Hot reload: the running process sees the new value on the very next resolve.
    assert os.environ["AI_MODEL"] == "integration-model"
    again = client.get(SETTINGS, headers={"X-Tenant-Id": str(tenant_id)})
    assert again.json()["chat"]["model"] == "integration-model"


def test_settings_write_requires_idempotency_key(
    client: TestClient, isolated_env_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AI_MODEL", "placeholder")
    with psycopg2.connect(DB) as connection:
        tenant_id = _tenant(connection)
    response = client.put(
        SETTINGS,
        headers={"X-Tenant-Id": str(tenant_id)},
        json={"model": "x"},
    )
    assert response.status_code == 422
    assert not isolated_env_file.exists()


def test_settings_write_rejects_an_unknown_provider(
    client: TestClient, isolated_env_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bad provider is rejected before anything reaches ``.env``."""
    monkeypatch.setenv("AI_PROVIDER", "openai_compat")
    with psycopg2.connect(DB) as connection:
        tenant_id = _tenant(connection)
    response = client.put(
        SETTINGS,
        headers={"X-Tenant-Id": str(tenant_id), "Idempotency-Key": "ai-settings-bad-provider"},
        json={"provider": "definitely-not-real"},
    )
    assert response.status_code == 422
    assert "must be one of" in response.json()["detail"]
    assert not isolated_env_file.exists()


def test_settings_write_clears_a_field_with_an_empty_string(
    client: TestClient, isolated_env_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An empty string means "clear": resolution falls back to the default."""
    monkeypatch.setenv("AI_MODEL", "was-here")
    # Also drop the legacy aliases so the assertion does not depend on whatever
    # the developer's shell happens to export.
    monkeypatch.delenv("OPENAI_COMPAT_MODEL", raising=False)
    monkeypatch.delenv("OLLAMA_CHAT_MODEL", raising=False)
    monkeypatch.delenv("AI_PLANNER_BACKEND", raising=False)
    isolated_env_file.write_text("AI_MODEL=was-here\n", encoding="utf-8")
    with psycopg2.connect(DB) as connection:
        tenant_id = _tenant(connection)
    response = client.put(
        SETTINGS,
        headers={"X-Tenant-Id": str(tenant_id), "Idempotency-Key": "ai-settings-clear"},
        json={"model": ""},
    )
    assert response.status_code == 200
    assert "AI_MODEL" not in isolated_env_file.read_text(encoding="utf-8")
    assert os.environ.get("AI_MODEL") is None
    assert response.json()["chat"]["model"] == "meituan/LongCat-2.0:free"


def test_probe_reports_a_broken_channel_without_raising(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed probe is a renderable diagnostic, not an HTTP error."""

    class Broken:
        def __init__(self, **kwargs: object) -> None:
            pass

        def complete_json(
            self, messages: list[dict[str, str]], *, temperature: float = 0.0
        ) -> dict[str, object]:
            raise AIClientError("channel down")

        def complete(self, messages: list[dict[str, str]], *, temperature: float = 0.0) -> str:
            raise AIClientError("channel down")

    monkeypatch.setattr("packages.llm.openai_compat_client.OpenAICompatChat", Broken)
    monkeypatch.setenv("AI_PROVIDER", "openai_compat")
    with psycopg2.connect(DB) as connection:
        tenant_id = _tenant(connection)
    response = client.post(
        TEST,
        headers={"X-Tenant-Id": str(tenant_id)},
        json={"include_embedding": False},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert "channel down" in body["detail"]
    assert body["latency_ms"] is None


def test_probe_reports_success_and_can_include_embedding(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Fine:
        def __init__(self, **kwargs: object) -> None:
            pass

        def complete_json(
            self, messages: list[dict[str, str]], *, temperature: float = 0.0
        ) -> dict[str, object]:
            return {"ok": True}

        def complete(self, messages: list[dict[str, str]], *, temperature: float = 0.0) -> str:
            return "ok"

    class FakeEmbedder:
        model = "fake-embed"

        def embed(self, texts: list[str]) -> list[list[float]]:
            return [[0.0] * 1024 for _ in texts]

    monkeypatch.setattr("packages.llm.openai_compat_client.OpenAICompatChat", Fine)
    monkeypatch.setattr("packages.ai.gateway.UnifiedEmbedder", FakeEmbedder)
    monkeypatch.setenv("AI_PROVIDER", "openai_compat")
    with psycopg2.connect(DB) as connection:
        tenant_id = _tenant(connection)
    response = client.post(
        TEST,
        headers={"X-Tenant-Id": str(tenant_id)},
        json={"include_embedding": True},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["detail"] == "ok"
    assert isinstance(body["latency_ms"], int)
    assert body["embedding_ok"] is True
    assert body["embedding_model"] == "fake-embed"


def test_probe_requires_tenant_header(client: TestClient) -> None:
    assert client.post(TEST, json={}).status_code == 422
