"""Contract tests: an AI-written flow file executes end to end.

This guards the chain the desktop "AI 画布组网" relies on: a flow file that an
AI (or the assistant) writes directly is compiled, passes the Policy Gateway,
runs in the isolated simulated executor and lands in ``control.node_attempts``
so the desktop Runs history + canvas projection can render it.

Regressions covered:
  - blueprint key vs runtime plugin id: a flow file that carries the blueprint
    key as plugin_id compiles but must NOT execute; the catalog maps blueprint
    keys to verified runtime ids so chat-generated drafts use the runtime id.
  - port contract vs runtime envelope: finding.draft expects a domain envelope
    ({"finding": {"anomaly_candidates": ref}}), not a port-id-keyed payload.
  - a deterministic duplicate-row ledger produces a finding draft, so the
    chain is not a silent empty pass.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from urllib.request import url2pathname
from uuid import UUID, uuid4

import psycopg2
import pytest

from packages.plugin_runtime.runner import ArtifactInput
from packages.plugin_topology import port_adapters  # noqa: F401  (registers the real port adapter)
from packages.plugin_topology.compiler import compile_plan
from packages.plugin_topology.service import TopologyService
from packages.policy.engine import PolicyEngine

TEST_DB = "postgresql://audit_app:admin@localhost:5432/audit_network_test"
_SHA64 = "a" * 64
_ALLOW = [
    "topology.chain.execute", "topology.chain.execute.isolated",
    "audit.ledger.validate", "audit.finding.draft",
]


def _port(port_id: str, direction: str, *, schema_ref: str) -> dict:
    return {
        "port_id": port_id, "direction": direction, "schema_ref": schema_ref,
        "schema_version": "1.0.0", "schema_sha256": _SHA64,
        "media_type": "application/json", "required": True,
        "cardinality": "one", "classification": "internal",
        "transport": "artifact_ref",
    }


def _flow_nodes() -> list[dict]:
    return [
        {
            "node_instance_id": "ledger-validate-001",
            "plugin_id": "audit.ledger-quality",
            "capability": "audit.ledger.validate",
            "input_ports": [_port("ledger", "input", schema_ref="ledger-artifact-ref")],
            "output_ports": [_port("audit-quality-candidates", "output", schema_ref="audit-quality-candidates")],
        },
        {
            "node_instance_id": "finding-draft-001",
            "plugin_id": "audit.finding-draft",
            "capability": "audit.finding.draft",
            "input_ports": [_port("candidates", "input", schema_ref="audit-quality-candidates")],
            "output_ports": [_port("finding-draft", "output", schema_ref="finding-draft")],
        },
    ]


def _flow_edges() -> list[dict]:
    return [
        {
            "edge_id": "e-ledger-candidates",
            "source_instance": "ledger-validate-001",
            "source_port": "audit-quality-candidates",
            "target_instance": "finding-draft-001",
            "target_port": "candidates",
        }
    ]


def _duplicate_row_ledger() -> str:
    return (
        "entry_id,date,account_code,description,debit_amount,credit_amount\n"
        "E1,2026-01-05,1101,收款,100.00,100.00\n"
        "E1,2026-01-05,1101,收款,100.00,100.00\n"
    )


def _write_seed(staging: Path) -> ArtifactInput:
    path = staging / "inputs" / "ledger-a.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_duplicate_row_ledger(), encoding="utf-8-sig")
    raw = path.read_bytes()
    return ArtifactInput(
        artifact_id=uuid4(), tenant_id=uuid4(), uri=path.resolve().as_uri(),
        media_type="text/csv", sha256=hashlib.sha256(raw).hexdigest(),
        size_bytes=len(raw), classification="audit_ledger",
    )


@pytest.fixture()
def tenant_id() -> UUID:
    with psycopg2.connect(TEST_DB) as connection, connection.cursor() as cur:
        cur.execute("SELECT id FROM iam.tenants WHERE slug='local-dev'")
        row = cur.fetchone()
        assert row is not None
        return UUID(str(row[0]))


def test_ai_written_flow_executes_end_to_end(tmp_path: Path, tenant_id: UUID) -> None:
    staging = tmp_path / "staging"
    staging.mkdir(parents=True, exist_ok=True)
    seed = _write_seed(staging)

    plan = compile_plan(
        nodes=_flow_nodes(), edges=_flow_edges(),
        budget={"max_chain_length": 8, "max_candidates": 8, "max_latency_ms": 5000},
        plan_key=f"plan-ai-direct-{uuid4().hex[:8]}",
        seed_inputs={("ledger-validate-001", "ledger")},
    )

    service = TopologyService(TEST_DB, policy=PolicyEngine(allow=_ALLOW))
    result = service.start_plan_run(
        plan,
        seed_inputs={("ledger-validate-001", "ledger"): seed},
        worker_id="ai-direct-test",
        staging_root=staging,
    )

    assert result["status"] == "succeeded"
    attempts = result["attempts"]
    assert {a["node_instance_id"] for a in attempts} == {"ledger-validate-001", "finding-draft-001"}
    assert all(a["status"] == "succeeded" for a in attempts)

    # finding-draft consumed the ledger node's candidates port via the data edge.
    finding = {a["node_instance_id"]: a for a in attempts}["finding-draft-001"]
    candidates = finding["input_bindings"]["candidates"]
    assert candidates["source_instance"] == "ledger-validate-001"
    assert candidates["source_port"] == "audit-quality-candidates"
    assert "finding-draft" in finding["output_refs"]

    # The finding draft is not empty: the duplicate-row candidate produced a
    # deterministic duplicate-entry finding.
    uri = finding["output_refs"]["finding-draft"]["uri"]
    path = Path(url2pathname(uri[len("file://"):] if uri.startswith("file://") else uri))
    draft = json.loads(path.read_text(encoding="utf-8"))
    assert draft.get("contract_id") == "finding-draft"
    assert len(draft.get("findings") or []) >= 1
    assert any(f.get("rule_key") == "duplicate_row" for f in draft["findings"])

    # Both attempts are visible to the Runs history query.
    with psycopg2.connect(TEST_DB) as connection, connection.cursor() as cur:
        cur.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant_id),))
        cur.execute(
            "SELECT count(*) FROM control.node_attempts "
            "WHERE run_id=%s AND status='succeeded'",
            (str(result["run_id"]),),
        )
        assert cur.fetchone()[0] == 2
