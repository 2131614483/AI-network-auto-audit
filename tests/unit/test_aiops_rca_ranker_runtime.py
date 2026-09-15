from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import uuid4

import pytest

from plugins.builtin.aiops_rca_ranker.runtime import InputRejected, handle


def _artifact(path: Path) -> dict[str, object]:
    content = path.read_bytes()
    return {
        "artifact_id": str(uuid4()),
        "tenant_id": str(uuid4()),
        "uri": path.resolve().as_uri(),
        "media_type": "application/json",
        "sha256": hashlib.sha256(content).hexdigest(),
        "size_bytes": len(content),
        "classification": "internal",
    }


def _envelope(incidents: Path, topology: Path, *, max_candidates: int = 10, max_hops: int = 2) -> dict[str, object]:
    return {
        "protocol": "audit-network-plugin-child-v1",
        "plugin_id": "aiops.rca-ranker",
        "capability": "aiops.rca.rank",
        "rca": {
            "incident_set": _artifact(incidents),
            "topology_graph": _artifact(topology),
            "window_minutes": 1440,
            "max_candidates": max_candidates,
            "max_hops": max_hops,
        },
    }


@pytest.fixture()
def read_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "rca-root"
    root.mkdir()
    monkeypatch.setenv("AUDIT_PLUGIN_READ_ROOTS", json.dumps([str(root)]))
    return root


def _write(root: Path, name: str, payload: object) -> Path:
    path = root / name
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _graph() -> dict[str, object]:
    return {
        "nodes_by_type": {
            "故障": [
                {"id": "n001", "text": "墨水粘度异常", "node_type": "故障", "confidence": 0.9},
                {"id": "n002", "text": "环境湿度异常", "node_type": "故障", "confidence": 0.8},
            ],
            "现象": [
                {"id": "n003", "text": "喷头堵塞", "node_type": "现象", "confidence": 0.7},
                {"id": "n004", "text": "溶剂残留", "node_type": "现象", "confidence": 0.6},
            ],
        },
        "edges": {
            "n001->n003": [{"source": "n001", "target": "n003", "confidence": 0.9, "strength": 0.8}],
            "n002->n004": [{"source": "n002", "target": "n004", "confidence": 0.8, "strength": 0.7}],
        },
    }


def _incidents() -> list[dict[str, object]]:
    return [
        {
            "ticket_id": "FT_001",
            "timestamp": "2025-08-18T15:28:50",
            "fault_description": "喷头堵塞",
            "affected_system": "环境湿度",
            "root_cause": "墨水粘度异常",
            "severity": "中",
        },
        {
            "ticket_id": "FT_002",
            "timestamp": "2025-08-19T09:00:00",
            "fault_description": "喷头堵塞",
            "affected_system": "环境湿度",
            "root_cause": "墨水粘度异常",
            "severity": "高",
        },
        {
            "ticket_id": "FT_003",
            "timestamp": "2025-08-20T11:00:00",
            "fault_description": "溶剂残留",
            "affected_system": "环境湿度",
            "root_cause": "环境湿度异常",
            "severity": "中",
        },
    ]


def test_golden_ranking_puts_most_supported_cause_first(read_roots: Path) -> None:
    incidents = _write(read_roots, "incidents.json", _incidents())
    topology = _write(read_roots, "topology.json", _graph())
    output = handle(_envelope(incidents, topology))
    assert output["contract_id"] == "rca-candidates"
    assert output["summary"]["incidents_processed"] == 3
    assert output["summary"]["candidate_count"] == 2
    assert output["summary"]["truncated"] is False
    first, second = output["candidates"]
    assert first["root_cause"] == "墨水粘度异常"
    assert first["rank"] == 1
    assert first["support_count"] >= second["support_count"]
    assert first["score"] >= second["score"]
    assert first["confidence"] <= 1.0


def test_candidates_carry_supporting_incidents_and_evidence(read_roots: Path) -> None:
    incidents = _write(read_roots, "incidents.json", _incidents())
    topology = _write(read_roots, "topology.json", _graph())
    output = handle(_envelope(incidents, topology))
    first = output["candidates"][0]
    assert set(first["supporting_incidents"]) == {"FT_001", "FT_002"}
    assert first["evidence"]["node_type"] == "故障"
    assert first["evidence"]["source_sha256"] == output["incident_set_sha256"]
    assert len(output["incident_set_sha256"]) == 64
    assert len(output["topology_sha256"]) == 64


