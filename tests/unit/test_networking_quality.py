"""计划质量门：拦"数据配不上这张网"。

判据必须分成两类并且界线清楚 —— 确定性事实（喂不饱的网）与人工口径（规模相称）。
混在一起会让口径看起来像事实，而口径是会随经验改的。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from packages.ops.networking_quality import assess_plan, load_policy


@dataclass
class _Port:
    port_id: str
    required: bool = True


@dataclass
class _Node:
    node_instance_id: str
    input_ports: tuple = ()
    output_ports: tuple = ()


@dataclass
class _Edge:
    source_instance: str
    source_port: str
    target_instance: str
    target_port: str


@dataclass
class _Plan:
    nodes: tuple = ()
    edges: tuple = ()
    seed_inputs: set = field(default_factory=set)


def _two_node_plan(seed_bound: bool = True) -> _Plan:
    ledger = _Node("ledger-a", (_Port("ledger"),))
    consumer = _Node("consumer-x", (_Port("experiment"),))
    return _Plan(
        nodes=(ledger, consumer),
        edges=(_Edge("ledger-a", "candidates", "consumer-x", "experiment"),),
        seed_inputs={("ledger-a", "ledger")} if seed_bound else set(),
    )


def test_bound_two_node_plan_passes_on_healthy_data() -> None:
    result = assess_plan(_two_node_plan(), seed_rows=121)
    assert result["ok"] is True
    assert result["issues"] == []


def test_unbound_required_input_is_a_hard_rejection() -> None:
    """必填输入既无上游边也无种子 → 这张网根本喂不饱，确定性事实，直接拒。"""
    result = assess_plan(_two_node_plan(seed_bound=False), seed_rows=121)
    assert result["ok"] is False
    assert [i["code"] for i in result["issues"]] == ["unbound_required_input"]
    assert "ledger" in result["issues"][0]["detail"]


def test_empty_seed_is_a_hard_rejection() -> None:
    result = assess_plan(_two_node_plan(), seed_rows=0)
    assert result["ok"] is False
    assert "empty_seed" in [i["code"] for i in result["issues"]]


def test_six_rows_with_two_nodes_is_rejected() -> None:
    """**回归用例**：2026-09-26 实测中，6 行的月份被组了 2 个节点、下游评估器 failed。

    这条口径（每多一个节点至少 8 行）就是从那 5 次实测的失配边界里取的：
    6 行失败、10 行起成功。质量门本该在那次执行前把它拦下。
    """
    result = assess_plan(_two_node_plan(), seed_rows=6)
    assert result["ok"] is False
    assert "rows_too_few_for_nodes" in [i["code"] for i in result["issues"]]


def test_ten_rows_with_two_nodes_passes() -> None:
    """同一批实测里 10 行的两个月都成功了 —— 门不能把它们也拦掉。"""
    assert assess_plan(_two_node_plan(), seed_rows=10)["ok"] is True


def test_single_node_plan_is_not_subject_to_the_extra_node_rule() -> None:
    plan = _Plan(nodes=(_Node("ledger-a", (_Port("ledger"),)),), seed_inputs={("ledger-a", "ledger")})
    assert assess_plan(plan, seed_rows=1)["ok"] is True


def test_node_count_range_is_enforced() -> None:
    plan = _Plan(nodes=tuple(_Node(f"n{i}") for i in range(20)), edges=(), seed_inputs=set())
    codes = [i["code"] for i in assess_plan(plan, seed_rows=1000)["issues"]]
    assert "node_count_out_of_range" in codes


def test_every_issue_carries_a_code_and_detail() -> None:
    """调用方要据此重规划，所以每条必须能读、能定位。"""
    for rows in (0, 6):
        for issue in assess_plan(_two_node_plan(), seed_rows=rows)["issues"]:
            assert issue["code"] and issue["detail"]


def test_missing_policy_file_falls_back_to_declared_defaults(tmp_path) -> None:
    """声明读不到时**不能静默放行** —— 退回内置缺省值并继续判。"""
    policy = load_policy(tmp_path / "nope.json")
    assert policy["min_seed_rows"] >= 1
    assert policy["max_planned_nodes"] > 0


def test_shipped_policy_documents_its_basis() -> None:
    """随包的策略必须带依据 —— 口径不带依据，下一个人只能靠猜。"""
    import json
    from pathlib import Path

    raw = json.loads(Path("contracts/networking-quality-gate.json").read_text(encoding="utf-8"))
    assert "_min_rows_per_extra_node" in raw["policy"]
    assert "实测" in raw["policy"]["_min_rows_per_extra_node"]
