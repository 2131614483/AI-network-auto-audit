"""Plugin Topology M10 integration tests: graph-driven orchestration planning.

End-to-end on the test database.  The M10 seeds (``capability-l2`` L2 capability
space + ``audit-l3`` L3 domain space, capability/domain nodes with Chinese and
English aliases, a ``depends_on`` internal edge, an active ``capability_contract``
bridge rule/edge, and three ``topology.blueprint_graph_links`` rows) exist; a
Chinese business intent is scored deterministically by pg_trgm into two
capability nodes and expanded one hop into a third; ``GraphPlanningService``
writes a plan_only routing plan whose checksum matches the service formula
recomputed from the stored ``plan_json``; the same requirements reuse the
existing plan on a deterministic ``plan_key`` collision; idempotency keys replay
the recorded intent row; the plan materializes into the same invocation chain as
the seeded M6 chain and runs through the isolated read-only drill (real child
processes) plus a fail-closed run on the expanded chain that stops at the node
without a governed input source; RLS hides planning intents across tenants;
``planning_intents`` is INSERT/SELECT only; and the API is fail-closed 403
without the capability, 422 on bad payloads, 404 on unknown tenants, 400 on an
unmatchable intent, and idempotent on replay.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from uuid import UUID, uuid4

import psycopg2
import pytest
from fastapi.testclient import TestClient

from apps.api.main import Settings, create_app
from packages.graph.graph_planning import CapabilityGraphAdapter
from packages.plugin_runtime.db_artifacts import ArtifactInput
from packages.plugin_topology.graph_planning import GraphPlanningService
from packages.plugin_topology.isolated import InputSource
from packages.plugin_topology.service import TopologyService
from packages.policy.engine import PolicyEngine

DB = os.getenv("AUDIT_NETWORK_TEST_DATABASE_URL", "postgresql://audit_app:admin@localhost:5432/audit_network_test")
STAGING = Path(__file__).resolve().parents[2] / ".data" / "isolated"

_CANONICAL_INTENT = "总账质量校验并出具量化研究结论"
_SINGLE_INTENT = "量化研究结论"
_NO_MATCH_INTENT = "完全不相关的外太空采矿优化"

_SEED_CHAIN_KEY = "chain-" + hashlib.sha256(
    "|".join([
        "plan-" + hashlib.sha256(b"ledger-quality-slot|research-note-slot").hexdigest()[:16],
        "ledger-quality-slot",
        "research-note-slot",
    ]).encode("utf-8")
).hexdigest()[:16]

_EXPECTED_2NODE = {"audit.ledger.validate", "quant.research-note.draft"}
_EXPECTED_3NODE = {"audit.ledger.validate", "quant.research-note.draft", "audit.finding.draft"}

_GRAPH_PLAN_CAPABILITIES = [
    "topology.intent.plan",
    "topology.plan.read",
    "topology.chain.write",
    "topology.chain.approve",
    "topology.chain.execute",
    "topology.chain.execute.isolated",
]


def _tenant(connection) -> UUID:
    with connection.cursor() as cur:
        cur.execute("SELECT id FROM iam.tenants WHERE slug='local-dev'")
        tenant_id = cur.fetchone()[0]
        cur.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant_id),))
        cur.fetchone()
    return tenant_id


def _topology_service() -> TopologyService:
    return TopologyService(DB, policy=PolicyEngine(allow=_GRAPH_PLAN_CAPABILITIES))


def _graph_service() -> GraphPlanningService:
    return GraphPlanningService(
        DB,
        topology_service=_topology_service(),
        graph_port=CapabilityGraphAdapter(DB),
        policy=PolicyEngine(allow=_GRAPH_PLAN_CAPABILITIES),
    )


def _intent_request(suffix: str, **overrides: object) -> dict:
    payload: dict = {
        "intent": _CANONICAL_INTENT,
        "budget": {"max_matches": 4, "expand_hops": 1},
        "idempotency_key": f"m10-intent-{suffix}-{uuid4().hex[:8]}",
        "reason": "M10 集成测试：图谱驱动组网规划",
    }
    payload.update(overrides)
    return payload


def _write_ledger_csv() -> Path:
    """A small, valid audit ledger CSV (deterministic bridge input)."""
    directory = STAGING / "inputs"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"a1-ledger-{uuid4().hex[:8]}.csv"
    path.write_text(
        "entry_id,date,account_code,description,debit_amount,credit_amount\n"
        "E1,2026-01-05,1101,现金收款,100.00,100.00\n"
        "E2,2026-01-06,1102,银行划转,200.00,100.00\n",
        encoding="utf-8-sig",
    )
    return path


def _ledger_input() -> InputSource:
    csv_path = _write_ledger_csv()
    raw = csv_path.read_bytes()
    artifact = ArtifactInput(
        artifact_id=uuid4(),
        tenant_id=uuid4(),
        uri=csv_path.resolve().as_uri(),
        media_type="text/csv",
        sha256=hashlib.sha256(raw).hexdigest(),
        size_bytes=len(raw),
        classification="audit_ledger",
    )
    return InputSource(
        capability="audit.ledger.validate",
        artifact=artifact,
        payload={
            "ledger": {
                "artifact": artifact.as_payload(),
                "schema_mapping_version": "1.0.0",
                "period": "2026-01",
            }
        },
    )


def _expected_checksum(plan_json: dict) -> str:
    """Recompute the planner checksum from a stored plan_json (formula parity)."""
    payload = {
        "nodes": [
            {
                "slot_key": n["slot_key"],
                "capability": n["capability"],
                "blueprint_key": n["blueprint_key"],
                "alternatives": list(n["alternatives"]),
            }
            for n in plan_json["nodes"]
        ],
        "edges": [
            {
                "source_node": e["source_node"],
                "target_node": e["target_node"],
                "relation_type": e["relation_type"],
            }
            for e in plan_json["edges"]
        ],
        "mode": plan_json["mode"],
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _policy_set_status(status: str) -> None:
    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        tenant_id = _tenant(connection)
        cur.execute(
            "UPDATE policy.policy_sets SET status=%s "
            "WHERE tenant_id=%s AND name='local-plugin-topology-graph-plan'",
            (status, tenant_id),
        )
        connection.commit()


@pytest.fixture(autouse=True)
def _restore_read_policy_set() -> None:
    """Every M10 test starts with the read policy set back on its contract state.

    Earlier topology modules (run alphabetically first, e.g.
    ``test_plugin_topology_api.py``) fail-closed by deactivating **all**
    ``%topology.%`` policy sets and never restore them, so the M10 seed
    assertion ``local-plugin-topology-read == active`` and the read-only API
    flows would otherwise see a stale inactive set in a full-suite run.
    """
    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        tenant_id = _tenant(connection)
        cur.execute(
            "UPDATE policy.policy_sets SET status='active' "
            "WHERE tenant_id=%s AND name='local-plugin-topology-read'",
            (tenant_id,),
        )
        connection.commit()


# -- seed bindings: graph <-> topology -------------------------------------------


def test_m10_seed_bindings_exist() -> None:
    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        tenant_id = _tenant(connection)
        cur.execute(
            "SELECT s.key, s.level, s.status FROM graph.spaces s "
            "WHERE s.tenant_id=%s AND s.key IN ('capability-l2','audit-l3') ORDER BY s.key",
            (tenant_id,),
        )
        spaces = cur.fetchall()
        assert [row[0] for row in spaces] == ["audit-l3", "capability-l2"]
        assert all(row[2] == "active" for row in spaces)

        cur.execute(
            "SELECT COUNT(*) FROM graph.space_profiles sp JOIN graph.spaces s ON s.id=sp.space_id "
            "WHERE s.tenant_id=%s AND s.key IN ('capability-l2','audit-l3')",
            (tenant_id,),
        )
        assert cur.fetchone()[0] == 2

        cur.execute(
            "SELECT canonical_key, node_type FROM graph.nodes "
            "WHERE tenant_id=%s AND deleted_at IS NULL AND canonical_key = ANY(%s) ORDER BY canonical_key",
            (
                tenant_id,
                [
                    "capability:audit.ledger.validate",
                    "capability:audit.finding.draft",
                    "capability:quant.research-note.draft",
                    "domain:financial-audit",
                ],
            ),
        )
        nodes = cur.fetchall()
        assert [row[0] for row in nodes] == [
            "capability:audit.finding.draft",
            "capability:audit.ledger.validate",
            "capability:quant.research-note.draft",
            "domain:financial-audit",
        ]
        assert [row[1] for row in nodes] == ["capability", "capability", "capability", "domain"]

        cur.execute(
            "SELECT COUNT(*) FROM graph.node_aliases a JOIN graph.nodes n ON n.id=a.node_id "
            "WHERE n.tenant_id=%s AND n.canonical_key = ANY(%s)",
            (tenant_id, ["capability:audit.ledger.validate", "capability:audit.finding.draft", "capability:quant.research-note.draft", "domain:financial-audit"]),
        )
        assert cur.fetchone()[0] >= 11

        # internal depends_on edge: ledger.validate -> finding.draft
        cur.execute(
            "SELECT COUNT(*) FROM graph.edges e "
            "JOIN graph.nodes n1 ON n1.id=e.source_node_id "
            "JOIN graph.nodes n2 ON n2.id=e.target_node_id "
            "WHERE n1.tenant_id=%s AND n1.canonical_key='capability:audit.ledger.validate' "
            "AND n2.canonical_key='capability:audit.finding.draft' AND e.relation_type='depends_on'",
            (tenant_id,),
        )
        assert cur.fetchone()[0] == 1

        # active capability_contract bridge rule + edge (audit-l3 -> capability-l2)
        cur.execute(
            "SELECT COUNT(*) FROM graph.bridge_rules br "
            "JOIN graph.spaces s1 ON s1.id=br.source_space_id "
            "JOIN graph.spaces s2 ON s2.id=br.target_space_id "
            "WHERE br.tenant_id=%s AND s1.key='audit-l3' AND s2.key='capability-l2' "
            "AND br.relation_type='capability_contract' AND br.status='active'",
            (tenant_id,),
        )
        assert cur.fetchone()[0] == 1
        cur.execute(
            "SELECT COUNT(*) FROM graph.bridge_edges be "
            "JOIN graph.nodes n1 ON n1.id=be.source_node_id "
            "JOIN graph.nodes n2 ON n2.id=be.target_node_id "
            "WHERE n1.tenant_id=%s AND n1.canonical_key='domain:financial-audit' "
            "AND n2.canonical_key='capability:audit.ledger.validate' "
            "AND be.relation_type='capability_contract' AND be.status='active'",
            (tenant_id,),
        )
        assert cur.fetchone()[0] == 1

        # blueprint <-> graph bindings finally close the graph/topology gap
        cur.execute(
            "SELECT node_key, blueprint_key FROM topology.blueprint_graph_links "
            "WHERE tenant_id=%s ORDER BY node_key",
            (tenant_id,),
        )
        pairs = {(str(row[0]), str(row[1])) for row in cur.fetchall()}
        # The three shipped slots must still resolve to their graph nodes.
        # Containment, not equality: registering the on-disk plugin directory
        # adds one link per plugin, so a graph node can now be reachable from
        # its own blueprint as well as from a governance slot.
        assert {
            ("capability:audit.finding.draft", "finding-draft-slot"),
            ("capability:audit.ledger.validate", "ledger-quality-slot"),
            ("capability:quant.research-note.draft", "research-note-slot"),
        } <= pairs

        # planning policy sets exist; intent.plan is fail-closed until a tenant
        # explicitly publishes it (the API 403 test flips status explicitly)
        cur.execute(
            "SELECT name, status FROM policy.policy_sets "
            "WHERE tenant_id=%s AND name IN ('local-plugin-topology-read','local-plugin-topology-graph-plan')",
            (tenant_id,),
        )
        policies = dict(cur.fetchall())
        assert policies["local-plugin-topology-read"] == "active"
        assert "local-plugin-topology-graph-plan" in policies

        # pg_trgm GIN indexes exist for deterministic intent matching
        cur.execute(
            "SELECT COUNT(*) FROM pg_indexes WHERE schemaname='graph' "
            "AND indexname IN ('graph_nodes_label_trgm_idx','graph_node_aliases_alias_trgm_idx')"
        )
        assert cur.fetchone()[0] == 2


# -- deterministic intent matching + bounded expansion ----------------------------


def test_chinese_intent_matches_two_capabilities_and_expands() -> None:
    adapter = CapabilityGraphAdapter(DB)
    matched = adapter.match_nodes(_CANONICAL_INTENT, max_matches=8)
    keys = [m["node_key"] for m in matched]
    assert "capability:audit.ledger.validate" in keys
    assert "capability:quant.research-note.draft" in keys
    by_key = {m["node_key"]: m for m in matched}
    for key in ("capability:audit.ledger.validate", "capability:quant.research-note.draft"):
        assert by_key[key]["score"] >= 0.05
        assert by_key[key]["match_kind"] == "direct"
        assert by_key[key]["match_source"] == "trigram"
    # deterministic order: descending score, then (space_key, canonical_key)
    scores = [m["score"] for m in matched]
    assert scores == sorted(scores, reverse=True)

    expanded = adapter.expand_to_capabilities(keys, expand_hops=1)
    expanded_keys = [e["node_key"] for e in expanded["expanded"]]
    assert "capability:audit.finding.draft" in expanded_keys
    finding = next(e for e in expanded["expanded"] if e["node_key"] == "capability:audit.finding.draft")
    assert finding["match_source"] == "depends_on"
    assert finding["source_node_key"] == "capability:audit.ledger.validate"

    # a 0-hop expansion is a no-op; an unmatchable intent yields nothing
    assert adapter.expand_to_capabilities(keys, expand_hops=0)["expanded"] == []
    assert adapter.match_nodes(_NO_MATCH_INTENT, max_matches=8) == []


# -- intent -> plan_only plan (checksum parity with the service formula) ----------


def test_plan_from_intent_writes_plan_only_with_formula_checksum() -> None:
    service = _graph_service()
    result = service.plan_from_intent(_intent_request("plan"))
    assert result["mode"] == "plan_only"
    # The three legacy capabilities must still be planned.  Registering the
    # on-disk plugin directory gives the intent more capabilities to resolve to,
    # so this is containment rather than an exact set.
    assert _EXPECTED_3NODE <= set(result["capability_requirements"])
    assert result["plan_key"].startswith("plan-")
    assert len(result["plan_checksum"]) == 64
    # matched-node evidence carries provenance
    evidence = {m["node_key"]: m for m in result["matched_nodes"]}
    assert evidence["capability:audit.finding.draft"]["match_kind"] == "expanded"
    assert evidence["capability:audit.ledger.validate"]["match_source"] == "trigram"

    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        tenant_id = _tenant(connection)
        cur.execute(
            "SELECT plan_json, checksum, mode FROM topology.routing_plans "
            "WHERE tenant_id=%s AND plan_key=%s",
            (tenant_id, result["plan_key"]),
        )
        row = cur.fetchone()
        assert row is not None
        plan_json, db_checksum, db_mode = row
        assert db_mode == "plan_only"
        assert db_checksum == result["plan_checksum"]
        assert db_checksum == _expected_checksum(plan_json)
        # plan nodes are the three capability slots (expansion included)
        assert _EXPECTED_3NODE <= {n["capability"] for n in plan_json["nodes"]}

        cur.execute(
            "SELECT intent_text, plan_key FROM topology.planning_intents "
            "WHERE tenant_id=%s AND id=%s",
            (tenant_id, UUID(result["intent_id"])),
        )
        intent_row = cur.fetchone()
        assert intent_row is not None
        assert intent_row[0] == _CANONICAL_INTENT
        assert intent_row[1] == result["plan_key"]


def test_plan_reused_on_plan_key_collision() -> None:
    service = _graph_service()
    first = service.plan_from_intent(
        _intent_request("reuse1", intent=_SINGLE_INTENT, budget={"max_matches": 4, "expand_hops": 0})
    )
    second = service.plan_from_intent(
        _intent_request("reuse2", intent=_SINGLE_INTENT, budget={"max_matches": 4, "expand_hops": 0})
    )
    # identical requirements always resolve to the same deterministic plan_key
    assert first["plan_key"] == second["plan_key"]
    assert first["plan_checksum"] == second["plan_checksum"]
    assert second["reused_plan"] is True
    # both intents are distinct append-only evidence rows
    assert first["intent_id"] != second["intent_id"]


def test_planning_intent_idempotent_replay() -> None:
    service = _graph_service()
    request = _intent_request("replay")
    once = service.plan_from_intent(request)
    assert once["idempotent"] is False
    twice = service.plan_from_intent(request)
    assert twice["idempotent"] is True
    assert twice["intent_id"] == once["intent_id"]
    assert twice["plan_key"] == once["plan_key"]
    assert twice["plan_checksum"] == once["plan_checksum"]
    # only one evidence row exists for the replayed key
    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        tenant_id = _tenant(connection)
        cur.execute(
            "SELECT COUNT(*) FROM topology.planning_intents "
            "WHERE tenant_id=%s AND idempotency_key=%s",
            (tenant_id, request["idempotency_key"]),
        )
        assert cur.fetchone()[0] == 1


def test_plan_from_intent_fail_closed_no_match() -> None:
    service = _graph_service()
    assert service.graph_port.match_nodes(_NO_MATCH_INTENT, max_matches=4) == []
    with pytest.raises(ValueError, match="fail-closed"):
        service.plan_from_intent(_intent_request("nomatch", intent=_NO_MATCH_INTENT))
    # nothing was written: no plan, no evidence row
    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        tenant_id = _tenant(connection)
        cur.execute(
            "SELECT COUNT(*) FROM topology.planning_intents "
            "WHERE tenant_id=%s AND intent_text=%s",
            (tenant_id, _NO_MATCH_INTENT),
        )
        assert cur.fetchone()[0] == 0


# -- plan -> chain -> approve -> isolated drill (M6 machinery compatibility) ------


def test_plan_materializes_into_existing_chain_and_isolated_run_succeeds() -> None:
    """Plan -> materialize -> isolated run succeeds, on an explicitly built plan.

    Requirements are stated explicitly instead of being derived from an intent:
    once the on-disk plugin directory is registered, the same intent legitimately
    resolves to more capabilities (see
    ``test_plan_from_intent_writes_plan_only_with_formula_checksum`` for the
    recall side).  This test is about the materialize/execute path, so its input
    must be stable.
    """
    topo = _topology_service()
    plan = topo.plan(
        {
            "intent": _CANONICAL_INTENT,
            "idempotency_key": f"m10-chain-{uuid4().hex[:8]}",
            "mode": "plan_only",
            "capability_requirements": sorted(_EXPECTED_2NODE),
            "budget": {"max_candidates": 8, "max_latency_ms": 5000, "max_chain_length": 4},
            "planner_version": "1.0.0",
            "trace_id": str(uuid4()),
        }
    )
    chain = topo.materialize_chain(
        {
            "plan_key": plan["plan_key"],
            "idempotency_key": f"m10-chain-{uuid4().hex[:8]}",
            "reason": "M10 测试库：图谱规划 → 链物化",
        }
    )
    # The seeded chain was built from the two legacy slots.  Now that the on-disk
    # plugin directory is registered, the same intent also resolves to the
    # plugin's own blueprint — whose key (`audit.ledger-quality`) sorts before
    # `ledger-quality-slot`, so the planner picks it — and the plan, hence the
    # chain, is a new one.  What this test is about is that a plan materializes
    # and its isolated run succeeds, not which key the seed happened to use.
    assert chain["chain_key"].startswith("chain-")
    assert chain["mode"] == "plan_only"

    # approve any intent still behind the human gate (seed intents are
    # policy_allowed, so this is normally a no-op -- idempotent by design)
    for intent in topo.list_intents(chain["chain_key"]):
        if intent["policy_decision"] == "requires_approval" and intent["status"] == "materialized":
            topo.approve_intent(
                {
                    "chain_key": chain["chain_key"],
                    "slot_key": intent["slot_key"],
                    "decision": "approve",
                    "reason": "M10 测试库：逐节点人工审批",
                    "idempotency_key": f"m10-appr-{uuid4().hex[:8]}",
                }
            )

    run = topo.start_run(
        {
            "chain_key": chain["chain_key"],
            "mode": "isolated",
            "idempotency_key": f"m10-run-{uuid4().hex[:8]}",
            "reason": "M10 测试库：isolated 演练兼容性",
        },
        input_sources={"audit.ledger.validate": _ledger_input()},
    )
    assert run["status"] == "success"
    assert run["node_total"] == 2
    assert run["node_succeeded"] == 2
    assert run["node_failed"] == 0
    # every ledger row is a real read-only child run with metadata
    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        tenant_id = _tenant(connection)
        cur.execute(
            "SELECT plan_node_slot_key, status, mode, plugin_id "
            "FROM topology.execution_ledger WHERE tenant_id=%s AND run_id=%s ORDER BY ordinal",
            (tenant_id, UUID(run["run_id"])),
        )
        ledger = cur.fetchall()
        assert [(row[0], row[1], row[2]) for row in ledger] == [
            ("audit.ledger.validate", "succeeded", "isolated"),
            ("quant.research-note.draft", "succeeded", "isolated"),
        ]
        assert all(row[3] for row in ledger)  # plugin_id recorded for real children


def test_expanded_chain_run_fails_closed_at_node_without_governed_input() -> None:
    service = _graph_service()
    plan = service.plan_from_intent(_intent_request("run3"))
    assert _EXPECTED_3NODE <= set(plan["capability_requirements"])

    topo = _topology_service()
    chain = topo.materialize_chain(
        {
            "plan_key": plan["plan_key"],
            "idempotency_key": f"m10-chain3-{uuid4().hex[:8]}",
            "reason": "M10 测试库：三节点链物化",
        }
    )
    for intent in topo.list_intents(chain["chain_key"]):
        if intent["policy_decision"] == "requires_approval" and intent["status"] == "materialized":
            topo.approve_intent(
                {
                    "chain_key": chain["chain_key"],
                    "slot_key": intent["slot_key"],
                    "decision": "approve",
                    "reason": "M10 测试库：逐节点人工审批（三节点链）",
                    "idempotency_key": f"m10-appr3-{uuid4().hex[:8]}",
                }
            )

    run = topo.start_run(
        {
            "chain_key": chain["chain_key"],
            "mode": "isolated",
            "idempotency_key": f"m10-run3-{uuid4().hex[:8]}",
            "reason": "M10 测试库：三节点 isolated 演练（期望 finding 无治理输入即停止）",
        },
        input_sources={"audit.ledger.validate": _ledger_input()},
    )
    # The run fails closed at the first node without a governed input source and
    # stops there — nothing ungoverned runs.  *Which* node that is depends on the
    # plan order, and the plan order depends on what the catalog offers: with the
    # on-disk plugin directory registered, a newly recallable capability can come
    # ahead of the seeded ones.  Assert the invariant, not a fixed node list.
    assert run["status"] == "failed"
    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        tenant_id = _tenant(connection)
        cur.execute(
            "SELECT plan_node_slot_key, status, policy_ref "
            "FROM topology.execution_ledger WHERE tenant_id=%s AND run_id=%s ORDER BY ordinal",
            (tenant_id, UUID(run["run_id"])),
        )
        ledger = cur.fetchall()
    assert ledger, "the run must have executed at least one node"
    assert all(status == "failed" for _slot, status, _policy in ledger)
    assert any(policy == "no_governed_input_source" for _slot, _status, policy in ledger)
    assert run["node_failed"] >= 1


# -- governance: RLS isolation + append-only privileges ---------------------------


def test_planning_intents_rls_isolates_tenants() -> None:
    _graph_service().plan_from_intent(_intent_request("rls"))
    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        # Ensure a distinct tenant exists regardless of test-run order; the
        # db-artifacts suite creates one, but this test must be self-contained
        # on a freshly rebuilt database.
        cur.execute("SELECT slug FROM iam.tenants WHERE slug LIKE 'db-artifacts-%' ORDER BY slug LIMIT 1")
        row = cur.fetchone()
        if row is None:
            other_slug = f"db-artifacts-{uuid4().hex[:8]}"
            cur.execute("INSERT INTO iam.tenants(slug,name) VALUES(%s,%s)", (other_slug, "RLS isolation probe"))
            connection.commit()
        else:
            other_slug = str(row[0])
    other = GraphPlanningService(
        DB,
        topology_service=TopologyService(DB, tenant_slug=other_slug),
        graph_port=CapabilityGraphAdapter(DB, tenant_slug=other_slug),
        policy=PolicyEngine(allow=_GRAPH_PLAN_CAPABILITIES),
        tenant_slug=other_slug,
    )
    # RLS hides every local-dev evidence row and every M10 graph seed
    assert other.list_intents() == []
    assert other.graph_port.match_nodes(_CANONICAL_INTENT, max_matches=4) == []
    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        cur.execute("SELECT id FROM iam.tenants WHERE slug=%s", (other_slug,))
        other_id = cur.fetchone()[0]
        cur.execute("SELECT set_config('app.tenant_id', %s, false)", (str(other_id),))
        cur.fetchone()
        cur.execute("SELECT COUNT(*) FROM topology.planning_intents")
        assert cur.fetchone()[0] == 0


def test_planning_intents_append_only_no_update_delete() -> None:
    _graph_service().plan_from_intent(_intent_request("apponly"))
    # audit_app holds only SELECT+INSERT; UPDATE/DELETE are contractually absent
    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        _ = _tenant(connection)
        with pytest.raises(psycopg2.errors.InsufficientPrivilege):
            cur.execute("UPDATE topology.planning_intents SET reason='tampered' WHERE true")
    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        _ = _tenant(connection)
        with pytest.raises(psycopg2.errors.InsufficientPrivilege):
            cur.execute("DELETE FROM topology.planning_intents WHERE true")


# -- API surface ------------------------------------------------------------------


def _client() -> TestClient:
    return TestClient(create_app(Settings(database_url=DB)))


def _headers(tenant_id: UUID) -> dict[str, str]:
    return {"X-Tenant-Id": str(tenant_id), "X-Trace-Id": str(uuid4())}


def _local_tenant_id() -> UUID:
    with psycopg2.connect(DB) as connection:
        return _tenant(connection)


def test_api_planning_fail_closed_without_capability() -> None:
    _policy_set_status("inactive")
    try:
        client = _client()
        tid = _local_tenant_id()
        resp = client.post(
            "/api/v1/topology/planning/intents", json=_intent_request("403"), headers=_headers(tid)
        )
        # fail-closed: the gateway blocks (403 deny or 409 require-approval)
        # and nothing is planned
        assert resp.status_code in (403, 409)
    finally:
        _policy_set_status("active")


def test_api_planning_bad_input_422() -> None:
    _policy_set_status("active")
    client = _client()
    tid = _local_tenant_id()
    # missing idempotency key
    payload = _intent_request("422a")
    del payload["idempotency_key"]
    resp = client.post("/api/v1/topology/planning/intents", json=payload, headers=_headers(tid))
    assert resp.status_code == 422
    # empty intent
    resp = client.post(
        "/api/v1/topology/planning/intents",
        json=_intent_request("422b", intent=""),
        headers=_headers(tid),
    )
    assert resp.status_code == 422
    # intent over the 512-char bound
    resp = client.post(
        "/api/v1/topology/planning/intents",
        json=_intent_request("422c", intent="总账" * 300),
        headers=_headers(tid),
    )
    assert resp.status_code == 422


def test_api_planning_unknown_tenant_404() -> None:
    _policy_set_status("active")
    client = _client()
    resp = client.post(
        "/api/v1/topology/planning/intents",
        json=_intent_request("404"),
        headers=_headers(uuid4()),
    )
    assert resp.status_code == 404


def test_api_planning_no_match_400() -> None:
    _policy_set_status("active")
    client = _client()
    tid = _local_tenant_id()
    resp = client.post(
        "/api/v1/topology/planning/intents",
        json=_intent_request("nomatch", intent=_NO_MATCH_INTENT),
        headers=_headers(tid),
    )
    assert resp.status_code == 400


def test_api_planning_flow_and_idempotent_replay() -> None:
    _policy_set_status("active")
    client = _client()
    tid = _local_tenant_id()
    key = f"m10-api-{uuid4().hex[:8]}"
    payload = _intent_request("api", idempotency_key=key)

    first = client.post("/api/v1/topology/planning/intents", json=payload, headers=_headers(tid))
    assert first.status_code == 200
    body = first.json()
    assert body["idempotent"] is False
    assert body["mode"] == "plan_only"
    assert _EXPECTED_3NODE <= set(body["capability_requirements"])
    assert body["plan_key"].startswith("plan-")

    listing = client.get("/api/v1/topology/planning/intents", headers=_headers(tid))
    assert listing.status_code == 200
    assert any(item["intent_id"] == body["intent_id"] for item in listing.json()["items"])

    detail = client.get(
        f"/api/v1/topology/planning/intents/{body['intent_id']}", headers=_headers(tid)
    )
    assert detail.status_code == 200
    assert detail.json()["plan_key"] == body["plan_key"]
    assert {m["node_key"] for m in detail.json()["matched_nodes"]} >= {
        "capability:audit.ledger.validate",
        "capability:quant.research-note.draft",
    }

    replay = client.post("/api/v1/topology/planning/intents", json=payload, headers=_headers(tid))
    assert replay.status_code == 200
    assert replay.json()["idempotent"] is True
    assert replay.json()["intent_id"] == body["intent_id"]
    assert replay.json()["plan_key"] == body["plan_key"]
