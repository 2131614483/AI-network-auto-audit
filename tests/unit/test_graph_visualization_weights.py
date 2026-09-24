"""画布边的**权重依据**。

原先所有边的 weight 都是常量 1 —— 固定模式，看不出哪条关系更要紧。现在权重由真实证据推出，
分四档，且每一档都必须把**依据**一起交出来：只说"权重 0.53"而不说依据，等于换个方式不说真话。

这里钉住四档的判定顺序与文案，因为顺序错了（比如契约衔接盖过运行实测）就会让界面
显示一个"看起来有依据、其实来自更弱证据"的数字。
"""

from __future__ import annotations

from packages.graph.service import _as_str_list, _visualization_edge, _visualization_node

PLUGIN_BY_NODE = {"a": "audit.plan.staff-schedule", "b": "audit.plan.effort-budget"}
PORTS_BY_NODE = {"a": (["effort-budget", "plan-draft"], []), "b": ([], ["effort-budget"])}


def _edge(relation: str = "depends_on", weight: float = 1.0, source: str = "a", target: str = "b"):
    return (source, target, relation, weight)


def test_contains_is_a_declared_hierarchy_fact() -> None:
    edge = _visualization_edge(
        _edge("contains"), plugin_by_node=PLUGIN_BY_NODE, ports_by_node=PORTS_BY_NODE, measured={},
    )
    assert edge["weight"] == 1.0
    assert edge["basis"] == "声明层级：族包含能力"


def test_measured_collaboration_beats_contract_overlap() -> None:
    """两端既有运行实测又有端口衔接时，必须用实测 —— 前者是真实发生过的证据。"""
    measured = {("audit.plan.staff-schedule", "audit.plan.effort-budget"): {"success": 20, "fail": 0, "weight": 2.5}}
    edge = _visualization_edge(
        _edge(), plugin_by_node=PLUGIN_BY_NODE, ports_by_node=PORTS_BY_NODE, measured=measured,
    )
    assert edge["basis"].startswith("经验实测")
    assert "20 成功 / 0 失败" in edge["basis"]
    assert 0 < edge["weight"] < 1


def test_measured_weight_is_normalised_not_raw() -> None:
    """经验层的 weight 是"次数×置信度×衰减"，量纲不是 0..1，必须归一化后再给界面。"""
    measured = {("audit.plan.staff-schedule", "audit.plan.effort-budget"): {"success": 31, "fail": 0, "weight": 3.08}}
    edge = _visualization_edge(
        _edge(), plugin_by_node=PLUGIN_BY_NODE, ports_by_node=PORTS_BY_NODE, measured=measured,
    )
    assert edge["weight"] == 0.77
    assert edge["weight"] <= 1.0


def test_contract_overlap_is_used_when_there_is_no_measurement() -> None:
    edge = _visualization_edge(
        _edge(), plugin_by_node=PLUGIN_BY_NODE, ports_by_node=PORTS_BY_NODE, measured={},
    )
    assert edge["basis"] == "契约衔接：共享端口 effort-budget"
    assert edge["weight"] > 0


def test_measured_entry_with_zero_attempts_falls_through_to_contract() -> None:
    """经验层里存在该键但次数为 0 时不能当成"实测过"，否则会给出一个没有内容的依据。"""
    measured = {("audit.plan.staff-schedule", "audit.plan.effort-budget"): {"success": 0, "fail": 0, "weight": 0.0}}
    edge = _visualization_edge(
        _edge(), plugin_by_node=PLUGIN_BY_NODE, ports_by_node=PORTS_BY_NODE, measured=measured,
    )
    assert edge["basis"].startswith("契约衔接")


def test_no_evidence_keeps_the_stored_weight_and_says_so() -> None:
    edge = _visualization_edge(
        _edge("related_to", 0.9), plugin_by_node={}, ports_by_node={}, measured={},
    )
    assert edge["weight"] == 0.9
    assert edge["basis"] == "声明关系：无端口衔接记录，也无运行实测"


def test_every_edge_carries_a_basis() -> None:
    for relation in ("contains", "depends_on", "related_to", "links"):
        edge = _visualization_edge(
            _edge(relation), plugin_by_node=PLUGIN_BY_NODE, ports_by_node=PORTS_BY_NODE, measured={},
        )
        assert edge["basis"].strip(), f"{relation} 没有给出权重依据"


def test_node_carries_the_properties_that_explain_relationships() -> None:
    node = _visualization_node((
        "id-1", "高风险领域定位", "capability",
        {
            "description": "筛选高风险点对应的业务领域",
            "inputs": ["risk-level"],
            "outputs": ["high-risk-area"],
            "family": "s2",
            "lifecycle": "verified",
            "name": "重复字段不该出现",
            "plugin_id": "audit.risk.high-risk-area-locate",
        },
    ))
    assert node["description"] == "筛选高风险点对应的业务领域"
    assert node["inputs"] == ["risk-level"]
    assert node["outputs"] == ["high-risk-area"]
    assert node["family"] == "s2"
    # name / plugin_id 与 label / 节点 id 重复或太细，不进画布载荷。
    assert "name" not in node and "plugin_id" not in node


def test_node_omits_empty_properties_instead_of_faking_them() -> None:
    node = _visualization_node(("id-2", "孤立节点", "control", {}))
    assert node == {"id": "id-2", "label": "孤立节点", "node_type": "control"}


def test_as_str_list_is_defensive() -> None:
    assert _as_str_list(["a", "b"]) == ["a", "b"]
    assert _as_str_list(None) == []
    assert _as_str_list("not-a-list") == []
