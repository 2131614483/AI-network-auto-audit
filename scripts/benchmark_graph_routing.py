"""Generate an isolated synthetic graph in the test database and run Golden Query.

This is intentionally an additive benchmark: every run uses a UUID-prefixed
space and inserts no data into the interactive ``audit_network`` database.
It never deletes previous evidence or graph rows.
"""

from __future__ import annotations

import argparse
import json
import os
from statistics import median
from time import perf_counter
from typing import Any
from urllib.parse import urlparse
from uuid import UUID, uuid4

import psycopg2
from psycopg2.extras import execute_values

from packages.graph.service import GraphBudget, GraphService


def percentile_95(values: list[float]) -> float:
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int((len(ordered) - 1) * 0.95)))
    return ordered[index]


def require_test_database(database_url: str) -> None:
    parsed = urlparse(database_url)
    if parsed.path.rstrip("/") != "/audit_network_test":
        raise SystemExit("Golden Query only permits audit_network_test; no interactive database was touched.")


def chunks(values: list[tuple[object, ...]], size: int) -> list[list[tuple[object, ...]]]:
    return [values[offset:offset + size] for offset in range(0, len(values), size)]


def build_fixture(database_url: str, nodes: int, edges: int) -> tuple[GraphService, str, UUID]:
    suffix = uuid4().hex
    control_space = f"golden-capability-{suffix}"
    domain_space = f"golden-domain-{suffix}"
    service = GraphService(database_url)
    service.ensure_space(control_space, "L2", "Golden 能力图", cluster_key="golden")
    service.ensure_space(domain_space, "L3", "Golden 领域图", cluster_key="golden")
    gateway = service.upsert_node(control_space, f"gateway:{suffix}", "capability", "Golden Query 网关")

    with psycopg2.connect(database_url) as connection:
        with connection.cursor() as cur:
            cur.execute("SELECT id FROM iam.tenants WHERE slug='local-dev'")
            tenant_row = cur.fetchone()
            if tenant_row is None:
                raise RuntimeError("local-dev tenant not found")
            tenant_id: UUID = tenant_row[0]
            cur.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),))
            cur.execute("SELECT id FROM graph.spaces WHERE tenant_id=%s AND key=%s", (tenant_id, domain_space))
            space_row = cur.fetchone()
            if space_row is None:
                raise RuntimeError("Golden domain space not found")
            domain_space_id: UUID = space_row[0]
            node_ids = [uuid4() for _ in range(nodes)]
            node_rows: list[tuple[object, ...]] = [
                (node_id, tenant_id, domain_space_id, f"golden:{suffix}:{index}", "entity", f"Golden 节点 {index}", json.dumps({"synthetic": True, "benchmark": suffix}))
                for index, node_id in enumerate(node_ids)
            ]
            for batch in chunks(node_rows, 10_000):
                execute_values(
                    cur,
                    """INSERT INTO graph.nodes(id,tenant_id,space_id,canonical_key,node_type,label,properties)
                    VALUES %s""",
                    batch,
                    template="(%s,%s,%s,%s,%s,%s,%s::jsonb)",
                    page_size=10_000,
                )
            edge_rows: list[tuple[object, ...]] = []
            for index in range(edges):
                source_index = index % nodes
                source = node_ids[source_index]
                target = node_ids[(source_index + index // nodes + 1) % nodes]
                edge_rows.append((uuid4(), tenant_id, domain_space_id, source, target, "related_to", 0.9, "{}"))
                if len(edge_rows) == 10_000:
                    execute_values(
                        cur,
                        """INSERT INTO graph.edges(id,tenant_id,space_id,source_node_id,target_node_id,relation_type,weight,properties)
                        VALUES %s ON CONFLICT(space_id,source_node_id,target_node_id,relation_type) DO NOTHING""",
                        edge_rows,
                        template="(%s,%s,%s,%s,%s,%s,%s,%s::jsonb)",
                        page_size=10_000,
                    )
                    edge_rows = []
            if edge_rows:
                execute_values(
                    cur,
                    """INSERT INTO graph.edges(id,tenant_id,space_id,source_node_id,target_node_id,relation_type,weight,properties)
                    VALUES %s ON CONFLICT(space_id,source_node_id,target_node_id,relation_type) DO NOTHING""",
                    edge_rows,
                    template="(%s,%s,%s,%s,%s,%s,%s,%s::jsonb)",
                    page_size=10_000,
                )
    service.register_bridge(control_space, domain_space, gateway, node_ids[0], "capability_contract", 0.9)
    return service, control_space, gateway


def main() -> None:
    parser = argparse.ArgumentParser(description="Run bounded multi-graph Golden Query in audit_network_test only.")
    parser.add_argument("--database-url", default=os.getenv("AUDIT_NETWORK_TEST_DATABASE_URL", "postgresql://audit_app:admin@localhost:5432/audit_network_test"))
    parser.add_argument("--nodes", type=int, default=2_000)
    parser.add_argument("--edges", type=int, default=8_000)
    parser.add_argument("--samples", type=int, default=8)
    parser.add_argument("--p95-budget-ms", type=float, default=3_000)
    args = parser.parse_args()
    if not 10 <= args.nodes <= 100_000 or not args.nodes <= args.edges <= 1_000_000:
        raise SystemExit("require 10 <= nodes <= 100000 and nodes <= edges <= 1000000")
    if not 3 <= args.samples <= 30:
        raise SystemExit("samples must be between 3 and 30")
    require_test_database(args.database_url)
    service, space_key, gateway = build_fixture(args.database_url, args.nodes, args.edges)
    budget = GraphBudget(
        max_graphs=2, max_hops=3, max_frontier=128, max_nodes=512, max_edges=1_024,
        max_bridge_hops=1, max_latency_ms=int(args.p95_budget_ms), min_confidence=0.5,
    )
    service.bounded_route(space_key, gateway, budget)  # warm-up
    timings: list[float] = []
    result: dict[str, Any] = {}
    for _ in range(args.samples):
        started = perf_counter()
        result = service.bounded_route(space_key, gateway, budget)
        timings.append((perf_counter() - started) * 1000)
    p95 = percentile_95(timings)
    report = {
        "database": "audit_network_test", "nodes_requested": args.nodes, "edges_requested": args.edges,
        "samples": args.samples, "p50_ms": round(median(timings), 2), "p95_ms": round(p95, 2),
        "p95_budget_ms": args.p95_budget_ms, "visited_space_keys": result["visited_space_keys"],
        "returned_nodes": len(result["nodes"]), "returned_edges": len(result["edges"]),
        "partial": result["partial"], "truncation_reasons": result["truncation_reasons"],
    }
    print(json.dumps(report, ensure_ascii=False))
    if p95 > args.p95_budget_ms:
        raise SystemExit("Golden Query P95 exceeded the requested budget")


if __name__ == "__main__":
    main()
