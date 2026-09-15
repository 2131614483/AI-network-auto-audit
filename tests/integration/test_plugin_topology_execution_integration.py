"""Plugin Topology M4 integration tests against the isolated test database.

Covers the human-approval workflow (requires_approval -> approved_projection /
denied), fail-closed whole-chain gating, deterministic simulated execution and
the immutable ledgers seeded by migration 0040.  Nothing here starts a
subprocess; every execution row is a pure status projection (mode=simulated).
"""

from __future__ import annotations

import hashlib
import os
from uuid import UUID, uuid4

import psycopg2
import pytest

from packages.plugin_topology.executor import node_output_checksum
from packages.plugin_topology.service import TopologyService
from packages.policy.engine import PolicyEngine

DB = os.getenv("AUDIT_NETWORK_TEST_DATABASE_URL", "postgresql://audit_app:admin@localhost:5432/audit_network_test")

_SEED_CHAIN_KEY = "chain-" + hashlib.sha256(
    "|".join([
        "plan-" + hashlib.sha256(b"ledger-quality-slot|research-note-slot").hexdigest()[:16],
        "ledger-quality-slot",
        "research-note-slot",
    ]).encode("utf-8")
).hexdigest()[:16]


def _tenant(connection) -> UUID:
    with connection.cursor() as cur:
        cur.execute("SELECT id FROM iam.tenants WHERE slug='local-dev'")
        tenant_id = cur.fetchone()[0]
        cur.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant_id),))
        cur.fetchone()
    return tenant_id


def _service() -> TopologyService:
    return TopologyService(
        DB,
        policy=PolicyEngine(allow=[
            "topology.cluster.write",
            "topology.blueprint.write",
            "topology.membership.write",
            "topology.edge.write",
            "topology.plan.read",
            "topology.chain.write",
            "topology.chain.approve",
            "topology.chain.execute",
        ]),
    )


@pytest.fixture(scope="module")
def marker() -> str:
    return uuid4().hex[:8]


def _build_chain(service: TopologyService, marker: str, suffix: str) -> tuple[str, str, str]:
    """Register a 2-node domain and materialize a chain whose intents all
    require human approval (the service policy allows topology writes but
    never the node capabilities)."""
    domain_key = f"it-{marker}-domain"
    producer_key = f"it-{marker}-producer"
    consumer_key = f"it-{marker}-consumer"
    shared = f"it-{suffix}-shared-contract"

    service.upsert(
        {
            "kind": "cluster",
            "idempotency_key": f"{marker}-exec-cluster",
            "payload": {
                "key": domain_key, "name": "M4 执行域", "axis": "business_domain", "layer": "L0",
                "domains": ["audit"],
                "routing_budget": {"max_candidates": 8, "max_chain_length": 4, "max_latency_ms": 5000},
            },
        }
    )
    for key, capability, contract in (
        (producer_key, f"audit.it-{suffix}.produce", {"capability": f"audit.it-{suffix}.produce", "version": "1.0.0", "outputs": [shared]}),
        (consumer_key, f"audit.it-{suffix}.consume", {"capability": f"audit.it-{suffix}.consume", "version": "1.0.0", "inputs": [shared], "outputs": []}),
    ):
        service.upsert(
            {
                "kind": "blueprint",
                "idempotency_key": f"{marker}-exec-bp-{key}",
                "payload": {
                    "key": key, "name": f"槽位-{key}", "blueprint_type": "capability",
                    "primary_cluster_key": domain_key, "lifecycle": "planned",
                    "capability_contract": contract,
                    "source_refs": [{"kind": "design_document", "uri": f"reference://M4-{suffix}"}],
                },
            }
        )
        service.upsert(
            {
                "kind": "membership",
                "idempotency_key": f"{marker}-exec-mb-{key}",
                "payload": {"blueprint_key": key, "cluster_key": domain_key, "axis": "business"},
            }
        )
    service.upsert(
        {
            "kind": "edge",
            "idempotency_key": f"{marker}-exec-edge",
            "payload": {"source_blueprint_key": producer_key, "target_blueprint_key": consumer_key, "relation_type": "depends_on"},
        }
    )
    plan = service.plan(
        {
            "intent": f"M4 执行链-{suffix}",
            "idempotency_key": f"{marker}-exec-plan",
            "mode": "plan_only",
            "capability_requirements": [f"audit.it-{suffix}.produce", f"audit.it-{suffix}.consume"],
            "budget": {"max_candidates": 8, "max_chain_length": 4, "max_latency_ms": 5000},
        }
    )
    assert plan["node_count"] == 2
    chain = service.materialize_chain(
        {"plan_key": plan["plan_key"], "idempotency_key": f"{marker}-exec-materialize", "reason": "M4 集成物化"}
    )
    assert chain["intent_count"] == 2
    return chain["chain_key"], producer_key, consumer_key


# -- seeded ledger reproduction -------------------------------------------------


