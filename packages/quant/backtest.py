from __future__ import annotations

import csv
import hashlib
import math
from dataclasses import dataclass
from pathlib import Path

import psycopg2
from psycopg2.extras import Json, register_uuid

register_uuid()  # type: ignore[no-untyped-call]


@dataclass(frozen=True, slots=True)
class BacktestResult:
    backtest_id: str
    observations: int
    total_return: float
    annualized_volatility: float
    max_drawdown: float
    simulated_only: bool = True


def run_csv_backtest(database_url: str, csv_path: str | Path, strategy_key: str = "daily_momentum") -> BacktestResult:
    path = Path(csv_path).resolve()
    if path.suffix.lower() != ".csv" or not path.is_file():
        raise ValueError("price input must be an existing CSV file")
    with path.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    prices = [float(str(row.get("close", row.get("收盘", ""))).replace(",", "")) for row in rows]
    if len(prices) < 3 or any(price <= 0 for price in prices):
        raise ValueError("price CSV requires at least three positive close prices")
    dates = [str(row.get("date", row.get("日期", ""))) for row in rows]
    if any(not value for value in dates) or dates != sorted(dates) or len(set(dates)) != len(dates):
        raise ValueError("price CSV dates must be present, unique, and ascending")
    returns: list[float] = []
    # Signal at t-1 is formed only from prices through t-1 and is applied to
    # the t return.  Using price[t] here would make every realized return look
    # favorable and violates the point-in-time / future-function gate.
    for index in range(2, len(prices)):
        position = 1.0 if prices[index - 1] >= prices[index - 2] else -1.0
        returns.append(position * (prices[index] / prices[index - 1] - 1.0))
    equity = 1.0
    peak = 1.0
    max_drawdown = 0.0
    for daily_return in returns:
        equity *= 1.0 + daily_return
        peak = max(peak, equity)
        max_drawdown = min(max_drawdown, equity / peak - 1.0)
    mean = sum(returns) / len(returns)
    variance = sum((value - mean) ** 2 for value in returns) / max(1, len(returns) - 1)
    volatility = math.sqrt(variance) * math.sqrt(252)
    snapshot_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
    code_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    with psycopg2.connect(database_url) as connection:
        with connection.cursor() as cur:
            cur.execute("SELECT id FROM iam.tenants WHERE slug='local-dev'")
            tenant_row = cur.fetchone()
            if tenant_row is None:
                raise ValueError("tenant not found: local-dev")
            tenant_id = tenant_row[0]
            cur.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),))
            cur.execute(
                """INSERT INTO quant.datasets(tenant_id,key,as_of,freshness_status,metadata) VALUES(%s,%s,now(),'fresh',%s)
                ON CONFLICT(tenant_id,key) DO UPDATE SET as_of=EXCLUDED.as_of, freshness_status='fresh', metadata=EXCLUDED.metadata RETURNING id""",
                (
                    tenant_id,
                    f"csv:{path.name}",
                    Json({"source_sha256": snapshot_sha256, "row_count": len(rows), "classification": "public"}),
                ),
            )
            dataset_row = cur.fetchone()
            if dataset_row is None:
                raise RuntimeError("dataset upsert returned no id")
            dataset_id = dataset_row[0]
            cur.execute(
                """INSERT INTO quant.backtests(tenant_id,dataset_id,strategy_key,parameters,status,metrics)
                VALUES(%s,%s,%s,%s,'completed',%s) RETURNING id""",
                (
                    tenant_id,
                    dataset_id,
                    strategy_key,
                    Json({"data_snapshot_sha256": snapshot_sha256, "code_sha256": code_hash, "point_in_time_gate": "passed"}),
                    Json({"total_return": equity - 1.0, "volatility": volatility, "max_drawdown": max_drawdown, "simulated_only": True}),
                ),
            )
            backtest_row = cur.fetchone()
            if backtest_row is None:
                raise RuntimeError("backtest insert returned no id")
            backtest_id = backtest_row[0]
    return BacktestResult(str(backtest_id), len(prices), equity - 1.0, volatility, max_drawdown)
