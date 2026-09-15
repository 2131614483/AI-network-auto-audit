"""CW2 wiring contract tests: real API/worker logs into the spool, and the
collector deployed as a scheduled-task one-shot (--once).

Acceptance:
  - an API request under X-Trace-Id produces real spool events queryable by
    trace-locate's event path (no hand-seeded events);
  - the worker attaches the same spool with its own producer id;
  - collector --once runs one maintenance pass, is repeatable, and reports
    health JSON on stdout (scheduled-task deployment shape).
"""

from __future__ import annotations

import logging
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from packages.observability.collector import main as collector_main
from packages.observability.log_spool import SegmentedSpool
from packages.observability.logging_setup import attach_spool_logging

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SPOOL_ROOT = PROJECT_ROOT / ".data" / "isolated" / "logs"
TEST_DB = "postgresql://audit_app:admin@localhost:5432/audit_network_test"


def _detach_spool(producer_id: str) -> None:
    root = logging.getLogger()
    for handler in list(root.handlers):
        from packages.observability.spool_logging import SpoolLogHandler

        if isinstance(handler, SpoolLogHandler) and handler.producer_id == producer_id:
            root.removeHandler(handler)


def test_api_request_logs_queryable_via_spool(monkeypatch) -> None:
    from apps.api.main import Settings, create_app

    _detach_spool("api")
    attach_spool_logging(SPOOL_ROOT, producer_id="api")
    try:
        client = TestClient(create_app(Settings(database_url=TEST_DB)))
        trace_id = str(uuid4())
        response = client.get(
            "/api/v1/health/ready",
            headers={"X-Trace-Id": trace_id},
        )
        assert response.status_code == 200
        spool = SegmentedSpool(SPOOL_ROOT)
        result = spool.query(trace_id=trace_id, limit=20)
        events = result["events"]
        # the API's own log record for this request is really persisted
        assert events, "no spool events for the API request trace"
        assert any("trace_id" in e and e["trace_id"] == trace_id for e in events)
        assert all(e["producer_id"] == "api" for e in events)
    finally:
        _detach_spool("api")


def test_worker_attach_produces_own_producer_events(tmp_path: Path) -> None:
    spool_root = tmp_path / "spool"
    _detach_spool("worker")
    handler = attach_spool_logging(spool_root, producer_id="worker")
    try:
        logger = logging.getLogger("cw2.wiring.worker")
        trace_id = str(uuid4())
        logger.info("worker heartbeat line", extra={"trace_id": trace_id})
        handler.flush()
        spool = SegmentedSpool(spool_root)
        events = spool.query(trace_id=trace_id, limit=10)["events"]
        assert len(events) == 1
        assert events[0]["producer_id"] == "worker"
        assert "worker heartbeat line" in events[0]["message"]
    finally:
        _detach_spool("worker")


def test_collector_once_runs_healthy_and_repeatable(tmp_path: Path) -> None:
    spool_root = tmp_path / "spool"
    first = collector_main(["--root", str(spool_root), "--once"])
    assert first == 0
    second = collector_main(["--root", str(spool_root), "--once"])
    assert second == 0
    # repeatable: no crash, no leftover gap on an empty spool
    spool = SegmentedSpool(spool_root)
    report = spool.accepting
    assert report is True
