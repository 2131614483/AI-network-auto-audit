"""CW0-B contract test: a crash mid-chain must not roll back the evidence that
was already committed.

Acceptance (方案 第13节 验收矩阵 CW0 出口3「故障后已提交账本仍在」):
  - begin_run row and every succeeded per-node ledger row stay committed
    even when the executor raises (simulated child crash) afterwards;
  - the run is finalized as ``failed`` with the already-written node rows.

Current defect: service.start_run wraps begin_run + executor.execute +
finalize_run in one ``with psycopg2.connect`` transaction; the ``except``
path finalizes then re-raises, and the connection rollback discards every row.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from uuid import uuid4

import psycopg2
import pytest

from packages.plugin_runtime.runner import ArtifactInput
from packages.plugin_topology.isolated import InputSource, IsolatedChainExecutor
from packages.plugin_topology.service import TopologyService
from packages.policy.engine import PolicyEngine

TEST_DB = "postgresql://audit_app:admin@localhost:5432/audit_network_test"


def _service(tenant_slug: str) -> TopologyService:
    return TopologyService(
        TEST_DB,
        tenant_slug=tenant_slug,
        policy=PolicyEngine(allow=[
            "topology.cluster.write", "topology.blueprint.write", "topology.membership.write",
            "topology.edge.write", "topology.contract.write", "topology.release.publish",
            "topology.plan.read", "topology.chain.write",
            "topology.chain.execute", "topology.chain.execute.isolated",
            "audit.ledger.validate", "quant.research-note.draft",
        ]),
    )


def _create_tenant(slug: str) -> None:
    with psycopg2.connect(TEST_DB) as connection, connection.cursor() as cur:
        cur.execute(
            "INSERT INTO iam.tenants(slug,name) VALUES(%s,%s) ON CONFLICT(slug) DO NOTHING",
            (slug, "CW0B 隔离租户"),
        )


def _build_chain(service: TopologyService, marker: str) -> str:
    """2-node chain: audit.ledger.validate (verified) -> quant.research-note.draft."""
    domain_key = f"cw0b-{marker}-domain"
    producer_key = f"cw0b-{marker}-producer"
    consumer_key = f"cw0b-{marker}-consumer"
    shared = f"cw0b-{marker}-shared-contract"

    service.upsert({
        "kind": "cluster",
        "idempotency_key": f"{marker}-cw0b-cluster",
        "payload": {
            "key": domain_key, "name": f"CW0B 执行域-{marker}", "axis": "business_domain", "layer": "L0",
            "domains": ["audit"],
            "routing_budget": {"max_candidates": 8, "max_chain_length": 4, "max_latency_ms": 5000},
        },
    })
    for key, capability, contract in (
        (producer_key, "audit.ledger.validate", {
            "capability": "audit.ledger.validate", "version": "1.0.0", "outputs": [shared],
        }),
        (consumer_key, "quant.research-note.draft", {
            "capability": "quant.research-note.draft", "version": "1.0.0",
            "inputs": [shared], "outputs": [],
        }),
    ):
        service.upsert({
            "kind": "blueprint",
            "idempotency_key": f"{marker}-cw0b-bp-{key}",
            "payload": {
                "key": key, "name": f"槽位-{key}", "blueprint_type": "capability",
                "primary_cluster_key": domain_key, "lifecycle": "planned",
                "capability_contract": contract,
                "source_refs": [{"kind": "design_document", "uri": f"reference://cw0b-{marker}"}],
            },
        })
        service.upsert({
            "kind": "membership",
            "idempotency_key": f"{marker}-cw0b-mb-{key}",
            "payload": {"blueprint_key": key, "cluster_key": domain_key, "axis": "business"},
        })
    service.upsert({
        "kind": "edge",
        "idempotency_key": f"{marker}-cw0b-edge",
        "payload": {
            "source_blueprint_key": producer_key, "target_blueprint_key": consumer_key,
            "relation_type": "depends_on",
        },
    })
    release = service.release({
        "action": "publish",
        "idempotency_key": f"{marker}-cw0b-release",
        "version": f"0.1.0-{marker}",
        "catalog_checksum": hashlib.sha256(f"cw0b-{marker}".encode("utf-8")).hexdigest(),
        "blueprint_keys": [producer_key, consumer_key],
        "cluster_keys": [domain_key],
        "created_by": str(uuid4()),
        "reason": "CW0B release lock",
    })
    release_lock = {
        "release_id": release["release_id"],
        "version": release["version"],
        "checksum_sha256": release["catalog_checksum"],
    }
    plan = service.plan({
        "intent": f"CW0B 执行链-{marker}",
        "idempotency_key": f"{marker}-cw0b-plan",
        "mode": "plan_only",
        "capability_requirements": ["audit.ledger.validate", "quant.research-note.draft"],
        "budget": {"max_candidates": 8, "max_chain_length": 4, "max_latency_ms": 5000},
        "release_lock": release_lock,
    })
    assert plan["node_count"] == 2
    chain = service.materialize_chain({
        "plan_key": plan["plan_key"],
        "idempotency_key": f"{marker}-cw0b-materialize",
        "reason": "CW0B 物化",
    })
    assert chain["intent_count"] == 2
    return chain["chain_key"]


def _write_ledger_csv(marker: str) -> str:
    staging = Path(__file__).resolve().parents[2] / ".data" / "isolated" / "inputs"
    staging.mkdir(parents=True, exist_ok=True)
    path = staging / f"cw0b-ledger-{marker}.csv"
    path.write_text(
        "entry_id,date,account_code,description,debit_amount,credit_amount\n"
        "E1,2026-01-05,1101,现金收款,100.00,100.00\n"
        "E2,2026-01-06,1102,银行划转,200.00,200.00\n",
        encoding="utf-8-sig",
    )
    return str(path)


def test_crash_after_committed_nodes_keeps_evidence(
    tmp_path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = uuid4().hex[:8]
    slug = f"cw0b-{marker}-t"
    _create_tenant(slug)
    service = _service(slug)
    chain_key = _build_chain(service, marker)

    # Simulate a crash that happens *after* the first node already wrote its
    # ledger row and the second node failed for lack of a governed input:
    # the wrapped executor runs the real child flow, then raises.
    original_execute = IsolatedChainExecutor.execute

    def crashing_execute(self, connection, cur):
        outcome = original_execute(self, connection, cur)
        assert outcome["entries"][0]["status"] == "succeeded", "precondition: node 1 succeeded"
        raise OSError("simulated child crash after committed nodes")

    monkeypatch.setattr(IsolatedChainExecutor, "execute", crashing_execute)

    csv_path = _write_ledger_csv(marker)
    raw = Path(csv_path).read_bytes()
    artifact = ArtifactInput(
        artifact_id=uuid4(), tenant_id=uuid4(),
        uri=Path(csv_path).resolve().as_uri(),
        media_type="text/csv",
        sha256=hashlib.sha256(raw).hexdigest(),
        size_bytes=len(raw),
        classification="audit_ledger",
    )

    with pytest.raises(OSError, match="simulated child crash"):
        service.start_run(
            {
                "chain_key": chain_key,
                "mode": "isolated",
                "idempotency_key": f"{marker}-cw0b-run",
                "reason": "CW0B 崩溃注入：已提交证据必须保留",
            },
            input_sources={
                "audit.ledger.validate": InputSource(
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
            },
        )

    # 出口3: the run row and both node rows are committed even though the
    # executor raised; the run itself is finalized as failed.
    with psycopg2.connect(TEST_DB) as connection, connection.cursor() as cur:
        cur.execute("SELECT set_config('app.tenant_id', (SELECT id::text FROM iam.tenants WHERE slug=%s), false)", (slug,))
        cur.fetchone()
        cur.execute(
            """SELECT r.status, COUNT(e.id)
            FROM topology.execution_runs r
            JOIN topology.execution_ledger e ON e.run_id=r.id
            JOIN topology.invocation_chains c ON c.id=r.chain_id AND c.tenant_id=r.tenant_id
            WHERE c.chain_key=%s GROUP BY r.status""",
            (chain_key,),
        )
        rows = cur.fetchall()
    assert rows, "evidence must survive a mid-chain crash (run + ledger rows)"
    status_by_run = dict(rows)
    assert "failed" in status_by_run
    assert status_by_run["failed"] == 2, "both node ledger rows must stay committed"
