from __future__ import annotations

import os
from pathlib import Path

import pytest

from packages.quant.backtest import run_csv_backtest

DB = os.getenv("AUDIT_NETWORK_TEST_DATABASE_URL", "postgresql://audit_app:admin@localhost:5432/audit_network")


def test_simulated_backtest_is_persisted(tmp_path: Path) -> None:
    prices = tmp_path / "prices.csv"
    prices.write_text("date,close\n2026-01-01,100\n2026-01-02,105\n2026-01-03,102\n2026-01-04,108\n", encoding="utf-8")
    result = run_csv_backtest(DB, prices)
    assert result.observations == 4
    assert result.simulated_only is True
    assert -1 < result.total_return < 1


def test_backtest_rejects_non_point_in_time_dates(tmp_path: Path) -> None:
    prices = tmp_path / "unordered.csv"
    prices.write_text("date,close\n2026-01-02,100\n2026-01-01,105\n2026-01-03,102\n", encoding="utf-8")
    with pytest.raises(ValueError, match="ascending"):
        run_csv_backtest(DB, prices)
