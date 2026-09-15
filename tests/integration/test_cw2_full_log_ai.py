"""CW2 full contract tests: resident collector, spool log handler, API log
event query, and real local-Ollama AI inference wired into the pipeline.

Acceptance:
  - a resident collector surfaces gaps / back-pressure (日志写失败暂停准入);
  - stdlib logging records persist into the spool with trace_id (trace filter);
  - trace-locate returns a real event/archive-index query (complete/partial/
    unavailable), not just file-path suggestions;
  - a local Ollama Qwen3 model (16K context forced) answers a constrained
    audit classification task, and the AI result becomes queryable evidence.

Ollama tests run against the local server (qwen3_27b_iq3xxs_64k:latest); when
the model/server is unavailable they are skipped with an explicit reason —
never faked.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from uuid import UUID, uuid4

import psycopg2
import pytest
from fastapi.testclient import TestClient

from packages.llm.ollama_client import OllamaChat, OllamaChatError, _is_available
from packages.observability.collector import CollectorService
from packages.observability.log_spool import LogEnvelope, SegmentedSpool
from packages.observability.spool_logging import SpoolLogHandler

TEST_DB = "postgresql://audit_app:admin@localhost:5432/audit_network_test"


def _env(producer: str, seq: int, message: str, *, trace_id: str | None = None,
         level: str = "info", run_id: str | None = None) -> LogEnvelope:
    return LogEnvelope(
        event_id=f"evt-{producer}-{seq}",
        producer_id=producer,
        producer_epoch=1,
        seq=seq,
        timestamp=f"2026-09-09T00:00:{seq:02d}+00:00",
        level=level,
        source="test",
        message=message,
        trace_id=trace_id,
        run_id=run_id,
        stream="system",
    )


# -- 1. stdlib logging -> spool (trace filter) --------------------------------


def test_spool_log_handler_persists_trace_id(tmp_path: Path) -> None:
    spool = SegmentedSpool(tmp_path / "spool")
    handler = SpoolLogHandler(spool, producer_id="cw2-svc")
    logger = logging.getLogger(f"cw2.handler.{uuid4().hex[:6]}")
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    trace_id = str(uuid4())
    try:
        logger.info("hello %s", "world", extra={"trace_id": trace_id})
        logger.warning("warn-line", extra={"trace_id": trace_id})
    finally:
        logger.removeHandler(handler)
        handler.close()
    spool.close()

    events = spool.query(trace_id=trace_id, limit=10)["events"]
    assert [e["message"] for e in events] == ["hello world", "warn-line"]
    assert all(e["trace_id"] == trace_id for e in events)
    # a different trace must not see these events
    assert spool.query(trace_id=str(uuid4()), limit=10)["events"] == []


# -- 2. resident collector: healthy / gap / back-pressure ----------------------


def test_collector_maintenance_healthy_then_detects_gap(tmp_path: Path) -> None:
    root = tmp_path / "spool"
    spool = SegmentedSpool(root)
    spool.append("svc", 1, [_env("svc", 1, "row-1")])
    spool.close()
    service = CollectorService(spool)
    healthy = service.maintenance()
    assert healthy["status"] == "healthy"
    assert healthy["accepting"] is True

    # crash residue: a hot segment with no manifest entry and no live writer
    ghost = root / "hot" / "ghost" / "e1"
    ghost.mkdir(parents=True, exist_ok=True)
    (ghost / "ghost-e1-s0001.jsonl").write_text('{"x":1}\n', encoding="utf-8")
    degraded = service.maintenance()
    assert degraded["status"] == "degraded"
    assert "unarchived" in (degraded["last_error"] or "")


def test_collector_surfaces_write_failure_backpressure(tmp_path: Path) -> None:
    root = tmp_path / "spool"
    hot_dir = root / "hot" / "p1" / "e1"
    hot_dir.parent.mkdir(parents=True, exist_ok=True)
    hot_dir.write_text("occupied", encoding="utf-8")
    spool = SegmentedSpool(root)
    from packages.observability.log_spool import SpoolWriteError

    with pytest.raises(SpoolWriteError):
        spool.append("p1", 1, [_env("p1", 1, "boom")])
    assert spool.accepting is False
    service = CollectorService(spool)
    report = service.maintenance()
    assert report["status"] == "degraded"
    assert report["accepting"] is False


# -- 3. trace-locate returns a real log-event query ---------------------------

def _tenant(connection, slug: str) -> UUID:
    with connection.cursor() as cur:
        cur.execute("SELECT id FROM iam.tenants WHERE slug=%s", (slug,))
        row = cur.fetchone()
        assert row is not None, f"tenant {slug} must exist"
        return UUID(str(row[0]))


def _allow(connection, tenant_id: UUID, name: str, capability: str) -> None:
    import psycopg2.extras

    with connection.cursor() as cur:
        cur.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant_id),))
        cur.fetchone()
        cur.execute(
            "INSERT INTO policy.policy_sets(tenant_id,name,version,status,rules) VALUES(%s,%s,1,'active',%s)",
            (str(tenant_id), name, psycopg2.extras.Json([
                {
                    "rule_id": str(uuid4()), "effect": "allow",
                    "match": {
                        "capabilities": [capability], "risk_classes": ["read_only"],
                        "side_effects": ["read_only"],
                    },
                }
            ])),
        )


def _client() -> TestClient:
    from apps.api.main import Settings, create_app

    return TestClient(create_app(Settings(database_url=TEST_DB)))


def test_trace_locate_includes_log_event_query(monkeypatch, tmp_path: Path) -> None:
    # Own spool root, pointed at via AUDIT_NETWORK_SPOOL_ROOT.  Writing into the
    # shared application spool made this test fail as soon as a segment id from a
    # previous run was still on disk — reusing it failed content verification —
    # and it left suite segments behind for real runs to read.
    spool_root = tmp_path / "spool"
    monkeypatch.setenv("AUDIT_NETWORK_SPOOL_ROOT", str(spool_root))
    spool_root.mkdir(parents=True, exist_ok=True)
    spool = SegmentedSpool(spool_root)
    trace_id = str(uuid4())
    for i in range(1, 4):
        spool.append("cw2-api", 1, [_env("cw2-api", i, f"trace-line-{i}", trace_id=trace_id)])
    spool.seal("cw2-api", 1, last_seq=3)
    spool.close()

    with psycopg2.connect(TEST_DB) as connection:
        owner = _tenant(connection, "local-dev")
        _allow(connection, owner, f"cw2-trace-{uuid4().hex[:6]}", "observability.trace.read")

    client = _client()
    response = client.get(
        f"/api/v1/observability/trace/{trace_id}",
        headers={"X-Tenant-Id": str(owner), "X-Trace-Id": str(uuid4())},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["completeness"] == "complete"
    log_query = body["log_query"]
    assert log_query is not None
    assert log_query["completeness"] == "complete"
    assert [e["message"] for e in log_query["events"]] == ["trace-line-1", "trace-line-2", "trace-line-3"]
    assert log_query["archive_state"] == "archived"
    assert log_query["persisted_seq"] == 3


# -- 4. real local Ollama Qwen3 (16K context) wired into the pipeline ---------

_ollama_ready = _is_available()


@pytest.mark.skipif(not _ollama_ready, reason="本地 ollama 服务不可用（跳过真实 AI 调用）")
def test_ollama_chat_qwen3_forces_configured_context() -> None:
    import os

    chat = OllamaChat()
    # 64K is the default (Qwen3 27B native window); the operator may tune it
    # via OLLAMA_CHAT_NUM_CTX without breaking this contract test.
    assert chat.num_ctx == int(os.getenv("OLLAMA_CHAT_NUM_CTX", "65536"))
    assert chat.num_ctx >= 16384
    assert "qwen3" in chat.model.lower() or "qwen" in chat.model.lower()
    reply = chat.complete([{"role": "user", "content": "只回复两个汉字：收到"}])
    assert isinstance(reply, str) and reply.strip()


@pytest.mark.skipif(not _ollama_ready, reason="本地 ollama 服务不可用（跳过真实 AI 调用）")
def test_ollama_ai_classification_json_and_spool_evidence(tmp_path: Path) -> None:
    chat = OllamaChat()
    system = "你是审计台账分类器。只输出 JSON，不输出任何其他文字。"
    user = (
        "把以下三条台账描述分类为 收款/划转/其他："
        "1) 客户回款100元；2) 内部账户互转200元；3) 购买办公用品300元。"
        '输出格式：{"items":[{"id":1,"label":"收款"},{"id":2,"label":"划转"},{"id":3,"label":"其他"}]}'
    )
    data = chat.complete_json([{"role": "system", "content": system}, {"role": "user", "content": user}])
    assert isinstance(data, dict) and "items" in data
    items = data["items"]
    assert isinstance(items, list) and len(items) == 3
    labels = {item.get("label") for item in items}
    assert labels <= {"收款", "划转", "其他"}

    # evidence loop: the AI result becomes queryable spool evidence under a trace
    spool = SegmentedSpool(tmp_path / "spool")
    trace_id = str(uuid4())
    spool.append("ai", 1, [
        _env("ai", 1, f"AI 分类结果: {json.dumps(data, ensure_ascii=False, sort_keys=True)}",
             trace_id=trace_id, level="info"),
    ])
    spool.seal("ai", 1, last_seq=1)
    spool.close()
    result = spool.query(trace_id=trace_id, limit=10)
    assert result["completeness"] == "complete"
    assert result["persisted_seq"] == 1
    assert result["events"] and result["events"][0]["message"].startswith("AI 分类结果:")
    assert "收款" in result["events"][0]["message"]


@pytest.mark.skipif(not _ollama_ready, reason="本地 ollama 服务不可用（跳过真实 AI 调用）")
def test_ollama_chat_fail_closed_on_garbage_model() -> None:
    chat = OllamaChat(model="no-such-model-xyz")
    with pytest.raises(OllamaChatError):
        chat.complete([{"role": "user", "content": "hi"}])
