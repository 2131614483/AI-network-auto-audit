"""Durable planning sessions: real tenant RLS, idempotency and lease fencing."""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from uuid import UUID, uuid4

import psycopg2
import pytest

from packages.ai_planner.evidence import GoalRequirement, PlanningContext, PlanningRequest
from packages.ai_planner.session_store import (
    PlanningSessionConflict,
    PlanningSessionFenced,
    PlanningSessionNotFound,
    PlanningSessionPolicyDenied,
    PlanningSessionStore,
)
from packages.policy.engine import PolicyEngine
from packages.policy.gateway import record_decision

DB = os.getenv(
    "AUDIT_NETWORK_TEST_DATABASE_URL",
    "postgresql://audit_app:admin@localhost:5432/audit_network_test",
)
WRITE_CAPABILITY = "topology.collaborative.plan"


def _tenant() -> UUID:
    tenant_id = uuid4()
    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        cur.execute(
            "INSERT INTO iam.tenants(id,slug,name) VALUES(%s,%s,%s)",
            (str(tenant_id), f"collab-store-{tenant_id.hex}", "Isolated collaborative-store fixture"),
        )
    return tenant_id


def _decision(
    context: PlanningContext, *, capability: str = WRITE_CAPABILITY, allowed: bool = True,
) -> UUID:
    policy = PolicyEngine(allow=[capability] if allowed else [], deny=[] if allowed else [capability])
    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        cur.execute("SELECT set_config('app.tenant_id', %s, true)", (str(context.tenant_id),))
        recorded = record_decision(
            cur, context.tenant_id, capability, {"session_id": str(context.session_id)},
            policy.evaluate(capability, {}, "low", side_effects="write_data"), context.trace_id,
        )
    return recorded.decision_id


def _request(key: str | None = None) -> PlanningRequest:
    return PlanningRequest(
        goal="Produce a sampling plan", idempotency_key=key or f"store-{uuid4().hex}",
        requirements=[GoalRequirement(key="sampling", description="Sampling capability is required",
                                      capabilities=["audit.plan.sampling-select"])],
        reason="Synthetic local persistence test",
    )


@pytest.fixture()
def session() -> tuple[PlanningSessionStore, PlanningContext, PlanningRequest, UUID]:
    context = PlanningContext(tenant_id=_tenant(), trace_id=uuid4(), session_id=uuid4())
    decision_id = _decision(context)
    return PlanningSessionStore(DB), context, _request(), decision_id


def _expire(context: PlanningContext) -> None:
    # Fault injection in this test's isolated tenant; retain the entire history.
    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        cur.execute("SELECT set_config('app.tenant_id', %s, true)", (str(context.tenant_id),))
        cur.execute(
            "UPDATE topology.planning_sessions SET lease_expires_at=now()-interval '1 second' "
            "WHERE tenant_id=%s AND id=%s",
            (str(context.tenant_id), str(context.session_id)),
        )


def test_create_replays_the_same_request_and_rejects_a_changed_body(session) -> None:
    store, context, request, decision = session
    original = store.create(context, request, decision)
    proposed = context.model_copy(update={"session_id": uuid4()})
    replay = store.create(proposed, request, decision)

    assert original["session_id"] == replay["session_id"] == str(context.session_id)
    assert original["idempotent"] is False and replay["idempotent"] is True
    assert original["request_json"] == request.model_dump(mode="json")
    assert original["trace_id"] == str(context.trace_id)
    with pytest.raises(PlanningSessionConflict):
        store.create(context, request.model_copy(update={"goal": "A different objective"}), decision)
    assert len(store.list(context.tenant_id)) == 1


def test_concurrent_submissions_create_one_session(session) -> None:
    store, context, request, decision = session
    proposed = [context, context.model_copy(update={"session_id": uuid4()})]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda ctx: store.create(ctx, request, decision), proposed))

    assert len({row["session_id"] for row in results}) == 1
    assert sorted(row["idempotent"] for row in results) == [False, True]
    assert len(store.list(context.tenant_id)) == 1


