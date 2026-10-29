"""确定性组网：合法起点判定、DAG 收敛、骨架建计划。

这个模块是"让模型凭空组网"的替代方案，**它自己出错时会伪装成结论**——本轮实测中就发生过：

* 键从 `schema_ref` 改成 `port_id` 时漏改一处 → 查生产者的字典永远返回空 →
  **所有边都退化成种子注入**，收敛结果看着像"一张网"，其实是 20 个孤立节点；
* 我一度把这个 bug 当成"最弱上游导致的必然结果"，差点写进结论。

所以这里钉住的不是"能跑"，而是**"连起来了"**：多节点 DAG 必须产出节点间边，
只用种子能喂饱的那些节点必须被识别为"起点"而不是"网"。
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from packages.ai_planner.path_search import build_dag, plan_from_dag, startable
from packages.plugin_topology.compiler import CompileError

SCHEMA = "artifact-ref.schema.json"


@dataclass(frozen=True)
class _Port:
    port_id: str
    direction: str = "input"
    schema_ref: str = SCHEMA
    required: bool = True
    cardinality: str = "one"
    schema_sha256: str | None = None


@dataclass(frozen=True)
class _Spec:
    plugin_id: str
    capability: str
    inputs: tuple = ()
    outputs: tuple = ()


def _in(port_id: str, required: bool = True) -> _Port:
    return _Port(port_id=port_id, direction="input", required=required)


def _out(port_id: str) -> _Port:
    return _Port(port_id=port_id, direction="output")


def _catalog() -> dict[str, _Spec]:
    """a → b → c 一条链，外加 d 需要两个上游（扇入）。"""
    return {
        "x.a": _Spec("x.a", "a.cap", (_in("raw"),), (_out("mid-a"),)),
        "x.b": _Spec("x.b", "b.cap", (_in("mid-a"),), (_out("mid-b"),)),
        "x.c": _Spec("x.c", "c.cap", (_in("mid-b"),), (_out("final"),)),
        "x.d": _Spec("x.d", "d.cap", (_in("mid-a"), _in("mid-b")), (_out("joined"),)),
    }


def test_startable_requires_every_required_input_to_be_seeded() -> None:
    """`ledger` 那种"看起来像入口、实则不是合法起点"的端口必须被排除。

    实测依据：种子给 `{ledger}` 时 `startable` 返回 **0** —— 因为需要 ledger 的插件
    声明的端口叫 `ledger-artifact-ref`。模型两天只组 2 个节点，根因就在这。
    """
    specs = _catalog()
    assert startable(specs, {"raw"}) == ["x.a"]
    assert startable(specs, {"ledger"}) == []          # 名字像入口但不匹配任何 required 端口
    assert startable(specs, {"raw", "mid-a"}) == ["x.a", "x.b"]


def test_dag_chains_through_node_outputs_not_only_seeds() -> None:
    """**这条就是本轮那个 bug 的回归用例。**

    只给 `raw` 播种时，正确的收敛应当沿 a → b → c 连出**两条节点间边**；
    键混用（按 schema_ref 查按 port_id 建的字典）会让它退化成"只有种子边"，
    于是 c 永远进不来，而结果看起来仍然"编译成功"。
    """
    dag = build_dag(_catalog(), {"raw"}, max_nodes=16)
    # a→b→c 是链；d 需要 a、b 两路，两者收敛后它也能进 —— 所以 4 个节点全在。
    assert dag["plugins"] == ["x.a", "x.b", "x.c", "x.d"]
    node_edges = [e for e in dag["edges"] if e["source"] != "__seed__"]
    assert len(node_edges) == 4, "必须连出节点间边，不能全是种子注入"
    assert {(e["source"], e["target"]) for e in node_edges} == {
        ("x.a", "x.b"), ("x.b", "x.c"), ("x.a", "x.d"), ("x.b", "x.d"),
    }


def test_dag_supports_fan_in_from_multiple_upstreams() -> None:
    """扇入：`x.d` 需要 a 与 b 两路产物，收敛后两条边都要在。"""
    dag = build_dag(_catalog(), {"raw"}, max_nodes=16)
    assert "x.d" in dag["plugins"]
    into_d = {e["source_port"] for e in dag["edges"] if e["target"] == "x.d"}
    assert into_d == {"mid-a", "mid-b"}


def test_dag_leaves_unreachable_nodes_out_instead_of_faking_edges() -> None:
    """种子喂不饱的节点**不进收敛集**，绝不为了"图好看"编一条边。"""
    dag = build_dag({"x.d": _Spec("x.d", "d.cap", (_in("nowhere"),), (_out("joined"),))}, {"raw"})
    assert dag["plugins"] == []
    assert dag["edges"] == []


def test_plan_from_dag_keeps_node_edges_and_seeds_separately() -> None:
    plan = plan_from_dag(_catalog(), build_dag(_catalog(), {"raw"}), {"raw"}, plan_key="t1")
    assert len(plan.nodes) == 4
    assert len(plan.edges) == 4, "节点间边必须保留；全变种子边说明绑定退化了"
    assert plan.seed_keys("raw") == (("a-000", "raw"),)


def test_multi_node_plan_must_have_edges() -> None:
    """多节点计划必须有边 —— 否则是「孤立节点」而不是「网」。

    这条断言是对上面那个 bug 的**兜底**：即使绑定的某处又退化，只要节点数 > 1 而边为 0，
    就该在测试里红，而不是在结论里被当成"编译成功"。
    """
    plan = plan_from_dag(_catalog(), build_dag(_catalog(), {"raw"}), {"raw"}, plan_key="t2")
    if len(plan.nodes) > 1:
        assert len(plan.edges) > 0


@pytest.mark.parametrize("max_nodes", [1, 2, 4, 5])
def test_dag_limits_nodes_within_one_expansion_pass(max_nodes: int) -> None:
    specs = {
        f"x.n{i}": _Spec(f"x.n{i}", f"n{i}.cap", (_in("raw"),), (_out(f"out-{i}"),))
        for i in range(5)
    }
    dag = build_dag(specs, {"raw"}, max_nodes=max_nodes)

    assert dag["plugins"] == [f"x.n{i}" for i in range(max_nodes)]
    assert {edge["target"] for edge in dag["edges"]} == set(dag["plugins"])
    assert set(dag["covered"]) == {"raw", *(f"out-{i}" for i in range(max_nodes))}


def test_dag_limits_nodes_across_expansion_passes() -> None:
    # The entry sorts last, so its dependants can only join on the second pass.
    specs = {
        "x.a": _Spec("x.a", "a.cap", (_in("mid"),), (_out("next"),)),
        "x.b": _Spec("x.b", "b.cap", (_in("next"),), (_out("final"),)),
        "x.z": _Spec("x.z", "z.cap", (_in("raw"),), (_out("mid"),)),
    }
    dag = build_dag(specs, {"raw"}, max_nodes=2)

    assert dag["plugins"] == ["x.z", "x.a"]
    assert "final" not in dag["covered"]
    assert {edge["target"] for edge in dag["edges"]} == {"x.z", "x.a"}


@pytest.mark.parametrize("max_nodes", [0, -1, True, False, 1.5, "2"])
def test_dag_rejects_invalid_node_budgets(max_nodes: object) -> None:
    with pytest.raises(ValueError, match="max_nodes.*positive integer"):
        build_dag(_catalog(), {"raw"}, max_nodes=max_nodes)  # type: ignore[arg-type]


def test_plan_from_dag_rejects_a_dag_larger_than_explicit_budget() -> None:
    dag = build_dag(_catalog(), {"raw"})
    with pytest.raises(CompileError, match="exceeding max_chain_length budget 2"):
        plan_from_dag(_catalog(), dag, {"raw"}, plan_key="limited-plan", max_nodes=2)


def test_plan_from_dag_preserves_an_explicit_budget_below_the_default() -> None:
    dag = build_dag(_catalog(), {"raw"})
    plan = plan_from_dag(_catalog(), dag, {"raw"}, plan_key="exact-budget", max_nodes=4)

    assert len(plan.nodes) == 4
    assert plan.budget["max_chain_length"] == 4


@pytest.mark.parametrize("max_nodes", [0, -1, True, False, 1.5, "4"])
def test_plan_from_dag_rejects_invalid_explicit_budgets(max_nodes: object) -> None:
    with pytest.raises(ValueError, match="max_nodes.*positive integer"):
        plan_from_dag(
            _catalog(), build_dag(_catalog(), {"raw"}), {"raw"},
            plan_key="invalid-budget", max_nodes=max_nodes,  # type: ignore[arg-type]
        )
