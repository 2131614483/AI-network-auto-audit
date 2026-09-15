from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import uuid4

import pytest

from plugins.builtin.aiops_playbook_proposer.runtime import InputRejected, handle


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


def _envelope(
    rca_candidates: Path,
    *,
    max_playbooks: int = 5,
    canary_scope: str = "single_node",
) -> dict[str, object]:
    return {
        "protocol": "audit-network-plugin-child-v1",
        "plugin_id": "aiops.playbook-proposer",
        "capability": "aiops.remediation.propose",
        "remediation": {
            "rca_candidates": _artifact(rca_candidates),
            "max_playbooks": max_playbooks,
            "canary_scope": canary_scope,
        },
    }


@pytest.fixture()
def read_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "remediation-root"
    root.mkdir()
    monkeypatch.setenv("AUDIT_PLUGIN_READ_ROOTS", json.dumps([str(root)]))
    return root


def _write_rca(root: Path, *, name: str = "rca.json", candidates: list[dict[str, object]]) -> Path:
    payload = {
        "contract_id": "rca-candidates",
        "contract_version": "1.0.0",
        "incident_set_sha256": "a" * 64,
        "topology_sha256": "b" * 64,
        "summary": {"incidents_processed": 3, "nodes_considered": 5, "candidate_count": len(candidates), "truncated": False},
        "candidates": candidates,
    }
    path = root / name
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _candidates() -> list[dict[str, object]]:
    return [
        {
            "rank": 1,
            "candidate_id": "RC-0001",
            "root_cause": "数据库连接池耗尽导致查询超时",
            "node_ref": "n001",
            "score": 0.85,
            "confidence": 0.9,
            "support_count": 12,
            "supporting_incidents": ["INC-001", "INC-002"],
            "evidence": {"node_type": "故障", "source_sha256": "c" * 64},
        },
        {
            "rank": 2,
            "candidate_id": "RC-0002",
            "root_cause": "应用服务无响应疑似内存泄漏",
            "node_ref": "n002",
            "score": 0.7,
            "confidence": 0.8,
            "support_count": 8,
            "supporting_incidents": ["INC-002"],
            "evidence": {"node_type": "故障", "source_sha256": "c" * 64},
        },
    ]


def test_playbook_proposal_selects_matched_playbooks(read_roots: Path) -> None:
    rca_path = _write_rca(read_roots, candidates=_candidates())
    output = handle(_envelope(rca_path))
    assert output["contract_id"] == "remediation-proposal"
    assert output["status"] == "proposed"
    playbook_ids = [proposal["playbook_id"] for proposal in output["proposals"]]
    assert "db-connection-recovery" in playbook_ids
    assert output["proposals"][0]["candidate_rank"] == 1
    assert output["summary"]["canary_suggested"] is True
    assert output["constraints"] == {
        "no_shell": True,
        "no_change_request": True,
        "no_auto_execution": True,
        "requires_human_approval": True,
    }
    assert all(action["action_type"] != "shell_exec" for proposal in output["proposals"] for action in proposal["actions"])


def test_proposal_is_deterministic(read_roots: Path) -> None:
    rca_path = _write_rca(read_roots, candidates=_candidates())
    first = handle(_envelope(rca_path))
    second = handle(_envelope(rca_path))
    assert first["proposal_id"] == second["proposal_id"]
    assert first["proposals"] == second["proposals"]


def test_max_playbooks_caps_proposals(read_roots: Path) -> None:
    rca_path = _write_rca(read_roots, candidates=_candidates())
    output = handle(_envelope(rca_path, max_playbooks=1))
    assert len(output["proposals"]) == 1
    assert output["summary"]["truncated"] is True


def test_canary_scope_is_applied(read_roots: Path) -> None:
    rca_path = _write_rca(read_roots, candidates=_candidates())
    output = handle(_envelope(rca_path, canary_scope="low_traffic"))
    assert all(proposal["canary"]["scope"] == "low_traffic" for proposal in output["proposals"])


def test_no_matching_keywords_yields_empty_proposals(read_roots: Path) -> None:
    rca_path = _write_rca(read_roots, candidates=[{"rank": 1, "candidate_id": "RC-0001", "root_cause": "完全无关的内容", "node_ref": "n001", "score": 0.9, "confidence": 0.9, "support_count": 5, "supporting_incidents": ["INC-001"], "evidence": {}}])
    output = handle(_envelope(rca_path))
    assert output["proposals"] == []
    assert output["summary"]["playbook_count"] == 0


def test_incident_refs_are_deduplicated(read_roots: Path) -> None:
    rca_path = _write_rca(read_roots, candidates=_candidates())
    output = handle(_envelope(rca_path))
    assert output["incident_refs"] == ["INC-001", "INC-002"]
    assert len(output["incident_refs"]) == len(set(output["incident_refs"]))


def test_wrong_plugin_identity_is_rejected(read_roots: Path) -> None:
    rca_path = _write_rca(read_roots, candidates=_candidates())
    envelope = _envelope(rca_path)
    envelope["plugin_id"] = "aiops.rca-ranker"
    with pytest.raises(InputRejected):
        handle(envelope)


def test_wrong_capability_is_rejected(read_roots: Path) -> None:
    rca_path = _write_rca(read_roots, candidates=_candidates())
    envelope = _envelope(rca_path)
    envelope["capability"] = "aiops.rca.rank"
    with pytest.raises(InputRejected):
        handle(envelope)


def test_invalid_canary_scope_is_rejected(read_roots: Path) -> None:
    rca_path = _write_rca(read_roots, candidates=_candidates())
    envelope = _envelope(rca_path, canary_scope="whole_fleet")
    with pytest.raises(InputRejected):
        handle(envelope)