@pytest.mark.parametrize("kind", ["denied", "read-grant", "other-trace", "other-tenant"])
def test_writes_require_the_exact_tenant_trace_and_write_capability(session, kind: str) -> None:
    store, context, request, _ = session
    if kind == "denied":
        decision = _decision(context, allowed=False)
    elif kind == "read-grant":
        decision = _decision(context, capability="topology.collaborative.read")
    elif kind == "other-trace":
        decision = _decision(context.model_copy(update={"trace_id": uuid4()}))
    else:
        decision = _decision(context.model_copy(update={"tenant_id": _tenant()}))

    with pytest.raises(PlanningSessionPolicyDenied):
        store.create(context, request, decision)
    assert store.list(context.tenant_id) == []


def test_other_tenants_cannot_read_or_append_to_a_session(session) -> None:
    store, context, request, decision = session
    store.create(context, request, decision)
    claim = store.claim(context, "worker-a", decision)
    assert claim is not None
    other = context.model_copy(update={"tenant_id": _tenant()})

    with pytest.raises(PlanningSessionNotFound):
        store.get(other.tenant_id, context.session_id)
    assert store.list(other.tenant_id) == []
    with pytest.raises(PlanningSessionNotFound):
        store.append(other, "foreign-query", "query", {}, _decision(other), claim["lease_token"])
    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        cur.execute("SELECT set_config('app.tenant_id', %s, true)", (str(other.tenant_id),))
        # Deliberately omit an explicit tenant predicate: database RLS must hide it.
        cur.execute("SELECT count(*) FROM topology.planning_sessions WHERE id=%s", (str(context.session_id),))
        assert cur.fetchone()[0] == 0
        cur.execute(
            "SELECT count(*) FROM topology.planning_session_events WHERE session_id=%s",
            (str(context.session_id),),
        )
        assert cur.fetchone()[0] == 0


def test_claim_is_exclusive_and_expired_owner_is_fenced(session) -> None:
    store, context, request, decision = session
    store.create(context, request, decision)
    first = store.claim(context, "worker-a", decision)
    assert first is not None
    assert store.claim(context, "worker-b", decision) is None
    _expire(context)
    second = store.claim(context, "worker-b", decision)
    assert second is not None
    assert second["lease_token"] != first["lease_token"]

    with pytest.raises(PlanningSessionFenced):
        store.append(context, "late-query", "query", {}, decision, first["lease_token"])
    with pytest.raises(PlanningSessionFenced):
        store.finish(context, "accepted", {"late": True}, decision, first["lease_token"])
    store.append(context, "fresh-query", "query", {"query": "fresh"}, decision, second["lease_token"])
    assert store.get(context.tenant_id, context.session_id)["worker_id"] == "worker-b"


def test_concurrent_workers_cannot_both_claim_the_same_session(session) -> None:
    store, context, request, decision = session
    store.create(context, request, decision)
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(lambda worker: store.claim(context, worker, decision), ["worker-a", "worker-b"]))

    assert sum(claim is not None for claim in claims) == 1
    assert store.get(context.tenant_id, context.session_id)["claim_count"] == 1


def test_expired_lease_cannot_commit_or_renew_before_reclaim(session) -> None:
    store, context, request, decision = session
    store.create(context, request, decision)
    claimed = store.claim(context, "worker-a", decision)
    assert claimed is not None
    _expire(context)

    with pytest.raises(PlanningSessionFenced):
        store.append(context, "expired-query", "query", {}, decision, claimed["lease_token"])
    with pytest.raises(PlanningSessionFenced):
        store.renew(context, claimed["lease_token"], decision)
    with pytest.raises(PlanningSessionFenced):
        store.finish(context, "failed", {}, decision, claimed["lease_token"])


