"""R2 readiness probe: /api/v1/health/ready returns structured operational facts."""

from __future__ import annotations

import os
from uuid import UUID

from fastapi.testclient import TestClient

from apps.api.main import EXPECTED_MIGRATION_HEAD, Settings, create_app

DB = os.getenv("AUDIT_NETWORK_TEST_DATABASE_URL", "postgresql://audit_app:admin@localhost:5432/audit_network_test")


def test_health_ready_returns_structured_probe() -> None:
    client = TestClient(create_app(Settings(database_url=DB)))
    response = client.get("/api/v1/health/ready")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] in {"ok", "degraded"}
    assert payload["database"] == "ok"
    assert payload["migration_head"] == EXPECTED_MIGRATION_HEAD
    assert payload["migration_expected"] == EXPECTED_MIGRATION_HEAD
    assert isinstance(payload["worker_heartbeats"], int)
    assert isinstance(payload["worker_stale"], int)
    assert isinstance(payload["outbox_backlog"], int)
    assert isinstance(payload["stuck_tasks"], int)
    assert isinstance(payload["checks"], list)
    UUID(payload["trace_id"])


def test_health_liveness_still_reports_ok() -> None:
    client = TestClient(create_app(Settings(database_url=DB)))
    response = client.get("/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["service"] == "audit-network-api"
    assert payload["database_configured"] is True