def test_seeded_execution_checksums_reproduce_service_formula() -> None:
    """Migration 0040 seed checksums must equal the executor's canonical digest."""
    with psycopg2.connect(DB) as connection:
        _ = _tenant(connection)
        with connection.cursor() as cur:
            cur.execute(
                """SELECT e.plan_node_slot_key, e.ordinal, e.input_refs, e.output_refs, e.output_checksum
                FROM topology.execution_ledger e
                JOIN topology.invocation_chains c ON c.id=e.chain_id AND c.tenant_id=e.tenant_id
                WHERE c.chain_key=%s AND e.mode='simulated' ORDER BY e.ordinal""",
                (_SEED_CHAIN_KEY,),
            )
            rows = cur.fetchall()
    assert len(rows) == 2
    for slot, ordinal, input_refs, output_refs, stored_checksum in rows:
        expected = node_output_checksum(
            slot_key=str(slot), ordinal=int(ordinal), version="1.0.0",
            input_refs=[str(item) for item in (input_refs or [])],
            output_refs=[str(item) for item in (output_refs or [])],
        )
        assert str(stored_checksum) == expected


def test_seeded_chain_execution_is_already_run_never_duplicates() -> None:
    service = _service()
    before = len(service.list_executions(_SEED_CHAIN_KEY))
    result = service.execute_chain(
        {"chain_key": _SEED_CHAIN_KEY, "mode": "simulated", "idempotency_key": f"seed-exec-{uuid4().hex[:8]}"}
    )
    assert result["status"] == "already_executed"
    assert result["mode"] == "simulated"
    assert result["chain_checksum"]
    assert len(service.list_executions(_SEED_CHAIN_KEY)) == before


def test_seeded_approval_and_ledger_readback() -> None:
    service = _service()
    approvals = service.list_approvals(_SEED_CHAIN_KEY)
    assert any(item["slot_key"] == "quant.research-note.draft" and item["decision"] == "approve" for item in approvals)
    # explicit large limit: the persistent test database accumulates isolated
    # ledger rows on the seeded chain across M4-M10 drills, which would
    # otherwise push the oldest simulated seed rows past the default limit;
    # simulated seeds are the two earliest rows and sort LAST under
    # started_at DESC, so the readback must span the whole ledger.  The limit
    # is raised well above observed accumulation (1148 rows after R4) rather
    # than made unbounded, so a runaway test database fails loudly instead of
    # silently masking a broken seed.
    executions = service.list_executions(_SEED_CHAIN_KEY, limit=10000)
    simulated = [item for item in executions if item["mode"] == "simulated"]
    assert [item["slot_key"] for item in simulated] == ["audit.ledger.validate", "quant.research-note.draft"]
    assert all(len(item["output_checksum"]) == 64 for item in simulated)


# -- approval state machine & fail-closed gating --------------------------------


def test_chain_execution_is_blocked_until_every_requires_approval_intent_is_approved(marker: str) -> None:
    service = _service()
    suffix = uuid4().hex[:6]
    chain_key, _, _ = _build_chain(service, marker, suffix)
    intents = service.list_intents(chain_key)
    assert all(item["policy_decision"] == "requires_approval" for item in intents)
    assert all(item["status"] == "materialized" for item in intents)

    # whole-chain gate fails closed while any intent still awaits approval
    with pytest.raises(PermissionError, match="approval pending"):
        service.execute_chain(
            {"chain_key": chain_key, "mode": "simulated", "idempotency_key": f"{marker}-exec-blocked"}
        )

    # a single human approval only unblocks after every pending slot is green
    first_slot = intents[0]["slot_key"]
    service.approve_intent(
        {
            "chain_key": chain_key, "slot_key": first_slot, "decision": "approve",
            "idempotency_key": f"{marker}-apr-1", "reason": "集成测试批准", "created_by": "u-it",
        }
    )
    with pytest.raises(PermissionError, match="approval pending"):
        service.execute_chain(
            {"chain_key": chain_key, "mode": "simulated", "idempotency_key": f"{marker}-exec-blocked-2"}
        )
    for intent in intents[1:]:
        service.approve_intent(
            {
                "chain_key": chain_key, "slot_key": intent["slot_key"], "decision": "approve",
                "idempotency_key": f"{marker}-apr-{intent['slot_key']}", "reason": "集成测试批准", "created_by": "u-it",
            }
        )

    ledger = service.execute_chain(
        {"chain_key": chain_key, "mode": "simulated", "idempotency_key": f"{marker}-exec-run"}
    )
    assert ledger["status"] == "succeeded"
    assert ledger["node_count"] == 2
    assert len(ledger["entries"]) == 2
    assert all(entry["mode"] == "simulated" for entry in ledger["entries"])
    assert all(len(entry["output_checksum"]) == 64 for entry in ledger["entries"])
    # deterministic: a second run with a new key is skipped, not duplicated
    rerun = service.execute_chain(
        {"chain_key": chain_key, "mode": "simulated", "idempotency_key": f"{marker}-exec-rerun"}
    )
    assert rerun["status"] == "already_executed"


