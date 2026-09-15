"""Phase 8: read-only quant evidence-chain queries.

Exposes the lineage of simulated backtests (backtest -> dataset -> snapshot),
including code/data SHA and the point-in-time gate, without any new run path.
"""

from __future__ import annotations

from typing import Any

import psycopg2
from psycopg2.extras import register_uuid

register_uuid()  # type: ignore[no-untyped-call]


class QuantService:
    def __init__(self, database_url: str, tenant_slug: str = "local-dev") -> None:
        self.database_url = database_url
        self.tenant_slug = tenant_slug

    def _tenant(self, cur: Any) -> Any:
        cur.execute("SELECT id FROM iam.tenants WHERE slug=%s", (self.tenant_slug,))
        row = cur.fetchone()
        if row is None:
            raise ValueError(f"tenant not found: {self.tenant_slug}")
        tenant_id = row[0]
        cur.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant_id),))
        cur.fetchone()
        return tenant_id

    def list_backtests(self, limit: int = 50) -> list[dict[str, Any]]:
        """Return backtest summaries within the RLS-isolated tenant scope."""
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        with psycopg2.connect(self.database_url) as connection:
            with connection.cursor() as cur:
                tenant_id = self._tenant(cur)
                cur.execute(
                    """SELECT b.id,b.strategy_key,b.status,d.key,b.created_at,
                    d.freshness_status,
                    COALESCE(b.metrics->>'total_return','0')::double precision,
                    COALESCE(b.metrics->>'volatility','0')::double precision,
                    COALESCE(b.metrics->>'max_drawdown','0')::double precision,
                    COALESCE(d.metadata->>'row_count','0')::int
                    FROM quant.backtests b LEFT JOIN quant.datasets d ON d.id=b.dataset_id AND d.tenant_id=b.tenant_id
                    WHERE b.tenant_id=%s ORDER BY b.created_at DESC LIMIT %s""",
                    (tenant_id, limit),
                )
                return [
                    {
                        "id": str(r[0]), "strategy_key": r[1], "status": r[2], "dataset_key": r[3],
                        "created_at": r[4], "freshness_status": r[5], "total_return": r[6],
                        "volatility": r[7], "max_drawdown": r[8], "observations": int(r[9]),
                    }
                    for r in cur.fetchall()
                ]

    def get_backtest_lineage(self, backtest_id: str) -> dict[str, Any]:
        """Return the full evidence chain for one simulated backtest."""
        with psycopg2.connect(self.database_url) as connection:
            with connection.cursor() as cur:
                tenant_id = self._tenant(cur)
                cur.execute(
                    """SELECT b.id,b.dataset_id,b.strategy_key,b.parameters,b.status,b.metrics,b.created_at
                    FROM quant.backtests b WHERE b.tenant_id=%s AND b.id=%s""",
                    (tenant_id, backtest_id),
                )
                row = cur.fetchone()
                if row is None:
                    raise ValueError("quant backtest not found")
                backtest_id, dataset_id, strategy_key, parameters, status, metrics, created_at = row
                dataset: dict[str, Any] | None = None
                if dataset_id is not None:
                    cur.execute(
                        """SELECT id,key,as_of,freshness_status,metadata
                        FROM quant.datasets WHERE tenant_id=%s AND id=%s""",
                        (tenant_id, dataset_id),
                    )
                    drow = cur.fetchone()
                    if drow is not None:
                        dataset = {
                            "id": str(drow[0]), "key": drow[1], "as_of": drow[2],
                            "freshness_status": drow[3], "metadata": drow[4] or {},
                        }
                return {
                    "kind": "quant_evidence_chain",
                    "backtest_id": str(backtest_id),
                    "backtest": {
                        "id": str(backtest_id), "strategy_key": strategy_key, "status": status,
                        "parameters": parameters or {}, "metrics": metrics or {},
                        "created_at": created_at,
                    },
                    "dataset": dataset,
                }