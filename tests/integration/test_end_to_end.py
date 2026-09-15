from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

from packages.aiops.engine import AIOpsEngine
from packages.audit.pipeline import AuditPipeline
from packages.graph.service import GraphBudget, GraphService
from packages.knowledge.ingest import ingest_directory
from packages.policy.engine import PolicyEngine
from packages.quant.backtest import run_csv_backtest

DB = os.getenv("AUDIT_NETWORK_TEST_DATABASE_URL", "postgresql://audit_app:admin@localhost:5432/audit_network")


def test_initial_vertical_slice_end_to_end(tmp_path: Path) -> None:
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir()
    (knowledge / "control.md").write_text("# Revenue Cutoff\n\nControl evidence.", encoding="utf-8")
    ingest = ingest_directory(knowledge, database_url=DB)
    assert ingest.accepted == 1

    graph = GraphService(DB)
    space_key = f"e2e-{uuid4().hex[:8]}"
    graph.ensure_space(space_key, "L1", "E2E")
    node = graph.upsert_node(space_key, "control:revenue-cutoff", "control", "Revenue Cutoff")
    result = graph.bounded_neighbors(space_key, node, GraphBudget(max_nodes=5, max_edges=5, max_hops=1))
    assert result["nodes"] == [str(node)]

    ledger = tmp_path / "ledger.csv"
    ledger.write_text("account,amount\n6001,1200000\n6002,bad\n", encoding="utf-8")
    audit = AuditPipeline(DB).run_ledger_csv(ledger, engagement_name=f"E2E {uuid4().hex[:8]}")
    assert audit.anomalies == 2

    prices = tmp_path / "prices.csv"
    prices.write_text("date,close\n2026-01-01,100\n2026-01-02,105\n2026-01-03,103\n", encoding="utf-8")
    backtest = run_csv_backtest(DB, prices, strategy_key=f"e2e-{uuid4().hex[:8]}")
    assert backtest.simulated_only is True

    aiops = AIOpsEngine(DB, PolicyEngine(auto=True))
    incident = aiops.ingest_alert("e2e", f"e2e-{uuid4().hex}", "low", {"ok": True})
    proposal = aiops.propose(incident, "refresh-cache", {"kind": "refresh"}, risk_class="low")
    _, status = aiops.execute(proposal)
    assert status == "completed"
