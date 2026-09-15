"""Contract tests for the dynamic knowledge-nebula graph builder.

The hard requirement these lock in: a newly added ``audit-*`` plugin folder
must surface in the nebula graph automatically — without editing any frontend
table — and must never be silently dropped.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from packages.ai_planner.composer import discover_plugins
from packages.ai_planner.nebula_graph import (
    TRUNK,
    UNGROUPED_STAGE,
    build_nebula_graph,
    classify,
    group_segment,
)

VALID_LAYERS = {"gov", "base", "biz"}


def _write_protocol(root: Path, folder: str, payload: dict) -> Path:
    plugin_dir = root / folder
    plugin_dir.mkdir(parents=True, exist_ok=True)
    path = plugin_dir / "plugin.protocol.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _protocol(plugin_id: str, lifecycle: str, name: str = "测试插件") -> dict:
    return {
        "protocol_version": "1.0.0",
        "id": plugin_id,
        "version": "0.1.0",
        "name": name,
        "lifecycle": lifecycle,
        "capabilities": [
            {"id": plugin_id, "inputs": [], "outputs": []},
        ],
    }


def test_group_segment_handles_three_and_two_segment_ids() -> None:
    assert group_segment("audit.field.workpaper-build") == "field"
    assert group_segment("audit.evidence-lineage") == "evidence"
    assert group_segment("audit.govern.rule-iteration") == "govern"


@pytest.mark.parametrize(
    "plugin_id,layer,stage",
    [
        ("audit.mandate.demand-collect", "biz", "s1"),
        ("audit.risk.matrix-build", "biz", "s2"),
        ("audit.field.workpaper-build", "biz", "s4"),
        ("audit.evidence-lineage", "biz", "s5"),
        ("audit.report-draft", "biz", "s7"),
        ("audit.govern.rule-iteration", "gov", None),
        ("audit.foundation.ocr-extract", "base", None),
        ("audit.zzz.unknown-thing", "biz", UNGROUPED_STAGE),
    ],
)
def test_classify_mapping(plugin_id: str, layer: str, stage: str | None) -> None:
    assert classify(plugin_id) == (layer, stage)


def test_every_discovered_plugin_is_included_once() -> None:
    specs = discover_plugins()
    graph = build_nebula_graph()
    node_ids = [n["id"] for n in graph["nodes"]]
    assert len(node_ids) == len(specs)
    assert set(node_ids) == set(specs.keys())
    assert len(node_ids) == len(set(node_ids))


def test_node_shape_and_layers_are_consistent() -> None:
    graph = build_nebula_graph()
    for node in graph["nodes"]:
        assert set(node) == {
            "id", "name", "capability", "layer", "stage", "lifecycle", "ctrl",
            "inputs", "outputs", "invokes", "version", "derived_from",
        }
        assert node["layer"] in VALID_LAYERS
        assert node["name"]
        # Every shipped plugin declares a version; none declares a base yet.
        assert node["version"]
        assert isinstance(node["derived_from"], str)
        if node["layer"] == "biz":
            assert node["stage"] is not None and node["stage"].startswith("s")
        else:
            assert node["stage"] is None


def test_shipped_directory_has_no_ungrouped_plugin() -> None:
    """All current plugins must classify into a known stage (mapping complete)."""
    graph = build_nebula_graph()
    assert graph["stats"]["ungrouped"] == 0
    assert all(n["stage"] != UNGROUPED_STAGE for n in graph["nodes"])


def test_verified_only_restricts_to_runnable() -> None:
    graph = build_nebula_graph(lifecycle="verified")
    assert graph["stats"]["total"] == 100
    assert graph["stats"]["by_layer"] == {"biz": 76, "base": 18, "gov": 6}
    assert all(n["lifecycle"] == "verified" for n in graph["nodes"])


def test_cluster_edges_and_trunk() -> None:
    graph = build_nebula_graph(lifecycle="verified")
    biz_count = graph["stats"]["by_layer"]["biz"]
    cluster = [e for e in graph["edges"] if e["type"] == "cluster"]
    assert len(cluster) == biz_count
    # every cluster edge runs from a stage heart to one of its plugins
    for edge in cluster:
        assert edge["source"].startswith("s")
        node = next(n for n in graph["nodes"] if n["id"] == edge["target"])
        assert node["stage"] == edge["source"]
    # trunk is a closed ring s1..s8 -> gov -> s1
    assert [(t["source"], t["target"]) for t in graph["trunk"]] == TRUNK
    assert TRUNK[-1] == ("gov", "s1")


def test_new_contract_only_plugin_appears_automatically(tmp_path: Path) -> None:
    """Core requirement: drop a brand-new plugin folder -> it shows up."""
    _write_protocol(
        tmp_path,
        "audit_zzz_brand_new",
        _protocol("audit.zzz.brand-new", "contract_only", "全新未知阶段插件"),
    )
    graph = build_nebula_graph(plugin_root=tmp_path)
    assert graph["stats"]["total"] == 1
    node = graph["nodes"][0]
    assert node["id"] == "audit.zzz.brand-new"
    assert node["name"] == "全新未知阶段插件"
    assert node["lifecycle"] == "contract_only"
    # unknown segment is kept visible in the ungrouped fallback, never dropped
    assert node["layer"] == "biz" and node["stage"] == UNGROUPED_STAGE
    assert any(s["key"] == UNGROUPED_STAGE for s in graph["stages"])


def test_new_field_plugin_auto_joins_stage_cluster(tmp_path: Path) -> None:
    """A plugin under a known segment auto-joins that stage's electron cloud."""
    _write_protocol(
        tmp_path,
        "audit_field_auto_new",
        _protocol("audit.field.auto-new", "verified", "自动入簇新插件"),
    )
    graph = build_nebula_graph(plugin_root=tmp_path, lifecycle="verified")
    node = graph["nodes"][0]
    assert node["stage"] == "s4"
    assert any(
        e["source"] == "s4" and e["target"] == "audit.field.auto-new"
        for e in graph["edges"]
    )


