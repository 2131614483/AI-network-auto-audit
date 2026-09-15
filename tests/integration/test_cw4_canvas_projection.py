"""CW4 contract tests: server-side canvas projection + runs feed.

Acceptance (方案 第13节 CW4 :436):
  - a run executed through CW3 is projected as a 2-D canvas definition
    (nodes = attempts with status/error/output refs, edges = data flow with
    sha/uri/adapter, seed edges included);
  - the runs feed lists persisted runs with aggregated attempt state;
  - the projection is tenant-isolated (RLS), unknown runs 404, and the
    canvas always matches the persisted attempt/edge state (no separate
    truth).
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from urllib.request import url2pathname
from uuid import UUID, uuid4

import psycopg2
import psycopg2.extras
import pytest
from fastapi.testclient import TestClient

from apps.api.main import Settings, create_app
from packages.plugin_runtime.runner import ArtifactInput
from packages.plugin_topology import port_adapters  # noqa: F401  (registers the real port adapter)
from packages.plugin_topology.compiler import compile_plan
from packages.plugin_topology.dag_persistence import AttemptStore
from packages.plugin_topology.service import TopologyService
from packages.policy.engine import PolicyEngine

TEST_DB = "postgresql://audit_app:admin@localhost:5432/audit_network_test"
_SHA64 = "a" * 64
_ADAPTER = "candidates-to-backtest"
_ALLOW = [
    "topology.chain.execute", "topology.chain.execute.isolated",
    "audit.ledger.validate", "quant.experiment.evaluate",
]


def _port(port_id: str, direction: str, *, schema_ref: str = "artifact-ref") -> dict:
    return {
        "port_id": port_id, "direction": direction, "schema_ref": schema_ref,
        "schema_version": "1.0.0", "schema_sha256": _SHA64,
        "media_type": "application/json", "required": True,
        "cardinality": "one", "classification": "internal",
        "transport": "artifact_ref",
    }


def _node(node_instance_id: str, capability: str, plugin_id: str, *,
          inputs: tuple[dict, ...] = (), outputs: tuple[dict, ...] = ()) -> dict:
    return {
        "node_instance_id": node_instance_id, "plugin_id": plugin_id,
        "capability": capability, "input_ports": list(inputs),
        "output_ports": list(outputs),
    }


def _edge(edge_id: str, source_instance: str, source_port: str,
          target_instance: str, target_port: str) -> dict:
    return {
        "edge_id": edge_id, "source_instance": source_instance,
        "source_port": source_port, "target_instance": target_instance,
        "target_port": target_port, "adapter": _ADAPTER,
    }


_LEDGER_IN = _port("ledger", "input", schema_ref="ledger-artifact-ref")
_LEDGER_OUT = _port("candidates", "output", schema_ref="audit-quality-candidates")
_EXPERIMENT_IN = _port("experiment", "input", schema_ref="backtest-report")
_EVAL_OUT = _port("evaluation", "output", schema_ref="experiment-evaluation")
_BUDGET = {"max_chain_length": 8, "max_candidates": 8, "max_latency_ms": 5000}


def _write_seed(staging: Path, name: str, rows: str) -> ArtifactInput:
    path = staging / "inputs" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(rows, encoding="utf-8-sig")
    raw = path.read_bytes()
    return ArtifactInput(
        artifact_id=uuid4(), tenant_id=uuid4(), uri=path.resolve().as_uri(),
        media_type="text/csv", sha256=hashlib.sha256(raw).hexdigest(),
        size_bytes=len(raw), classification="audit_ledger",
    )


def _ledger_csv(tag: str) -> str:
    return (
        "entry_id,date,account_code,description,debit_amount,credit_amount\n"
        f"E1,2026-01-05,1101,{tag}-收款,100.00,100.00\n"
        f"E2,2026-01-06,1102,{tag}-划转,200.00,200.00\n"
    )


def _build_plan() -> object:
    nodes = [
        _node("ledger-a", "audit.ledger.validate", "audit.ledger-quality",
              inputs=(_LEDGER_IN,), outputs=(_LEDGER_OUT,)),
        _node("ledger-b", "audit.ledger.validate", "audit.ledger-quality",
              inputs=(_LEDGER_IN,), outputs=(_LEDGER_OUT,)),
        _node("consumer-x", "quant.experiment.evaluate", "quant.experiment-evaluator",
              inputs=(_EXPERIMENT_IN,), outputs=(_EVAL_OUT,)),
        _node("consumer-y", "quant.experiment.evaluate", "quant.experiment-evaluator",
              inputs=(_EXPERIMENT_IN,), outputs=(_EVAL_OUT,)),
    ]
    edges = [
        _edge("e1", "ledger-a", "candidates", "consumer-x", "experiment"),
        _edge("e2", "ledger-a", "candidates", "consumer-y", "experiment"),
    ]
    return compile_plan(
        nodes=nodes, edges=edges, budget=_BUDGET, plan_key=f"cw4-{uuid4().hex[:8]}",
        seed_inputs={("ledger-a", "ledger"), ("ledger-b", "ledger")},
    )


def _tenant(connection) -> UUID:
    with connection.cursor() as cur:
        cur.execute("SELECT id FROM iam.tenants WHERE slug='local-dev'")
        row = cur.fetchone()
        assert row is not None
        return UUID(str(row[0]))


def _allow(connection, tenant_id: UUID, name: str, action: str, risk_class: str) -> None:
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
                        "capabilities": [action], "risk_classes": [risk_class],
                        "side_effects": ["read_only" if risk_class == "read_only" else "write_data"],
                    },
                }]),
            ),
        )


@pytest.fixture()
def tenant_id() -> UUID:
    with psycopg2.connect(TEST_DB) as connection:
        return _tenant(connection)


def _run_once(tmp_path: Path, tenant_id: UUID) -> dict:
    staging = tmp_path / "staging"
    staging.mkdir(parents=True, exist_ok=True)
    seed_a = _write_seed(staging, "ledger-a.csv", _ledger_csv("A"))
    seed_b = _write_seed(staging, "ledger-b.csv", _ledger_csv("B"))
    plan = _build_plan()
    service = TopologyService(TEST_DB, policy=PolicyEngine(allow=_ALLOW))
    return service.start_plan_run(
        plan,
        seed_inputs={("ledger-a", "ledger"): seed_a, ("ledger-b", "ledger"): seed_b},
        worker_id="cw4-test-worker",
        staging_root=staging,
    )


def _api_headers(tenant_id: UUID) -> dict[str, str]:
    return {"X-Tenant-Id": str(tenant_id), "X-Trace-Id": str(uuid4())}


# -- 1. runs feed: persisted run with aggregated attempt state ------------------


def test_runs_feed_lists_persisted_run_with_state(tmp_path: Path, tenant_id: UUID) -> None:
    run = _run_once(tmp_path, tenant_id)
    with psycopg2.connect(TEST_DB) as connection:
        _allow(connection, tenant_id, f"cw4-lst-{uuid4().hex[:6]}", "topology.execution.read", "read_only")

    client = TestClient(create_app(Settings(database_url=TEST_DB)))
    response = client.get("/api/v1/topology/runs", headers=_api_headers(tenant_id))
    assert response.status_code == 200
    body = response.json()
    items = body["items"]
    assert len(items) >= 1
    mine = next(item for item in items if item["run_id"] == run["run_id"])
    assert mine["plan_key"] == run["plan_key"]
    assert mine["execution_hash"] == run["execution_hash"]
    assert mine["status"] == "succeeded"
    assert mine["attempt_total"] == 4
    assert mine["attempt_succeeded"] == 4
    assert mine["attempt_failed"] == 0
    assert mine["trace_id"]


# -- 2. canvas projection matches persisted attempt/edge state ------------------


def test_canvas_projection_matches_persisted_state(tmp_path: Path, tenant_id: UUID) -> None:
    run = _run_once(tmp_path, tenant_id)
    with psycopg2.connect(TEST_DB) as connection:
        _allow(connection, tenant_id, f"cw4-cnv-{uuid4().hex[:6]}", "topology.execution.read", "read_only")

    client = TestClient(create_app(Settings(database_url=TEST_DB)))
    response = client.get(f"/api/v1/topology/canvas/{run['run_id']}", headers=_api_headers(tenant_id))
    assert response.status_code == 200
    body = response.json()
    assert body["run_id"] == run["run_id"]
    assert body["status"] == "succeeded"
    assert body["trace_id"] == run["trace_id"]

    nodes = {n["node_instance_id"]: n for n in body["nodes"]}
    assert set(nodes) == {"ledger-a", "ledger-b", "consumer-x", "consumer-y"}
    assert all(n["status"] == "succeeded" and n["error_kind"] is None for n in nodes.values())
    # output refs: every node has real disk artifacts with matching sha
    for node_id, node in nodes.items():
        refs = node["output_refs"]
        assert refs, node_id
        for ref in refs.values():
            uri = ref["uri"]
            path = Path(url2pathname(uri[len("file://"):] if uri.startswith("file://") else uri))
            assert path.is_file(), (node_id, ref)
            assert hashlib.sha256(path.read_bytes()).hexdigest() == ref["sha256"]

    # edges = plan fan-out + seed injections, all with sha/uri/adapter
    edges = body["edges"]
    keys = {tuple(e["key"]) for e in edges}
    assert {
        ("ledger-a", "candidates", "consumer-x", "experiment"),
        ("ledger-a", "candidates", "consumer-y", "experiment"),
        ("seed", "ledger", "ledger-a", "ledger"),
        ("seed", "ledger", "ledger-b", "ledger"),
    } <= keys
    assert all(e["sha256"] and e["uri"] for e in edges)
    # adapter recorded on the candidates->backtest hand-off
    fan_out = [e for e in edges if e["key"][1] == "candidates"]
    assert fan_out and all(e["adapter"] == _ADAPTER for e in fan_out)

    # projection is the same truth as the store
    store = AttemptStore(TEST_DB)
    persisted_attempts = store.query_attempts(tenant_id=tenant_id, run_id=run["run_id"])
    persisted_edges = store.query_edges(tenant_id=tenant_id, run_id=run["run_id"])
    assert {n["node_instance_id"] for n in body["nodes"]} == {a["node_instance_id"] for a in persisted_attempts}
    assert {tuple(e["key"]) for e in body["edges"]} == {e["key"] for e in persisted_edges}


# -- 3. unknown run / tenant isolation ------------------------------------------


def test_canvas_unknown_run_404(tmp_path: Path, tenant_id: UUID) -> None:
    with psycopg2.connect(TEST_DB) as connection:
        _allow(connection, tenant_id, f"cw4-404-{uuid4().hex[:6]}", "topology.execution.read", "read_only")
    client = TestClient(create_app(Settings(database_url=TEST_DB)))
    response = client.get(f"/api/v1/topology/canvas/{uuid4()}", headers=_api_headers(tenant_id))
    assert response.status_code == 404


def test_canvas_tenant_isolation(tmp_path: Path, tenant_id: UUID) -> None:
    run = _run_once(tmp_path, tenant_id)
    with psycopg2.connect(TEST_DB) as connection:
        with connection.cursor() as cur:
            cur.execute(
                "SELECT id FROM iam.tenants WHERE slug <> 'local-dev' AND (slug LIKE '%-other%' OR slug LIKE 'm3-other%') ORDER BY slug LIMIT 1",
            )
            row = cur.fetchone()
            assert row is not None
        other_tenant = UUID(str(row[0]))
        # authorize a tenant that has no run rows at all
        _allow(connection, other_tenant, f"cw4-iso-{uuid4().hex[:6]}", "topology.execution.read", "read_only")
    client = TestClient(create_app(Settings(database_url=TEST_DB)))
    headers = {"X-Tenant-Id": str(other_tenant), "X-Trace-Id": str(uuid4())}
    # the run belongs to the local-dev tenant; another tenant must not see it
    response = client.get(f"/api/v1/topology/canvas/{run['run_id']}", headers=headers)
    assert response.status_code == 404
    feed = client.get("/api/v1/topology/runs", headers=headers)
    assert feed.status_code == 200
    assert all(item["run_id"] != run["run_id"] for item in feed.json()["items"])
