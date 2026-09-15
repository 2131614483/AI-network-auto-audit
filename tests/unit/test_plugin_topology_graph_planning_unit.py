"""Plugin Topology M10 unit tests: graph-driven planning guard rails (zero-DB).

The graph planner is a deterministic intent->plan_only pipeline; its guard
branches (contract validation, budget bounds, requirement resolution,
deterministic ordering, plan_key reuse on collision, and the fail-closed
no-match rejection) are unit-covered with a scripted cursor and stubbed
``TopologyService``/graph port -- nothing here opens a real database or starts
a child process.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import patch
from uuid import uuid4

import pytest
from psycopg2.errors import UniqueViolation

from packages.graph.graph_planning import CapabilityGraphAdapter
from packages.plugin_topology.contracts import (
    validate_planning_intent,
    validate_planning_intent_result,
)
from packages.plugin_topology.graph_planning import GraphPlanningService
from packages.policy.engine import PolicyEngine


class _FakeCur:
    """Scripted cursor: fetchone/fetchall consume rows from per-query queues."""

    def __init__(self, *result_sets: list[tuple[Any, ...]]) -> None:
        self._queues = list(result_sets)
        self.calls: list[str] = []

    def execute(self, statement: str, _params: tuple[Any, ...] | None = None) -> None:
        self.calls.append(statement)

    def fetchone(self) -> tuple[Any, ...] | None:
        if not self._queues:
            return None
        rows = self._queues.pop(0)
        return rows[0] if rows else None

    def fetchall(self) -> list[tuple[Any, ...]]:
        if not self._queues:
            return []
        return self._queues.pop(0)

    def __enter__(self) -> "_FakeCur":
        return self

    def __exit__(self, *_exc: Any) -> None:
        return None


class _FakeConnection:
    """psycopg2-connection stand-in usable as a context manager."""

    def __init__(self, cursor: _FakeCur) -> None:
        self._cursor = cursor

    def cursor(self) -> _FakeCur:
        return self._cursor

    def __enter__(self) -> "_FakeConnection":
        return self

    def __exit__(self, *_exc: Any) -> None:
        return None


class _StubTopologyService:
    """TopologyService stand-in: ``plan`` is scripted (normal or collision)."""

    def __init__(self, plan_result: dict[str, Any] | None = None, *, collide: bool = False) -> None:
        self.plan_result = plan_result or {}
        self.collide = collide
        self.plan_calls: list[dict[str, Any]] = []

    def plan(self, request: dict[str, Any]) -> dict[str, Any]:
        self.plan_calls.append(dict(request))
        if self.collide:
            raise UniqueViolation("duplicate key value violates unique constraint")
        return dict(self.plan_result)


def _service(
    topology_service: _StubTopologyService | None = None,
    graph_port: Any = None,
) -> GraphPlanningService:
    return GraphPlanningService(
        "postgresql://unused@localhost/unused",
        topology_service=topology_service or _StubTopologyService(),
        graph_port=graph_port or _NoMatchPort(),
        policy=PolicyEngine(allow=["topology.intent.plan"]),
    )


class _NoMatchPort:
    """Graph port that never matches: exercises the fail-closed branch."""

    def match_nodes(self, intent_text: str, max_matches: int = 4) -> list[dict[str, Any]]:
        return []

    def expand_to_capabilities(self, node_keys: list[str], expand_hops: int = 1) -> dict[str, Any]:
        return {"expanded": []}


# -- contract validation ------------------------------------------------------


def _valid_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "intent": "总账质量校验并出具量化研究结论",
        "budget": {"max_matches": 8, "expand_hops": 1},
        "idempotency_key": "unit-graph-plan-k1",
        "reason": "M10 单元：契约正例",
    }
    payload.update(overrides)
    return payload


def test_planning_intent_contract_accepts_valid_payload() -> None:
    validate_planning_intent(_valid_payload())


def test_planning_intent_contract_requires_idempotency_key() -> None:
    payload = _valid_payload()
    payload.pop("idempotency_key")
    with pytest.raises(ValueError, match="idempotency_key"):
        validate_planning_intent(payload)


def test_planning_intent_contract_rejects_missing_reason() -> None:
    payload = _valid_payload()
    payload.pop("reason")
    with pytest.raises(ValueError, match="reason"):
        validate_planning_intent(payload)


def test_planning_intent_contract_rejects_empty_intent() -> None:
    with pytest.raises(ValueError, match="should be non-empty"):
        validate_planning_intent(_valid_payload(intent=""))


def test_planning_intent_contract_rejects_intent_over_512_chars() -> None:
    with pytest.raises(ValueError, match="too long"):
        validate_planning_intent(_valid_payload(intent="长" * 513))


def test_planning_intent_contract_rejects_budget_out_of_bounds() -> None:
    for budget in (
        {"max_matches": 0, "expand_hops": 1},
        {"max_matches": 17, "expand_hops": 1},
        {"max_matches": 8, "expand_hops": 2},
        {"max_matches": 8},
    ):
        with pytest.raises(ValueError):
            validate_planning_intent(_valid_payload(budget=budget))


def test_planning_intent_result_contract_validates_projection() -> None:
    validate_planning_intent_result(
        {
            "intent_id": str(uuid4()),
            "intent_text": "总账质量校验并出具量化研究结论",
            "matched_nodes": [
                {
                    "node_key": "capability:audit.ledger.validate",
                    "space_key": "capability-l2",
                    "label": "总账质量校验",
                    "score": 0.5,
                    "match_kind": "direct",
                    "match_source": "trigram",
                }
            ],
            "capability_requirements": ["audit.ledger.validate", "quant.research-note.draft"],
            "plan_key": "plan-" + "a" * 16,
            "mode": "plan_only",
            "plan_checksum": "b" * 64,
            "reused_plan": False,
            "node_count": 2,
            "edge_count": 1,
            "created_at": "2026-09-07T00:00:00+00:00",
            "trace_id": str(uuid4()),
            "idempotent": False,
        }
    )


def test_planning_intent_result_contract_rejects_execution_modes() -> None:
    with pytest.raises(ValueError, match="plan_only"):
        validate_planning_intent_result(
            {
                "intent_id": str(uuid4()),
                "intent_text": "意图",
                "matched_nodes": [
                    {
                        "node_key": "capability:audit.ledger.validate",
                        "space_key": "capability-l2",
                        "label": "总账质量校验",
                        "score": 0.5,
                        "match_kind": "direct",
                        "match_source": "trigram",
                    }
                ],
                "capability_requirements": ["audit.ledger.validate"],
                "plan_key": "plan-" + "a" * 16,
                "mode": "execute",
                "plan_checksum": "b" * 64,
                "reused_plan": False,
                "node_count": 1,
                "edge_count": 0,
                "created_at": "2026-09-07T00:00:00+00:00",
                "trace_id": str(uuid4()),
                "idempotent": False,
            }
        )


# -- graph adapter budget bounds (reject before any connection) ----------------


def test_adapter_rejects_max_matches_out_of_range_before_connecting() -> None:
    adapter = CapabilityGraphAdapter("postgresql://unused@localhost/unused")
    with pytest.raises(ValueError, match="max_matches"):
        adapter.match_nodes("意图", max_matches=0)
    with pytest.raises(ValueError, match="max_matches"):
        adapter.match_nodes("意图", max_matches=17)


def test_adapter_rejects_expand_hops_out_of_range_and_empty_walk() -> None:
    adapter = CapabilityGraphAdapter("postgresql://unused@localhost/unused")
    with pytest.raises(ValueError, match="expand_hops"):
        adapter.expand_to_capabilities(["capability:x"], expand_hops=2)
    # an empty key list short-circuits before opening a connection
    assert adapter.expand_to_capabilities([], expand_hops=1) == {"expanded": []}


# -- deterministic merge/ordering of matched + expanded nodes ------------------


def _match(node_key: str, score: float, *, kind: str = "direct", label: str = "label") -> dict[str, Any]:
    return {
        "node_key": node_key,
        "space_key": "capability-l2",
        "label": label,
        "node_type": "capability",
        "score": score,
        "match_kind": kind,
        "match_source": "trigram",
    }


def test_capability_nodes_merge_is_deterministic_and_deduped() -> None:
    matched = [_match("capability:a.one", 0.5), _match("capability:z.two", 0.4)]
    expanded = [
        {
            "node_key": "capability:m.three",
            "space_key": "capability-l2",
            "label": "依赖扩展",
            "node_type": "capability",
            "source_node_key": "capability:a.one",
        },
        # a direct match is never duplicated by the expansion walk
        {"node_key": "capability:a.one", "source_node_key": "capability:z.two"},
    ]
    merged = GraphPlanningService._capability_nodes(matched, expanded)
    assert [item["node_key"] for item in merged] == [
        "capability:a.one",  # highest score, ties broken by node_key
        "capability:m.three",  # expanded inherits a.one's score
        "capability:z.two",
    ]
    assert merged[1]["match_kind"] == "expanded"
    assert merged[1]["match_source"] == "bridge"
    assert merged[1]["source_node_key"] == "capability:a.one"
    assert merged[1]["score"] == 0.5


def test_capability_nodes_orders_by_score_then_key() -> None:
    merged = GraphPlanningService._capability_nodes(
        [_match("capability:b.same", 0.3), _match("capability:a.same", 0.3), _match("capability:c.hi", 0.6)],
        [],
    )
    assert [item["node_key"] for item in merged] == [
        "capability:c.hi",
        "capability:a.same",
        "capability:b.same",
    ]


# -- requirement resolution (capability nodes -> blueprint tokens) -------------


def test_resolve_requirements_maps_only_capability_nodes_deduped() -> None:
    service = _service()
    cur = _FakeCur(
        [  # blueprint_graph_links rows
            ("capability:audit.ledger.validate", "ledger-quality-slot"),
            ("capability:quant.research-note.draft", "research-note-slot"),
            ("capability:audit.finding.draft", "finding-draft-slot"),
        ],
        [  # plugin_blueprints capability contracts
            ("ledger-quality-slot", "audit.ledger.validate"),
            ("research-note-slot", "quant.research-note.draft"),
            ("finding-draft-slot", "audit.finding.draft"),
        ],
    )
    tenant_id = uuid4()
    nodes = [
        _match("capability:audit.ledger.validate", 0.5),
        _match("capability:quant.research-note.draft", 0.4),
        # a domain node never contributes a requirement even if linked
        {
            "node_key": "domain:financial-audit",
            "space_key": "audit-l3",
            "label": "财务审计",
            "node_type": "domain",
            "score": 0.3,
            "match_kind": "direct",
            "match_source": "trigram",
        },
        # expanded node with a capability that is already resolved -> dedup
        {
            "node_key": "capability:audit.finding.draft",
            "space_key": "capability-l2",
            "label": "审计发现草稿",
            "node_type": "capability",
            "score": 0.5,
            "match_kind": "expanded",
            "match_source": "depends_on",
            "source_node_key": "capability:audit.ledger.validate",
        },
        _match("capability:audit.ledger.validate", 0.5),  # duplicate node -> single token
    ]
    requirements = service._resolve_requirements(cur, tenant_id, nodes)
    assert requirements == ["audit.ledger.validate", "quant.research-note.draft", "audit.finding.draft"]
    # the domain row is never queried: capability keys only reach the link table
    assert cur.calls


# -- plan reuse on deterministic plan_key collision ----------------------------


def test_plan_or_reuse_loads_existing_plan_on_collision() -> None:
    collision = _StubTopologyService(collide=True)
    service = _service(topology_service=collision)
    cur = _FakeCur(
        [  # _load_plan_summary fetchone: plan_key, checksum, plan_json
            (
                "plan-" + "d" * 16,
                "e" * 64,
                {"nodes": [{"slot_key": "s.one"}, {"slot_key": "s.two"}], "edges": [{"relation_type": "depends_on"}]},
            )
        ]
    )
    tenant_id = uuid4()
    request = _valid_payload()
    from packages.plugin_topology.contracts import PlanningIntentRequest

    parsed = PlanningIntentRequest.parse(request)
    plan, caught = service._plan_or_reuse(cur, tenant_id, parsed, ["audit.ledger.validate"], uuid4())
    assert caught is True
    assert plan["plan_key"] == "plan-" + "d" * 16
    assert plan["node_count"] == 2
    assert plan["edge_count"] == 1
    # the derived idempotency key is stable for the same intent key + requirements
    assert collision.plan_calls
    assert collision.plan_calls[0]["idempotency_key"].startswith("m10-graph-plan:")
    assert collision.plan_calls[0]["mode"] == "plan_only"
    assert collision.plan_calls[0]["capability_requirements"] == ["audit.ledger.validate"]


def test_plan_or_reuse_passes_through_fresh_plan() -> None:
    fresh = _StubTopologyService(
        plan_result={"plan_key": "plan-" + "f" * 16, "checksum": "c" * 64, "node_count": 2, "edge_count": 1}
    )
    service = _service(topology_service=fresh)
    from packages.plugin_topology.contracts import PlanningIntentRequest

    plan, caught = service._plan_or_reuse(
        _FakeCur(), uuid4(), PlanningIntentRequest.parse(_valid_payload()), ["audit.ledger.validate"], uuid4()
    )
    assert caught is False
    assert plan["plan_key"] == "plan-" + "f" * 16


# -- fail-closed no-match branch (scripted connection, zero DB) ----------------


def test_plan_from_intent_rejects_no_match_without_planning() -> None:
    service = _service(graph_port=_NoMatchPort())
    tenant_id = uuid4()
    cur = _FakeCur(
        [(tenant_id,)],  # _tenant SELECT id
        [(None,)],       # _tenant set_config fetchone
        [],              # _intent_replay fetchone -> no replay (fetchone returns None)
    )
    with (
        patch("packages.plugin_topology.graph_planning.psycopg2.connect", return_value=_FakeConnection(cur)),
        pytest.raises(ValueError, match="fail-closed"),
    ):
        service.plan_from_intent(_valid_payload(), trace_id=uuid4())
    # no INSERT was attempted and the port was consulted exactly once
    assert not any("INSERT INTO topology.planning_intents" in call for call in cur.calls)


def test_plan_from_intent_replays_recorded_intent_idempotently() -> None:
    service = _service()
    tenant_id = uuid4()
    recorded_id = uuid4()
    created = datetime(2026, 9, 7, tzinfo=UTC)
    cur = _FakeCur(
        [(tenant_id,)],
        [(None,)],
        [  # _intent_replay row: id,intent_text,matched_nodes,requirements,plan_key,trace,created_at
            (
                recorded_id,
                "总账质量校验并出具量化研究结论",
                [
                    {
                        "node_key": "capability:audit.ledger.validate",
                        "space_key": "capability-l2",
                        "label": "总账质量校验",
                        "node_type": "capability",
                        "score": 0.5,
                        "match_kind": "direct",
                        "match_source": "trigram",
                    }
                ],
                ["audit.ledger.validate"],
                "plan-" + "a" * 16,
                str(uuid4()),
                created,
            ),
        ],
        [  # _load_plan_summary fetchone
            ("plan-" + "a" * 16, "b" * 64, {"nodes": [{"slot_key": "s.one"}], "edges": []}),
        ],
        [(1,)],  # reuse count
    )
    with patch("packages.plugin_topology.graph_planning.psycopg2.connect", return_value=_FakeConnection(cur)):
        result = service.plan_from_intent(_valid_payload(), trace_id=uuid4())
    assert result["idempotent"] is True
    assert result["intent_id"] == str(recorded_id)
    assert result["reused_plan"] is True
    assert result["mode"] == "plan_only"
    assert result["plan_key"] == "plan-" + "a" * 16
    assert result["plan_checksum"] == "b" * 64
    assert not any("INSERT INTO topology.planning_intents" in call for call in cur.calls)
