"""CW5 canvas chat API integration tests (test database, offline model fake).

Acceptance (方案 第6节 / 第13节 CW5):
  - a chat message plans a graph flow only through the policy gateway
    (``topology.intent.plan``, fail-closed when inactive);
  - every request writes an append-only ``topology.planning_intents`` row and
    replays idempotently without duplicates;
  - a broken/absent model backend is a visible 503, never a faked draft;
  - a base_draft carried by the client reaches the planner goal.
"""

from __future__ import annotations

import json
import os
from typing import Any
from uuid import UUID, uuid4

import psycopg2
import psycopg2.extras
import pytest
from fastapi.testclient import TestClient

from apps.api.main import Settings, create_app
from packages.ai_planner import TEMPLATES
from packages.llm.openai_compat_client import OpenAICompatChatError

TEST_DB = os.getenv("AUDIT_NETWORK_TEST_DATABASE_URL", "postgresql://audit_app:admin@localhost:5432/audit_network_test")


class _FakeLLM:
    """Deterministic injected model: returns preset JSON drafts in order."""

    def __init__(self, drafts: list[Any]) -> None:
        self.drafts = drafts
        self.calls = 0
        self.messages_seen: list[list[dict[str, str]]] = []

    def complete_json(self, messages: list[dict[str, str]], *, temperature: float) -> dict[str, Any]:
        self.calls += 1
        self.messages_seen.append(list(messages))
        draft = self.drafts[min(self.calls - 1, len(self.drafts) - 1)]
        if isinstance(draft, Exception):
            raise draft
        return draft


def _template_draft(**overrides: Any) -> dict[str, Any]:
    template = TEMPLATES["ledger-backtest"]
    draft = {
        "plan_key": f"plan-chat-{uuid4().hex[:8]}",
        "nodes": template["nodes"],
        "edges": template["edges"],
        "budget": template["budget"],
        "seed_inputs": [["ledger-a", "ledger"]],
        "selection_reasons": "chat integration draft",
    }
    draft.update(overrides)
    return draft


def _tenant() -> UUID:
    with psycopg2.connect(TEST_DB) as connection:
        with connection.cursor() as cur:
            cur.execute("SELECT id FROM iam.tenants WHERE slug='local-dev'")
            row = cur.fetchone()
            assert row is not None
            return UUID(str(row[0]))


def _other_tenant() -> UUID:
    with psycopg2.connect(TEST_DB) as connection:
        with connection.cursor() as cur:
            cur.execute(
                "SELECT id FROM iam.tenants WHERE slug <> 'local-dev' "
                "AND (slug LIKE '%-other%' OR slug LIKE 'm3-other%') ORDER BY slug LIMIT 1",
            )
            row = cur.fetchone()
            assert row is not None
            return UUID(str(row[0]))


def _api_headers(tenant_id: UUID) -> dict[str, str]:
    return {"X-Tenant-Id": str(tenant_id), "X-Trace-Id": str(uuid4())}


def _allow_chat(connection, tenant_id: UUID, name: str) -> None:
    with connection.cursor() as cur:
        cur.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant_id),))
        cur.fetchone()
        cur.execute(
            "INSERT INTO policy.policy_sets(tenant_id,name,version,status,rules) VALUES(%s,%s,1,'active',%s)",
            (
                str(tenant_id),
                name,
                psycopg2.extras.Json([{
                    "rule_id": str(uuid4()), "effect": "allow",
                    "match": {
                        "capabilities": ["topology.intent.plan"],
                        "risk_classes": ["low"], "side_effects": ["write_data"],
                    },
                }]),
            ),
        )


def _chat_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "message": "校验日记账质量并生成回测结论",
        "session_id": f"chat-{uuid4().hex[:12]}",
        "data_sources": [["ledger-a", "ledger"]],
        "template_keys": ["ledger-backtest"],
        "idempotency_key": f"chat-key-{uuid4().hex[:10]}",
    }
    payload.update(overrides)
    return payload


def _count_intent_rows(tenant_id: UUID, idempotency_key: str) -> int:
    with psycopg2.connect(TEST_DB) as connection:
        with connection.cursor() as cur:
            # planning_intents is RLS + FORCE RLS: the query needs the tenant
            # context the same way the API sets it.
            cur.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant_id),))
            cur.fetchone()
            cur.execute(
                "SELECT count(*) FROM topology.planning_intents "
                "WHERE tenant_id=%s AND idempotency_key=%s",
                (str(tenant_id), idempotency_key),
            )
            return int(cur.fetchone()[0])


