from __future__ import annotations

from uuid import UUID

from fastapi.testclient import TestClient

from apps.api.main import Settings, _safe_upload_relative, create_app


def _client() -> TestClient:
    return TestClient(create_app(Settings(database_url="postgresql://configured")))


def test_health_is_live_without_opening_database() -> None:
    client = _client()
    response = client.get("/health", headers={"X-Trace-Id": "00000000-0000-0000-0000-000000000001"})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["database_configured"] is True
    assert body["trace_id"] == "00000000-0000-0000-0000-000000000001"
    assert response.headers["x-trace-id"] == body["trace_id"]


def test_invalid_trace_id_is_replaced_with_uuid() -> None:
    response = _client().get("/health", headers={"X-Trace-Id": "not-a-uuid"})
    assert response.status_code == 200
    UUID(response.json()["trace_id"])


def test_ui_bootstrap_is_framework_independent_and_policy_aware() -> None:
    response = _client().get("/api/v1/ui/bootstrap")
    assert response.status_code == 200
    body = response.json()
    assert "data-table" in {item["id"] for item in body["renderers"]}
    assert "workspace.navigation" in body["slots"]
    assert body["features"]["policy_gated_actions"] is True


def test_ui_contributions_returns_safe_empty_page_until_catalog_repository_exists() -> None:
    response = _client().get(
        "/api/v1/ui/contributions?workspace_id=00000000-0000-0000-0000-000000000002"
    )
    assert response.status_code == 200
    body = response.json()
    assert body["workspace_id"] == "00000000-0000-0000-0000-000000000002"
    assert body["items"] == []
    assert body["next_cursor"] is None


def test_knowledge_ingest_rejects_path_escape(tmp_path) -> None:
    client = _client()
    response = client.post(
        "/api/v1/knowledge/ingest",
        json={"source_dir": str(tmp_path), "dry_run": True},
    )
    assert response.status_code == 400


def test_upload_folder_paths_cannot_escape_the_staging_root() -> None:
    assert _safe_upload_relative("审计/凭证.pdf", "ignored", 0).as_posix() == "审计/凭证.pdf"
    try:
        _safe_upload_relative("../secret.txt", "ignored", 1)
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 400
    else:  # pragma: no cover - assertion clarity
        raise AssertionError("path traversal must be rejected")