def _protocol_with_ports(plugin_id: str, inputs, outputs, lifecycle: str = "verified", name: str = "端口插件", invokes=None) -> dict:
    capability = {
        "id": plugin_id,
        "inputs": [{"contract_id": c, "schema_ref": f"{c}.schema.json"} for c in inputs],
        "outputs": [{"contract_id": c, "schema_ref": f"{c}.schema.json"} for c in outputs],
    }
    if invokes:
        capability["invokes"] = list(invokes)
    return {
        "protocol_version": "1.0.0", "id": plugin_id, "version": "0.1.0",
        "name": name, "lifecycle": lifecycle,
        "capabilities": [capability],
    }


def test_dataflow_edge_wires_output_contract_to_matching_input(tmp_path: Path) -> None:
    _write_protocol(tmp_path, "audit_field_producer",
                    _protocol_with_ports("audit.field.producer", [], ["demo-artifact"]))
    _write_protocol(tmp_path, "audit_evidence_consumer",
                    _protocol_with_ports("audit.evidence.consumer", ["demo-artifact"], []))
    _write_protocol(tmp_path, "audit_report_unrelated",
                    _protocol_with_ports("audit.report.unrelated", ["other-contract"], []))
    graph = build_nebula_graph(plugin_root=tmp_path)
    flow = [e for e in graph["edges"] if e["type"] == "dataflow"]
    # producer -> the one consumer that declares the same contract
    assert {"source": "audit.field.producer", "target": "audit.evidence.consumer",
            "type": "dataflow", "contract": "demo-artifact"} in flow
    # unrelated consumer (different contract) is not wired; no self loop either
    assert not any(e["target"] == "audit.report.unrelated" for e in flow)
    assert all(e["source"] != e["target"] for e in graph["edges"])


def test_shipped_dataflow_edges_are_port_consistent() -> None:
    graph = build_nebula_graph()
    by_id = {n["id"]: n for n in graph["nodes"]}
    flow = [e for e in graph["edges"] if e["type"] == "dataflow"]
    assert len(flow) >= 60  # locked-in: the shipped directory carries a rich subject graph
    seen = set()
    for edge in flow:
        assert edge["contract"]
        assert edge["contract"] in by_id[edge["source"]]["outputs"]
        assert edge["contract"] in by_id[edge["target"]]["inputs"]
        key = (edge["source"], edge["target"], edge["contract"])
        assert key not in seen  # no duplicate triple
        seen.add(key)
    assert graph["stats"]["dataflow_edges"] == len(flow)
    # nodes echo their port contracts
    sample = by_id["audit.field.workpaper-build"]
    assert "workpaper-draft" in sample["outputs"]


