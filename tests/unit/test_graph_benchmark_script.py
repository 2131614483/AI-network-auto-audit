from __future__ import annotations

from pathlib import Path

import pytest

from scripts.benchmark_graph_routing import require_test_database


def test_golden_query_benchmark_refuses_interactive_database() -> None:
    with pytest.raises(SystemExit, match="audit_network_test"):
        require_test_database("postgresql://audit_app:admin@localhost:5432/audit_network")
    require_test_database("postgresql://audit_app:admin@localhost:5432/audit_network_test")


def test_golden_query_benchmark_has_no_delete_statement() -> None:
    script = (Path(__file__).resolve().parents[2] / "scripts" / "benchmark_graph_routing.py").read_text(encoding="utf-8")
    assert "audit_network_test" in script
    assert "DELETE FROM" not in script
    assert "100_000" in script
    assert "1_000_000" in script