def test_duplicate_request_with_same_immutable_input_is_idempotent(read_roots: Path) -> None:
    incidents = _write(read_roots, "incidents.json", _incidents())
    topology = _write(read_roots, "topology.json", _graph())
    first = handle(_envelope(incidents, topology))
    second = handle(_envelope(incidents, topology))
    assert first["candidates"] == second["candidates"]
    assert first["incident_set_sha256"] == second["incident_set_sha256"]


def test_tampered_incident_sha256_is_rejected(read_roots: Path) -> None:
    incidents = _write(read_roots, "incidents.json", _incidents())
    topology = _write(read_roots, "topology.json", _graph())
    envelope = _envelope(incidents, topology)
    envelope["rca"]["incident_set"]["sha256"] = "0" * 64  # type: ignore[index]
    with pytest.raises(InputRejected, match="sha256"):
        handle(envelope)


def test_tampered_topology_sha256_is_rejected(read_roots: Path) -> None:
    incidents = _write(read_roots, "incidents.json", _incidents())
    topology = _write(read_roots, "topology.json", _graph())
    envelope = _envelope(incidents, topology)
    envelope["rca"]["topology_graph"]["sha256"] = "0" * 64  # type: ignore[index]
    with pytest.raises(InputRejected, match="sha256"):
        handle(envelope)


def test_size_mismatch_is_rejected(read_roots: Path) -> None:
    incidents = _write(read_roots, "incidents.json", _incidents())
    topology = _write(read_roots, "topology.json", _graph())
    envelope = _envelope(incidents, topology)
    envelope["rca"]["topology_graph"]["size_bytes"] = 1  # type: ignore[index]
    with pytest.raises(InputRejected, match="size"):
        handle(envelope)


def test_missing_topology_graph_is_rejected(read_roots: Path) -> None:
    incidents = _write(read_roots, "incidents.json", _incidents())
    envelope = _envelope(incidents, incidents)
    envelope["rca"].pop("topology_graph")  # type: ignore[union-attr]
    with pytest.raises(InputRejected, match="missing"):
        handle(envelope)


def test_malformed_topology_without_nodes_is_rejected(read_roots: Path) -> None:
    incidents = _write(read_roots, "incidents.json", _incidents())
    topology = _write(read_roots, "bad-topology.json", {"edges": {}})
    with pytest.raises(InputRejected, match="nodes_by_type"):
        handle(_envelope(incidents, topology))


def test_max_hops_outside_budget_is_rejected(read_roots: Path) -> None:
    incidents = _write(read_roots, "incidents.json", _incidents())
    topology = _write(read_roots, "topology.json", _graph())
    with pytest.raises(InputRejected, match="max_hops"):
        handle(_envelope(incidents, topology, max_hops=4))


def test_candidate_cap_marks_summary_truncated(read_roots: Path) -> None:
    incidents = _write(read_roots, "incidents.json", _incidents())
    topology = _write(read_roots, "topology.json", _graph())
    output = handle(_envelope(incidents, topology, max_candidates=1))
    assert output["summary"]["candidate_count"] == 1
    assert output["summary"]["truncated"] is True


def test_artifact_outside_declared_read_roots_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUDIT_PLUGIN_READ_ROOTS", json.dumps([str(tmp_path / "allowed")]))
    incidents = tmp_path / "incidents-outside.json"
    incidents.write_text(json.dumps(_incidents()), encoding="utf-8")
    topology = tmp_path / "topology-outside.json"
    topology.write_text(json.dumps(_graph()), encoding="utf-8")
    with pytest.raises(InputRejected, match="read roots"):
        handle(_envelope(incidents, topology))


def test_environment_without_read_roots_is_rejected(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("AUDIT_PLUGIN_READ_ROOTS", raising=False)
    incidents = tmp_path / "incidents.json"
    incidents.write_text(json.dumps(_incidents()), encoding="utf-8")
    topology = tmp_path / "topology.json"
    topology.write_text(json.dumps(_graph()), encoding="utf-8")
    with pytest.raises(InputRejected, match="read roots"):
        handle(_envelope(incidents, topology))