def test_cross_edge_wires_invokes_to_foundation_only(tmp_path: Path) -> None:
    # 支撑层能力
    _write_protocol(tmp_path, "audit_foundation_cap",
                    _protocol_with_ports("audit.foundation.cap", ["req"], ["cap-out"]))
    # 业务插件：调用上面的支撑能力 + 一个非支撑插件 + 一个不存在的 id
    _write_protocol(tmp_path, "audit_field_caller",
                    _protocol_with_ports(
                        "audit.field.caller", ["in"], ["out"],
                        invokes=["audit.foundation.cap", "audit.risk.other", "audit.missing.x"]))
    _write_protocol(tmp_path, "audit_risk_other",
                    _protocol_with_ports("audit.risk.other", ["x"], ["y"]))
    graph = build_nebula_graph(plugin_root=tmp_path)
    cross = [e for e in graph["edges"] if e["type"] == "cross"]
    # 只有指向真实存在支撑层的那一条
    assert cross == [{
        "source": "audit.field.caller", "target": "audit.foundation.cap",
        "type": "cross", "contract": "",
    }]
    caller = next(n for n in graph["nodes"] if n["id"] == "audit.field.caller")
    assert caller["invokes"] == ["audit.foundation.cap", "audit.missing.x", "audit.risk.other"]


def test_shipped_cross_edges_target_support_layer() -> None:
    graph = build_nebula_graph()
    by_id = {n["id"]: n for n in graph["nodes"]}
    cross = [e for e in graph["edges"] if e["type"] == "cross"]
    assert len(cross) == 34  # 回填自已评审的 100 插件网络设计
    for edge in cross:
        assert by_id[edge["source"]]["layer"] in {"biz", "gov"}
        assert by_id[edge["target"]]["layer"] == "base"
    assert graph["stats"]["cross_edges"] == len(cross)
    photo = by_id["audit.field.evidence-photo"]
    assert photo["invokes"] == ["audit.foundation.ocr-extract"]


# -- derivation layer ----------------------------------------------------------


def _derived(plugin_id: str, base_id: str, name: str = "衍生插件") -> dict:
    protocol = _protocol(plugin_id, "verified", name)
    protocol["provenance"] = {
        "source_refs": ["docs/test.md"],
        "derived_from": {
            "plugin_id": base_id, "version": "0.1.0", "descriptor_sha256": "a" * 64,
        },
    }
    return protocol


def test_derivation_shows_as_an_edge_and_a_node_field(tmp_path: Path) -> None:
    """A derived plugin carries its base on the node and as a `derivation` edge."""
    _write_protocol(tmp_path, "audit_field_base", _protocol("audit.field.base", "verified"))
    _write_protocol(
        tmp_path, "audit_evidence_derived", _derived("audit.evidence.derived", "audit.field.base")
    )

    graph = build_nebula_graph(plugin_root=tmp_path)
    by_id = {n["id"]: n for n in graph["nodes"]}
    assert by_id["audit.evidence.derived"]["derived_from"] == "audit.field.base"
    assert by_id["audit.field.base"]["derived_from"] == ""
    assert by_id["audit.evidence.derived"]["version"] == "0.1.0"
    assert [e for e in graph["edges"] if e["type"] == "derivation"] == [
        {"source": "audit.evidence.derived", "target": "audit.field.base",
         "type": "derivation", "contract": ""}
    ]
    assert graph["stats"]["derivation_edges"] == 1


def test_a_base_outside_the_graph_draws_no_dangling_edge(tmp_path: Path) -> None:
    """The catalog spans domains; an edge to a plugin this graph does not
    contain would dangle, so it is dropped (the node field still shows it)."""
    _write_protocol(
        tmp_path, "audit_evidence_orphan", _derived("audit.evidence.orphan", "quant.some.base")
    )

    graph = build_nebula_graph(plugin_root=tmp_path)
    assert [e for e in graph["edges"] if e["type"] == "derivation"] == []
    assert graph["stats"]["derivation_edges"] == 0
    # The node still reports where it came from — it is just not drawn here.
    assert graph["nodes"][0]["derived_from"] == "quant.some.base"
