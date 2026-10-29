"""Two explicit model modes, without outbound calls or fabricated answers."""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from time import monotonic, sleep
from uuid import uuid4

import pytest

from packages.ai.config import MANAGED_ENV, resolve_planning_mode
from packages.ai.errors import AIClientError
from packages.ai_planner.evidence import PlanningContext
from packages.ai_planner.model_access import resolve_model_access
from packages.ai_planner.session_bridge import submit_session_response


@pytest.fixture(autouse=True)
def isolated_config(monkeypatch, tmp_path):
    monkeypatch.setenv("AUDIT_NETWORK_SKIP_DOTENV", "1")
    for name in (*MANAGED_ENV, "AI_PLANNER_BACKEND", "AUDIT_COLLABORATIVE_DEBUG_SYNTHETIC"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AUDIT_COLLABORATIVE_DEBUG_ROOT", str(tmp_path))


def context():
    return PlanningContext(tenant_id=uuid4(), trace_id=uuid4(), session_id=uuid4())


def test_default_is_debug_and_modes_are_closed(monkeypatch):
    assert resolve_planning_mode() == "debug"
    monkeypatch.setenv("AI_PLANNING_MODE", "external")
    assert resolve_planning_mode() == "external"
    monkeypatch.setenv("AI_PLANNING_MODE", "auto")
    with pytest.raises(AIClientError):
        resolve_planning_mode()


def test_debug_does_not_resolve_or_use_external_credentials(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "invalid-provider")
    access = resolve_model_access()
    assert access.mode == "debug"
    assert access.snapshot(context())["model"] == "codex-current-session"


@pytest.mark.parametrize("database", ["postgresql://localhost/audit_network", "postgresql://remote.invalid/audit_network_test"])
def test_debug_requires_local_test_database(monkeypatch, database):
    monkeypatch.setenv("AUDIT_COLLABORATIVE_DEBUG_SYNTHETIC", "1")
    with pytest.raises(AIClientError, match="test database"):
        with resolve_model_access().connect(context(), database_url=database, max_seconds=2):
            pytest.fail("debug channel must remain closed")


def test_debug_requires_explicit_synthetic_declaration():
    with pytest.raises(AIClientError, match="synthetic"):
        with resolve_model_access().connect(context(), database_url="postgresql://localhost/audit_network_test", max_seconds=2):
            pytest.fail("debug channel must remain closed")


def test_debug_exports_real_http_request_and_waits_for_bound_answer(monkeypatch, tmp_path):
    monkeypatch.setenv("AUDIT_COLLABORATIVE_DEBUG_SYNTHETIC", "1")
    monkeypatch.setenv("OPENAI_COMPAT_PROXY", "http://unreachable.invalid:1")
    ctx = context()
    access = resolve_model_access()
    snapshot = access.snapshot(ctx)
    exchange = Path(snapshot["exchange_dir"])
    messages = [{"role": "system", "content": "You are the planning role."},
                {"role": "user", "content": json.dumps({"untrusted_context": {"round_no": 1, "evidence": []}})}]
    answer = {"nodes": [{"node_id": "fixture", "plugin_id": "fixture.plugin"}], "edges": [], "evidence_ids": [], "decision_evidence": {}, "rationale": "合成资料缺能力"}
    with access.connect(ctx, database_url="postgresql://localhost/audit_network_test", max_seconds=5) as client, ThreadPoolExecutor() as pool:
        pending = pool.submit(client.complete_json, messages)
        deadline = monotonic() + 3
        files = []
        while not files and monotonic() < deadline:
            files = list((exchange / "requests").glob("*.json"))
            sleep(0.02)
        assert files and not pending.done()
        request = json.loads(files[0].read_text(encoding="utf-8"))
        assert request["session_id"] == str(ctx.session_id)
        assert request["messages"] == messages
        submit_session_response(exchange, request["request_id"], answer)
        assert pending.result(timeout=3) == answer
        assert client.mode == "debug"
        assert client.transport == "manual-session-relay"


def test_external_snapshot_is_secret_free_and_configuration_bound(monkeypatch):
    monkeypatch.setenv("AI_PLANNING_MODE", "external")
    monkeypatch.setenv("AI_PROVIDER", "openai_compat")
    monkeypatch.setenv("AI_BASE_URL", "https://example.invalid/v1")
    monkeypatch.setenv("AI_MODEL", "fixture-model")
    monkeypatch.setenv("AI_API_KEY", "placeholder-private-credential")
    ctx = context()
    first = resolve_model_access().snapshot(ctx)
    assert first["mode"] == "external"
    assert first["endpoint"] == "https://example.invalid/v1"
    assert "placeholder-private" not in json.dumps(first)
    monkeypatch.setenv("AI_MODEL", "different-model")
    assert resolve_model_access().snapshot(ctx)["config_hash"] != first["config_hash"]


def test_external_failure_never_creates_debug_requests(monkeypatch, tmp_path):
    from packages.ai.gateway import UnifiedAIClient

    monkeypatch.setenv("AI_PLANNING_MODE", "external")
    monkeypatch.setenv("AI_API_KEY", "placeholder-private-credential")
    def fail(*args, **kwargs):
        raise AIClientError("fixture provider unavailable")
    monkeypatch.setattr(UnifiedAIClient, "complete_json", fail)
    with resolve_model_access().connect(context(), database_url="postgresql://localhost/audit_network_test", max_seconds=3) as client:
        with pytest.raises(AIClientError, match="unavailable"):
            client.complete_json([])
    assert not list(tmp_path.rglob("requests"))


@pytest.mark.parametrize("endpoint", ["https://user:secret@example.invalid/v1", "https://example.invalid/v1?key=secret", "http://example.invalid/v1"])
def test_external_rejects_credential_urls_and_plain_remote_http(monkeypatch, endpoint):
    monkeypatch.setenv("AI_PLANNING_MODE", "external")
    monkeypatch.setenv("AI_BASE_URL", endpoint)
    with pytest.raises(AIClientError):
        resolve_model_access()


def test_provider_error_content_cannot_enter_planning_results(monkeypatch):
    from packages.ai.gateway import UnifiedAIClient

    monkeypatch.setenv("AI_PLANNING_MODE", "external")
    monkeypatch.setenv("AI_API_KEY", "placeholder-private-credential")
    def fail(*args, **kwargs):
        raise AIClientError("provider echoed placeholder-private-credential and confidential context")
    monkeypatch.setattr(UnifiedAIClient, "complete_json", fail)
    with resolve_model_access().connect(context(), database_url="postgresql://localhost/audit_network_test", max_seconds=3) as client:
        with pytest.raises(AIClientError) as error:
            client.complete_json([])
    assert "placeholder-private" not in str(error.value)
    assert "confidential" not in str(error.value)


def test_model_transport_refuses_redirects_and_bounds_response(monkeypatch):
    from urllib.error import HTTPError
    from urllib.request import Request

    from packages.llm.openai_compat_client import OpenAICompatChat, _NoRedirect

    assert _NoRedirect().redirect_request(Request("http://127.0.0.1/v1"), None, 302, "moved", {}, "https://example.invalid") is None
    class OversizedResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self, size):
            assert size == 1_048_577
            return b"x" * size

    class Opener:
        def open(self, *args, **kwargs):
            return OversizedResponse()

    client = OpenAICompatChat(base_url="http://127.0.0.1/v1", api_key="placeholder")
    client._opener = Opener()
    with pytest.raises(AIClientError, match="byte budget"):
        client.complete_json([])
    # Redirects become an error and cannot reuse the request's Bearer header.
    class RedirectOpener:
        def open(self, *args, **kwargs):
            raise HTTPError("http://127.0.0.1/v1", 302, "moved", {}, None)
    client._opener = RedirectOpener()
    with pytest.raises(AIClientError, match="302"):
        client.complete_json([])
