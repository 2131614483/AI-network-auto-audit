from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import uuid4

import pytest

from plugins.builtin.audit_evidence_lineage.runtime import InputRejected, handle

RELEASE_SHA256 = "c3d4e5f67890a1b2c3d4e5f67890a1b2c3d4e5f67890a1b2c3d4e5f67890abc1"


def _graph(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "contract_id": "released-graph",
        "contract_version": "1.0.0",
        "release_id": "rel-L2-001",
        "space_level": "L2",
        "release_sha256": RELEASE_SHA256,
        "nodes": [
            {"node_id": "doc-102", "node_type": "document", "label": "BCSA 大案例 01 故障工单", "source_ref": "file:///G:/data/fault_tickets.json"},
            {"node_id": "ev-77", "node_type": "evidence", "label": "工单 102-7 温度越限记录"},
            {"node_id": "cl-5", "node_type": "claim", "label": "设备 12 过温停机导致告警", "source_ref": "rule:overheat_shutdown@v3"},
            {"node_id": "fi-1", "node_type": "finding", "label": "待复核：过温停机候选发现", "source_ref": "review:human"},
        ],
        "edges": [
            {"source_id": "ev-77", "target_id": "doc-102", "edge_type": "part_of"},
            {"source_id": "cl-5", "target_id": "ev-77", "edge_type": "supports", "confidence": 0.91},
            {"source_id": "fi-1", "target_id": "cl-5", "edge_type": "derived_from", "confidence": 0.8},
        ],
    }
    payload.update(overrides)
    return payload


def _artifact(path: Path) -> dict[str, object]:
    content = path.read_bytes()
    return {
        "artifact_id": str(uuid4()),
        "tenant_id": str(uuid4()),
        "uri": path.resolve().as_uri(),
        "media_type": "application/json",
        "sha256": hashlib.sha256(content).hexdigest(),
        "size_bytes": len(content),
        "classification": "audit_confidential",
    }


def _graph_ref(path: Path, **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "contract_id": "released-graph-ref",
        "contract_version": "1.0.0",
        "artifact": _artifact(path),
        "space_level": "L2",
        "release_sha256": RELEASE_SHA256,
        "release_time": "2026-09-05T02:00:00+08:00",
        "budget": {"max_nodes": 10000, "max_edges": 50000, "max_hops": 3, "timeout_seconds": 60},
    }
    payload.update(overrides)
    return payload


def _envelope(path: Path, **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "protocol": "audit-network-plugin-child-v1",
        "plugin_id": "audit.evidence-lineage",
        "capability": "audit.evidence.lineage",
        "lineage": {"graph_ref": _graph_ref(path)},
    }
    payload.update(overrides)
    return payload


@pytest.fixture()
def read_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "lineage-root"
    root.mkdir()
    monkeypatch.setenv("AUDIT_PLUGIN_READ_ROOTS", json.dumps([str(root)]))
    return root


def _write(root: Path, name: str, graph: dict[str, object]) -> Path:
    path = root / name
    path.write_text(json.dumps(graph, ensure_ascii=False), encoding="utf-8")
    return path


def test_lineage_query_produces_a_budgeted_read_only_result(read_roots: Path) -> None:
    graph_path = _write(read_roots, "L2-evidence.graph.json", _graph())
    output = handle(_envelope(graph_path))

    assert output["contract_id"] == "evidence-lineage"
    assert output["contract_version"] == "1.0.0"
    assert len(output["lineage_id"]) == 16
    assert output["release_sha256"] == RELEASE_SHA256.lower()
    assert output["truncated"] is False
    assert output["node_count"] == 4
    assert output["edge_count"] == 3
    node_ids = {node["node_id"] for node in output["nodes"]}
    assert node_ids == {"doc-102", "ev-77", "cl-5", "fi-1"}
    assert output["summary"] == {"evidence_count": 1, "claim_count": 1, "finding_count": 1}
    assert "graph_sha256" in output["provenance"]


def test_lineage_query_is_deterministic_over_the_same_release(read_roots: Path) -> None:
    first = handle(_envelope(_write(read_roots, "a.json", _graph())))
    second = handle(_envelope(_write(read_roots, "b.json", _graph())))
    assert first["lineage_id"] == second["lineage_id"]
    assert first["nodes"] == second["nodes"]
    assert first["edges"] == second["edges"]
    assert first["truncated"] == second["truncated"]


