"""Plugin Topology M3 integration tests against the isolated test database.

Covers chain materialization determinism (re-materializing the migration-seeded
plan reproduces the canonical chain_key and checksum), idempotency at both the
key and the plan level, seeded read-back, RLS tenant isolation and the write
policy gate.  Nothing here executes a plugin; chains and intents are status
projections only.
"""

from __future__ import annotations

import hashlib
import os
from uuid import UUID, uuid4

import psycopg2
import pytest

from packages.plugin_topology.service import TopologyService
from packages.policy.engine import PolicyEngine

DB = os.getenv("AUDIT_NETWORK_TEST_DATABASE_URL", "postgresql://audit_app:admin@localhost:5432/audit_network_test")

_SEED_PLAN_KEY = "plan-" + hashlib.sha256(b"ledger-quality-slot|research-note-slot").hexdigest()[:16]
_SEED_CHAIN_KEY = "chain-" + hashlib.sha256(
    "|".join([_SEED_PLAN_KEY, "ledger-quality-slot", "research-note-slot"]).encode("utf-8")
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
        policy=PolicyEngine(
            allow=[
                "topology.cluster.write",
                "topology.blueprint.write",
                "topology.membership.write",
                "topology.edge.write",
                "topology.plan.read",
                "topology.chain.write",
            ]
        ),
    )


@pytest.fixture(scope="module")
def marker() -> str:
    return uuid4().hex[:8]


# -- seed reproduction ---------------------------------------------------------


def test_seeded_plan_rematerializes_to_canonical_chain() -> None:
    """Materializing the seeded plan yields the migration's exact chain identity."""
    service = _service()
    result = service.materialize_chain(
        {"plan_key": _SEED_PLAN_KEY, "idempotency_key": f"seed-repro-{uuid4().hex[:8]}", "reason": "M3 种子重建校验"}
    )
    assert result["chain_key"] == _SEED_CHAIN_KEY
    assert result["mode"] == "plan_only"
    assert result["release_locked"] is True
    # the chain already exists from the migration, so the same plan must never
    # create a second, divergent chain (determinism, not duplication)
    assert result["already_materialized"] is True


def test_seeded_plan_chain_checksum_is_stable_across_calls() -> None:
    service = _service()
    first = service.materialize_chain(
        {"plan_key": _SEED_PLAN_KEY, "idempotency_key": f"seed-stable-{uuid4().hex[:8]}"}
    )
    second = service.materialize_chain(
        {"plan_key": _SEED_PLAN_KEY, "idempotency_key": f"seed-stable-{uuid4().hex[:8]}"}
    )
    assert len(first["checksum"]) == 64
    assert first["checksum"] == second["checksum"]


# -- idempotency ----------------------------------------------------------------