def test_rejected_intent_freezes_denied_and_blocks_chain(marker: str) -> None:
    service = _service()
    suffix = uuid4().hex[:6]
    chain_key, _, _ = _build_chain(service, marker + "-r", suffix)
    slots = [item["slot_key"] for item in service.list_intents(chain_key)]
    rejected_slot = slots[0]
    service.approve_intent(
        {
            "chain_key": chain_key, "slot_key": rejected_slot, "decision": "reject",
            "idempotency_key": f"{marker}-rej", "reason": "证据不满足", "created_by": "u-it",
        }
    )
    intent = next(item for item in service.list_intents(chain_key) if item["slot_key"] == rejected_slot)
    assert intent["policy_decision"] == "denied"
    assert intent["status"] == "denied"
    with pytest.raises(PermissionError, match="intent denied"):
        service.execute_chain(
            {"chain_key": chain_key, "mode": "simulated", "idempotency_key": f"{marker}-exec-after-reject"}
        )


def test_approval_is_idempotent_and_terminal_states_are_frozen(marker: str) -> None:
    service = _service()
    suffix = uuid4().hex[:6]
    chain_key, _, _ = _build_chain(service, marker + "-i", suffix)
    first_slot = service.list_intents(chain_key)[0]["slot_key"]
    ikey = f"{marker}-apr-replay"
    first = service.approve_intent(
        {
            "chain_key": chain_key, "slot_key": first_slot, "decision": "approve",
            "idempotency_key": ikey, "reason": "集成幂等", "created_by": "u-it",
        }
    )
    replay = service.approve_intent(
        {
            "chain_key": chain_key, "slot_key": first_slot, "decision": "approve",
            "idempotency_key": ikey, "reason": "集成幂等", "created_by": "u-it",
        }
    )
    assert replay["idempotent"] is True
    assert replay["approval_ref"] == first["approval_ref"]
    assert replay["intent_status"] == "approved_projection"
    # already-approved intent can never flip back or be approved twice
    with pytest.raises(ValueError, match="not awaiting approval"):
        service.approve_intent(
            {
                "chain_key": chain_key, "slot_key": first_slot, "decision": "approve",
                "idempotency_key": f"{marker}-apr-again", "created_by": "u-it",
            }
        )


# -- policy gate and RLS ---------------------------------------------------------


def test_approval_and_execution_are_policy_gated() -> None:
    denied = TopologyService(DB)  # PolicyEngine() has no allow rules
    with pytest.raises(PermissionError, match="policy denied topology.chain.approve"):
        denied.approve_intent(
            {
                "chain_key": _SEED_CHAIN_KEY, "slot_key": "quant.research-note.draft", "decision": "approve",
                "idempotency_key": f"denied-{uuid4().hex[:8]}",
            }
        )
    with pytest.raises(PermissionError, match="policy denied topology.chain.execute"):
        denied.execute_chain(
            {"chain_key": _SEED_CHAIN_KEY, "mode": "simulated", "idempotency_key": f"denied-{uuid4().hex[:8]}"}
        )


def test_approval_and_ledger_rls_hide_from_other_tenant(marker: str) -> None:
    service = _service()
    suffix = uuid4().hex[:6]
    chain_key, _, _ = _build_chain(service, marker + "-rls", suffix)
    first_slot = service.list_intents(chain_key)[0]["slot_key"]
    service.approve_intent(
        {
            "chain_key": chain_key, "slot_key": first_slot, "decision": "approve",
            "idempotency_key": f"{marker}-rls-apr", "reason": "RLS", "created_by": "u-it",
        }
    )
    assert len(service.list_approvals(chain_key)) >= 1
    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        slug = f"m4-{marker}-other"
        cur.execute("INSERT INTO iam.tenants(slug,name) VALUES(%s,%s) ON CONFLICT(slug) DO NOTHING", (slug, "M4 其他租户"))
        cur.execute("SELECT id FROM iam.tenants WHERE slug=%s", (slug,))
        other_id = cur.fetchone()[0]
        cur.execute("SELECT set_config('app.tenant_id', %s, false)", (str(other_id),))
        cur.fetchone()
        cur.execute(
            """SELECT COUNT(*) FROM topology.execution_ledger e
            JOIN topology.invocation_chains c ON c.id=e.chain_id
            WHERE e.tenant_id=%s AND c.chain_key=%s""",
            (other_id, chain_key),
        )
        assert cur.fetchone()[0] == 0
        cur.execute(
            """SELECT COUNT(*) FROM topology.invocation_approvals a
            JOIN topology.invocation_chains c ON c.id=a.chain_id
            WHERE a.tenant_id=%s AND c.chain_key=%s""",
            (other_id, chain_key),
        )
        assert cur.fetchone()[0] == 0