def test_hop_budget_bounds_the_lineage_without_marking_truncation(read_roots: Path) -> None:
    graph_path = _write(read_roots, "far.json", _graph())
    envelope = _envelope(graph_path)
    envelope["lineage"]["graph_ref"]["budget"]["max_hops"] = 1  # type: ignore[index]
    output = handle(envelope)

    # hop=1 从 evidence 出发只能到达 claim 与 document；finding 在 2 跳外
    # 跳数预算是调用方声明的语义边界，命中该边界是完整结果，不算资源截断
    assert output["truncated"] is False
    node_ids = {node["node_id"] for node in output["nodes"]}
    assert "fi-1" not in node_ids
    assert "ev-77" in node_ids and "cl-5" in node_ids and "doc-102" in node_ids


def test_node_budget_truncation_is_marked(read_roots: Path) -> None:
    graph_path = _write(read_roots, "tight.json", _graph())
    envelope = _envelope(graph_path)
    envelope["lineage"]["graph_ref"]["budget"]["max_nodes"] = 1  # type: ignore[index]
    output = handle(envelope)

    # 只放得下种子 evidence 节点，其余邻居超出 max_nodes 预算
    assert output["truncated"] is True
    assert output["node_count"] == 1
    assert [node["node_id"] for node in output["nodes"]] == ["ev-77"]


def test_release_hash_mismatch_is_rejected(read_roots: Path) -> None:
    graph_path = _write(read_roots, "mismatch.json", _graph())
    envelope = _envelope(graph_path)
    envelope["lineage"]["graph_ref"]["release_sha256"] = "f" * 64  # type: ignore[index]
    with pytest.raises(InputRejected, match="release_sha256"):
        handle(envelope)


def test_graph_sha256_mismatch_is_rejected(read_roots: Path) -> None:
    graph_path = _write(read_roots, "tampered.json", _graph())
    envelope = _envelope(graph_path)
    envelope["lineage"]["graph_ref"]["artifact"]["sha256"] = "f" * 64  # type: ignore[index]
    with pytest.raises(InputRejected, match="sha256"):
        handle(envelope)


def test_space_level_mismatch_is_rejected(read_roots: Path) -> None:
    graph_path = _write(read_roots, "space.json", _graph())
    envelope = _envelope(graph_path)
    envelope["lineage"]["graph_ref"]["space_level"] = "L4"  # type: ignore[index]
    with pytest.raises(InputRejected, match="space_level"):
        handle(envelope)


def test_unknown_edge_reference_is_rejected(read_roots: Path) -> None:
    graph = _graph()
    graph["edges"].append({"source_id": "fi-1", "target_id": "ghost-9", "edge_type": "references"})  # type: ignore[union-attr]
    graph_path = _write(read_roots, "ghost.json", graph)
    with pytest.raises(InputRejected, match="unknown node"):
        handle(_envelope(graph_path))


def test_invalid_budget_is_rejected(read_roots: Path) -> None:
    graph_path = _write(read_roots, "budget.json", _graph())
    envelope = _envelope(graph_path)
    envelope["lineage"]["graph_ref"]["budget"]["max_hops"] = 0  # type: ignore[index]
    with pytest.raises(InputRejected, match="max_hops"):
        handle(envelope)


def test_no_evidence_seeds_is_rejected_as_truncated(read_roots: Path) -> None:
    graph = _graph()
    graph["nodes"] = [  # type: ignore[assignment]
        {"node_id": "fi-9", "node_type": "finding", "label": "无证据支撑的孤立发现"}
    ]
    graph["edges"] = []  # type: ignore[assignment]
    graph_path = _write(read_roots, "empty-seed.json", graph)
    output = handle(_envelope(graph_path))
    assert output["truncated"] is True
    assert output["node_count"] == 0
    assert output["edge_count"] == 0


def test_path_escape_outside_read_root_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "lineage-root"
    root.mkdir()
    monkeypatch.setenv("AUDIT_PLUGIN_READ_ROOTS", json.dumps([str(root)]))
    outside = tmp_path / "outside"
    outside.mkdir()
    graph_path = _write(outside, "graph.json", _graph())
    with pytest.raises(InputRejected, match="outside declared read roots"):
        handle(_envelope(graph_path))


def test_wrong_plugin_identity_is_rejected(read_roots: Path) -> None:
    envelope = _envelope(_write(read_roots, "event.json", _graph()))
    envelope["plugin_id"] = "audit.ledger-quality"
    with pytest.raises(InputRejected, match="plugin identity"):
        handle(envelope)