def test_events_and_checkpoint_are_atomic_and_replayable(session) -> None:
    store, context, request, decision = session
    store.create(context, request, decision)
    claimed = store.claim(context, "worker-a", decision)
    assert claimed is not None
    token = claimed["lease_token"]
    checkpoint = {"round_no": 1, "queries_used": 3, "evidence": [{"evidence_id": "k1"}]}
    store.append(context, "round-1", "assessment", {"passed": False}, decision, token, checkpoint)
    store.append(context, "round-1", "assessment", {"passed": False}, decision, token, checkpoint)

    rows = [event for event in store.events(context.tenant_id, context.session_id)
            if event["event_key"] == "round-1"]
    assert len(rows) == 1
    assert rows[0]["decision_id"] == str(decision)
    assert rows[0]["session_id"] == str(context.session_id)
    assert rows[0]["round_no"] == 1
    assert store.get(context.tenant_id, context.session_id)["checkpoint_json"] == checkpoint
    with pytest.raises(PlanningSessionConflict):
        store.append(context, "round-1", "assessment", {"passed": True}, decision, token,
                     {"queries_used": 0})
    assert store.get(context.tenant_id, context.session_id)["checkpoint_json"] == checkpoint


def test_a_new_store_resumes_checkpoint_and_keeps_a_terminal_result(session) -> None:
    store, context, request, decision = session
    store.create(context, request, decision)
    claimed = store.claim(context, "crashed-worker", decision)
    assert claimed is not None
    checkpoint = {"round_no": 2, "queries_used": 4, "candidate": {"nodes": ["n1"]}}
    store.append(context, "checkpoint-2", "round", {"number": 2}, decision, claimed["lease_token"], checkpoint)
    _expire(context)

    restored = PlanningSessionStore(DB)
    assert restored.list_pending(context.tenant_id)[0]["session_id"] == str(context.session_id)
    resumed = restored.claim(context, "replacement-worker", decision)
    assert resumed is not None and resumed["checkpoint_json"] == checkpoint
    result: dict[str, Any] = {"status": "accepted", "candidate_hash": "a" * 64, "plan": {"nodes": ["n1"]}}
    terminal = restored.finish(context, "accepted", result, decision, resumed["lease_token"])

    assert terminal["status"] == "accepted"
    assert terminal["result_json"] == result
    assert terminal["finished_at"]
    assert restored.get(context.tenant_id, context.session_id)["result_json"] == result
    assert restored.claim(context, "third-worker", decision) is None
    assert restored.list_pending(context.tenant_id) == []
    assert restored.finish(context, "accepted", result, decision, resumed["lease_token"])["result_json"] == result
    with pytest.raises(PlanningSessionConflict):
        restored.finish(context, "accepted", {"replacement": True}, decision, resumed["lease_token"])


def test_events_are_append_only_for_the_application_role(session) -> None:
    store, context, request, decision = session
    store.create(context, request, decision)
    claimed = store.claim(context, "worker-a", decision)
    assert claimed is not None
    store.append(context, "q1", "query", {"source": "knowledge"}, decision, claimed["lease_token"])

    for verb in ("UPDATE topology.planning_session_events SET payload='{}'::jsonb",
                 "DELETE FROM topology.planning_session_events"):
        with pytest.raises(psycopg2.errors.InsufficientPrivilege):
            with psycopg2.connect(DB) as connection, connection.cursor() as cur:
                cur.execute("SELECT set_config('app.tenant_id', %s, true)", (str(context.tenant_id),))
                cur.execute(f"{verb} WHERE session_id=%s", (str(context.session_id),))


def test_new_planning_policy_is_inactive_and_tables_force_rls() -> None:
    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        cur.execute("SELECT id FROM iam.tenants WHERE slug='local-dev'")
        tenant_id = cur.fetchone()[0]
        cur.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),))
        cur.execute("SELECT status,rules FROM policy.policy_sets WHERE name='local-collaborative-planning' AND version=1")
        status, rules = cur.fetchone()
        assert status == "inactive"
        capabilities = {capability for rule in rules for capability in rule["match"]["capabilities"]}
        assert "topology.collaborative.input.read" in capabilities
        assert all("*" not in capability and "execute" not in capability for capability in capabilities)
        cur.execute(
            "SELECT relrowsecurity,relforcerowsecurity FROM pg_class "
            "WHERE oid IN ('topology.planning_sessions'::regclass,'topology.planning_session_events'::regclass)"
        )
        assert cur.fetchall() == [(True, True), (True, True)]