# -- policy gate ----------------------------------------------------------------


def test_chat_requires_policy_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    tenant = _other_tenant()
    monkeypatch.setattr(
        "apps.api.main.ai_planner_module._default_llm",
        lambda: _FakeLLM([_template_draft()]),
    )
    client = TestClient(create_app(Settings(database_url=TEST_DB)))
    response = client.post(
        "/api/v1/topology/canvas/chat",
        json=_chat_payload(),
        headers=_api_headers(tenant),
    )
    # no allow rule -> the gateway must not auto-allow a write_data intent
    assert response.status_code in (403, 409)


# -- happy path: draft_ready + append-only evidence -----------------------------


def test_chat_draft_ready_records_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    tenant = _tenant()
    with psycopg2.connect(TEST_DB) as connection:
        _allow_chat(connection, tenant, f"cw5-chat-{uuid4().hex[:6]}")
    fake = _FakeLLM([_template_draft()])
    monkeypatch.setattr("apps.api.main.ai_planner_module._default_llm", lambda: fake)
    client = TestClient(create_app(Settings(database_url=TEST_DB)))
    payload = _chat_payload()
    response = client.post(
        "/api/v1/topology/canvas/chat",
        json=payload,
        headers=_api_headers(tenant),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "draft_ready"
    assert body["plan_key"].startswith("plan-chat-")
    assert body["draft"] and body["draft"]["nodes"]
    assert "节点" in body["reply_text"] and "边" in body["reply_text"]
    assert body["backend"] == "openai_compat"
    assert body["intent_id"], "chat must record an evidence row"
    assert _count_intent_rows(tenant, payload["idempotency_key"]) == 1


# -- idempotent replay ----------------------------------------------------------


def test_chat_replays_idempotency_key(monkeypatch: pytest.MonkeyPatch) -> None:
    tenant = _tenant()
    with psycopg2.connect(TEST_DB) as connection:
        _allow_chat(connection, tenant, f"cw5-chat-idem-{uuid4().hex[:6]}")
    fake = _FakeLLM([_template_draft()])
    monkeypatch.setattr("apps.api.main.ai_planner_module._default_llm", lambda: fake)
    client = TestClient(create_app(Settings(database_url=TEST_DB)))
    payload = _chat_payload()
    headers = _api_headers(tenant)
    first = client.post("/api/v1/topology/canvas/chat", json=payload, headers=headers)
    second = client.post("/api/v1/topology/canvas/chat", json=payload, headers=headers)
    assert first.status_code == 200 and second.status_code == 200
    assert _count_intent_rows(tenant, payload["idempotency_key"]) == 1
    assert first.json()["intent_id"] == second.json()["intent_id"]


# -- model outage is visible, never faked ---------------------------------------


def test_chat_model_outage_503(monkeypatch: pytest.MonkeyPatch) -> None:
    tenant = _tenant()
    with psycopg2.connect(TEST_DB) as connection:
        _allow_chat(connection, tenant, f"cw5-chat-out-{uuid4().hex[:6]}")

    def _broken() -> Any:
        raise OpenAICompatChatError("cloud backend unavailable")

    monkeypatch.setattr("apps.api.main.ai_planner_module._default_llm", _broken)
    client = TestClient(create_app(Settings(database_url=TEST_DB)))
    payload = _chat_payload()
    response = client.post(
        "/api/v1/topology/canvas/chat",
        json=payload,
        headers=_api_headers(tenant),
    )
    assert response.status_code == 503
    assert "model backend unavailable" in response.json()["detail"]
    assert "失败记录已保存" in response.json()["detail"]
    assert _count_intent_rows(tenant, payload["idempotency_key"]) == 1


# -- malformed payloads ---------------------------------------------------------


def test_chat_rejects_malformed_sources(monkeypatch: pytest.MonkeyPatch) -> None:
    tenant = _tenant()
    with psycopg2.connect(TEST_DB) as connection:
        _allow_chat(connection, tenant, f"cw5-chat-mal-{uuid4().hex[:6]}")
    client = TestClient(create_app(Settings(database_url=TEST_DB)))
    response = client.post(
        "/api/v1/topology/canvas/chat",
        json=_chat_payload(data_sources=[["ledger-a"]]),
        headers=_api_headers(tenant),
    )
    assert response.status_code == 422


# -- base_draft revision reaches the planner goal --------------------------------


def test_chat_revision_goal_carries_base_draft(monkeypatch: pytest.MonkeyPatch) -> None:
    tenant = _tenant()
    with psycopg2.connect(TEST_DB) as connection:
        _allow_chat(connection, tenant, f"cw5-chat-rev-{uuid4().hex[:6]}")
    fake = _FakeLLM([_template_draft()])
    monkeypatch.setattr("apps.api.main.ai_planner_module._default_llm", lambda: fake)
    client = TestClient(create_app(Settings(database_url=TEST_DB)))
    base = _template_draft()
    response = client.post(
        "/api/v1/topology/canvas/chat",
        json=_chat_payload(
            message="在现有草稿上增加证据校验节点",
            base_draft=base,
            history=[{"role": "assistant", "content": "已生成 2 节点 1 边草稿。"}],
        ),
        headers=_api_headers(tenant),
    )
    assert response.status_code == 200
    assert fake.messages_seen, "the planner must have called the model"
    joined = "\n".join(
        str(m.get("content") or "") for m in fake.messages_seen[0]
    )
    assert "在现有草稿上增加证据校验节点" in joined
    assert "既有画布草稿" in joined


# -- SSE stream endpoint --------------------------------------------------------


def _parse_sse(text: str) -> list[tuple[str, Any]]:
    events: list[tuple[str, Any]] = []
    for block in text.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        event_type = "message"
        data_lines: list[str] = []
        for line in block.splitlines():
            if line.startswith("event:"):
                event_type = line[len("event:"):].strip()
            elif line.startswith("data:"):
                data_lines.append(line[len("data:"):].strip())
        if data_lines:
            events.append((event_type, json.loads("\n".join(data_lines))))
    return events


def test_chat_stream_emits_stage_then_done(monkeypatch: pytest.MonkeyPatch) -> None:
    tenant = _tenant()
    with psycopg2.connect(TEST_DB) as connection:
        _allow_chat(connection, tenant, f"cw5-chat-ss-{uuid4().hex[:6]}")
    fake = _FakeLLM([_template_draft()])
    monkeypatch.setattr("apps.api.main.ai_planner_module._default_llm", lambda: fake)
    client = TestClient(create_app(Settings(database_url=TEST_DB)))
    payload = _chat_payload()
    response = client.post(
        "/api/v1/topology/canvas/chat/stream",
        json=payload,
        headers=_api_headers(tenant),
    )
    assert response.status_code == 200
    events = _parse_sse(response.text)
    types = [event_type for event_type, _ in events]
    assert types[0] == "stage"
    assert "stage" in types and "done" in types
    done_data = next(data for event_type, data in events if event_type == "done")
    assert done_data["status"] == "draft_ready"
    assert done_data["draft"] and done_data["draft"]["nodes"]
    assert done_data["intent_id"]
    assert _count_intent_rows(tenant, payload["idempotency_key"]) == 1
    stage_data = next(data for event_type, data in events if event_type == "stage")
    assert stage_data["stage"] in ("capability_recall", "llm_draft", "validate", "compile")


def test_chat_stream_model_outage_emits_error_event(monkeypatch: pytest.MonkeyPatch) -> None:
    tenant = _tenant()
    with psycopg2.connect(TEST_DB) as connection:
        _allow_chat(connection, tenant, f"cw5-chat-ss-err-{uuid4().hex[:6]}")

    def _broken() -> Any:
        raise OpenAICompatChatError("cloud backend unavailable")

    monkeypatch.setattr("apps.api.main.ai_planner_module._default_llm", _broken)
    client = TestClient(create_app(Settings(database_url=TEST_DB)))
    payload = _chat_payload()
    response = client.post(
        "/api/v1/topology/canvas/chat/stream",
        json=payload,
        headers=_api_headers(tenant),
    )
    assert response.status_code == 200
    events = _parse_sse(response.text)
    assert events[-1][0] == "error"
    assert "model backend unavailable" in events[-1][1]["detail"]
    assert events[-1][1]["intent_id"]
    assert _count_intent_rows(tenant, payload["idempotency_key"]) == 1


def test_chat_stream_requires_policy_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    tenant = _other_tenant()
    monkeypatch.setattr(
        "apps.api.main.ai_planner_module._default_llm",
        lambda: _FakeLLM([_template_draft()]),
    )
    client = TestClient(create_app(Settings(database_url=TEST_DB)))
    response = client.post(
        "/api/v1/topology/canvas/chat/stream",
        json=_chat_payload(),
        headers=_api_headers(tenant),
    )
    assert response.status_code in (403, 409)
