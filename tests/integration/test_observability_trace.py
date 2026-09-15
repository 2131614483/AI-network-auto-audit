"""R3 contract tests: trace_id into logs, global exception handler,
rotating file logging, service code fingerprint, and the code-location index.

The user's hard standard is that every layer can be expanded down to the data
layer, the log layer and the code layer.  These tests pin the observability
contract that makes the log layer traceable and the code layer indexable.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.responses import JSONResponse

from packages.observability import (
    TraceIdFilter,
    build_code_map,
    configure_rotating_file_logging,
    service_code_sha256,
    trace_context,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
API_FILE = PROJECT_ROOT / "apps" / "api" / "main.py"

APP_MODULE = "apps.api.main"


@pytest.fixture(scope="module")
def app() -> FastAPI:
    from apps.api.main import create_app

    return create_app()


@pytest.fixture(scope="module")
def client(app: FastAPI) -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


def test_trace_filter_injects_trace_id_and_code_sha() -> None:
    record = logging.LogRecord("audit.test", logging.INFO, __file__, 1, "hello %s", ("world",), None)
    with trace_context("trace-abc123"):
        assert TraceIdFilter().filter(record) is True
        assert record.trace_id == "trace-abc123"  # type: ignore[attr-defined]
        assert re.fullmatch(r"[0-9a-f]{64}", record.code_sha)  # type: ignore[attr-defined]


def test_api_response_carries_trace_id(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert "X-Trace-Id" in response.headers
    trace_id = response.headers["X-Trace-Id"]
    assert trace_id != "-"
    assert UUID(trace_id)


def test_api_request_logs_carry_trace_id(client: TestClient) -> None:
    """A handler-emitted log line inside a request must carry that request's trace_id.

    The TestClient runs handlers on its own worker thread, so we attach a
    thread-safe list handler with TraceIdFilter and read what it captured.
    """

    def _log_route() -> JSONResponse:
        logging.getLogger("audit.api").info("trace-check endpoint hit")
        return JSONResponse({"ok": True})

    client.app.add_api_route("/__test_log", _log_route, methods=["GET"])  # type: ignore[attr-defined]
    collected: list[logging.LogRecord] = []

    class _Collect(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            collected.append(record)

    collector = _Collect()
    collector.addFilter(TraceIdFilter())
    root = logging.getLogger()
    root.addHandler(collector)
    try:
        response = client.get("/__test_log")
        assert response.status_code == 200
        trace_id = response.headers["X-Trace-Id"]
        matches = [r for r in collected if getattr(r, "trace_id", None) == trace_id]
        assert matches, f"no log record carried trace_id={trace_id}"
        assert all(re.fullmatch(r"[0-9a-f]{64}", getattr(r, "code_sha", "")) for r in matches)
    finally:
        root.removeHandler(collector)


def test_global_exception_handler_returns_structured_500(client: TestClient) -> None:
    def _boom() -> JSONResponse:
        raise RuntimeError("boom-in-test")

    client.app.add_api_route("/__test_boom", _boom, methods=["GET"])  # type: ignore[attr-defined]
    response = client.get("/__test_boom")
    assert response.status_code == 500
    body = response.json()
    assert body["error_code"] == "INTERNAL_ERROR"
    assert body["trace_id"] != "-"
    assert UUID(body["trace_id"])
    assert response.headers["X-Trace-Id"] == body["trace_id"]


def test_rotating_file_logging_installed(tmp_path: Path) -> None:
    log_file = tmp_path / "sub" / "audit.log"
    handler = configure_rotating_file_logging(log_file, max_bytes=1024, backup_count=3)
    try:
        assert isinstance(handler, logging.handlers.RotatingFileHandler)
        assert handler.backupCount == 3
        assert any(isinstance(f, TraceIdFilter) for f in handler.filters)
        logger = logging.getLogger("audit.rotate-test")
        logger.info("rotate test line")
        assert log_file.exists()
    finally:
        handler.close()
        logging.getLogger().removeHandler(handler)


def test_code_sha256_deterministic() -> None:
    first = service_code_sha256([PROJECT_ROOT / "packages"])
    second = service_code_sha256([PROJECT_ROOT / "packages"])
    assert first == second
    assert re.fullmatch(r"[0-9a-f]{64}", first)


def test_code_map_covers_every_api_route(app: FastAPI) -> None:
    code_map = build_code_map(PROJECT_ROOT, api_file=API_FILE)
    assert code_map["endpoints"]
    indexed = {(entry["method"].upper(), entry["path"]) for entry in code_map["endpoints"]}
    missing: list[str] = []
    for route in app.routes:
        path = getattr(route, "path", None)
        methods = sorted(getattr(route, "methods", None) or [])
        if not path or not path.startswith("/api/v1"):
            continue
        for method in methods:
            if (method, path) not in indexed:
                missing.append(f"{method} {path}")
    assert not missing, f"routes missing from code map: {missing}"


def test_code_map_endpoint_entries_have_location_and_tables() -> None:
    code_map = build_code_map(PROJECT_ROOT, api_file=API_FILE)
    endpoints = code_map["endpoints"]
    assert endpoints
    for entry in endpoints:
        assert entry["file"].endswith("main.py")
        assert isinstance(entry["line"], int) and entry["line"] > 0
        assert isinstance(entry["tables"], list)
    assert any(entry["tables"] for entry in endpoints), "at least one route should reference schema.tables"
    assert len({entry["code_sha256"] for entry in endpoints}) == 1


def test_readiness_uses_observability_trace(client: TestClient) -> None:
    response = client.get("/api/v1/health/ready")
    assert response.status_code == 200
    assert "X-Trace-Id" in response.headers