def test_chain_materialize_is_key_idempotent_and_plan_deterministic(marker: str) -> None:
    service = _service()
    suffix = uuid4().hex[:6]
    domain_key = f"it-{marker}-chain-domain"
    producer_key = f"it-{marker}-ch-{suffix}-producer"
    consumer_key = f"it-{marker}-ch-{suffix}-consumer"
    shared = f"it-{suffix}-shared-contract"

    service.upsert(
        {
            "kind": "cluster",
            "idempotency_key": f"{marker}-ch-cluster",
            "payload": {
                "key": domain_key, "name": "M3 链测试域", "axis": "business_domain", "layer": "L0",
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
                "idempotency_key": f"{marker}-ch-bp-{key}",
                "payload": {
                    "key": key, "name": f"槽位-{key}", "blueprint_type": "capability",
                    "primary_cluster_key": domain_key, "lifecycle": "planned",
                    "capability_contract": contract,
                    "source_refs": [{"kind": "design_document", "uri": f"reference://M3-{suffix}"}],
                },
            }
        )
        service.upsert(
            {
                "kind": "membership",
                "idempotency_key": f"{marker}-ch-mb-{key}",
                "payload": {"blueprint_key": key, "cluster_key": domain_key, "axis": "business"},
            }
        )
    service.upsert(
        {
            "kind": "edge",
            "idempotency_key": f"{marker}-ch-edge",
            "payload": {"source_blueprint_key": producer_key, "target_blueprint_key": consumer_key, "relation_type": "depends_on"},
        }
    )

    plan = service.plan(
        {
            "intent": f"M3 链集成测试-{suffix}",
            "idempotency_key": f"{marker}-ch-plan",
            "mode": "plan_only",
            "capability_requirements": [f"audit.it-{suffix}.produce", f"audit.it-{suffix}.consume"],
            "budget": {"max_candidates": 8, "max_chain_length": 4, "max_latency_ms": 5000},
        }
    )
    assert plan["node_count"] == 2

    ikey = f"{marker}-ch-materialize"
    first = service.materialize_chain(
        {"plan_key": plan["plan_key"], "idempotency_key": ikey, "reason": "M3 集成物化"}
    )
    assert first["idempotent"] is False
    assert first["mode"] == "plan_only"
    assert first["node_count"] == 2
    assert first["binding_count"] == 1
    assert first["intent_count"] == 2

    # same key -> recorded replay
    replay = service.materialize_chain(
        {"plan_key": plan["plan_key"], "idempotency_key": ikey, "reason": "M3 集成物化"}
    )
    assert replay["idempotent"] is True
    assert replay["chain_key"] == first["chain_key"]

    # different key, same locked plan -> deterministic same chain, not a duplicate
    other = service.materialize_chain(
        {"plan_key": plan["plan_key"], "idempotency_key": f"{marker}-ch-materialize-2", "reason": "M3 重放"}
    )
    assert other["already_materialized"] is True
    assert other["chain_key"] == first["chain_key"]
    assert other["checksum"] == first["checksum"]
    assert len(first["checksum"]) == 64


# -- seeded read-back ------------------------------------------------------------


def test_list_chains_and_intents_returns_seeded_rows() -> None:
    service = _service()
    # 列表按 created_at DESC 分页，测试库累积的临时链会挤掉最老的种子链；
    # 种子链是迁移 0040 的物化基座，用足够大的 limit 验证其仍可枚举。
    chains = service.list_chains(limit=500)
    assert any(item["chain_key"] == _SEED_CHAIN_KEY for item in chains)
    seeded = next(item for item in chains if item["chain_key"] == _SEED_CHAIN_KEY)
    assert seeded["mode"] == "plan_only"
    assert len(seeded["checksum"]) == 64

    intents = service.list_intents(_SEED_CHAIN_KEY)
    assert len(intents) == 2
    ledger = next(item for item in intents if item["slot_key"] == "audit.ledger.validate")
    research = next(item for item in intents if item["slot_key"] == "quant.research-note.draft")
    assert ledger["expected_outputs"] == ["audit-quality-candidates"]
    # the consumer slot's expected inputs are bound exactly to the bridge ref
    assert research["expected_inputs"] == ["contracts/jsonschema/audit-quality-candidates@1"]
    for intent in intents:
        assert intent["status"] in {"materialized", "policy_allowed", "approved_projection", "denied"}
        assert intent["policy_decision"] in {"allowed", "requires_approval", "denied"}


def test_list_intents_unknown_chain_returns_empty() -> None:
    service = _service()
    assert service.list_intents("chain-" + "f" * 16) == []


def test_idempotent_replay_ignores_gateway_decision_rows() -> None:
    """A gateway decision row (response_status=200) must never be replayed as a
    business response; only the service's own 201 rows carry chain payloads."""
    service = _service()
    ikey = f"m3-type-conf-{uuid4().hex[:8]}"
    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        tenant_id = _tenant(connection)
        cur.execute(
            """INSERT INTO control.idempotency_records
            (tenant_id,idempotency_key,request_hash,response_status,response_json)
            VALUES(%s,%s,%s,200,%s)
            ON CONFLICT (tenant_id,idempotency_key) DO UPDATE
              SET response_status=200,response_json=EXCLUDED.response_json""",
            (tenant_id, ikey, "a" * 64, psycopg2.extras.Json({"decision": "ALLOW", "trace_id": str(uuid4())})),
        )
    result = service.materialize_chain({"plan_key": _SEED_PLAN_KEY, "idempotency_key": ikey})
    assert "decision" not in result
    assert result["chain_key"] == _SEED_CHAIN_KEY
    assert result["already_materialized"] is True


# -- policy gate and RLS ---------------------------------------------------------


def test_chain_write_is_policy_gated() -> None:
    denied = TopologyService(DB)  # PolicyEngine() has no allow rules
    with pytest.raises(PermissionError, match="policy denied topology.chain.write"):
        denied.materialize_chain(
            {"plan_key": _SEED_PLAN_KEY, "idempotency_key": f"denied-{uuid4().hex[:8]}"}
        )


def test_chain_rls_hides_from_other_tenant() -> None:
    with psycopg2.connect(DB) as connection:
        with connection.cursor() as cur:
            slug = f"m3-other-{uuid4().hex[:6]}"
            cur.execute("INSERT INTO iam.tenants(slug,name) VALUES(%s,%s) ON CONFLICT(slug) DO NOTHING", (slug, "M3 其他租户"))
            cur.execute("SELECT id FROM iam.tenants WHERE slug=%s", (slug,))
            other_id = cur.fetchone()[0]
            cur.execute("SELECT set_config('app.tenant_id', %s, false)", (str(other_id),))
            cur.execute("SELECT count(*) FROM topology.invocation_chains WHERE chain_key=%s", (_SEED_CHAIN_KEY,))
            assert cur.fetchone()[0] == 0
            cur.execute("SELECT count(*) FROM topology.invocation_intents")
            assert cur.fetchone()[0] == 0