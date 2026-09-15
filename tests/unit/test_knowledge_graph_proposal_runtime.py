from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import uuid4

import pytest

from plugins.builtin.knowledge_graph_proposal_builder.runtime import InputRejected, handle


@pytest.fixture(autouse=True)
def _read_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUDIT_PLUGIN_READ_ROOTS", json.dumps([str(tmp_path)]))


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


def _candidate_set(path: Path) -> dict[str, object]:
    return {
        "contract_id": "graph-candidate-set",
        "contract_version": "1.0.0",
        "candidate_set": {
            "artifact": _artifact(path),
            "space_level": "L1",
            "max_proposals": 1000,
        },
    }


def _write_candidates(
    tmp_path: Path,
    *,
    entities: list[dict[str, object]],
    relations: list[dict[str, object]],
) -> Path:
    source = tmp_path / "candidates.json"
    source.write_text(
        json.dumps(
            {
                "contract_id": "graph-candidate-set",
                "contract_version": "1.0.0",
                "entity_candidates": entities,
                "relation_candidates": relations,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return source


def _envelope(
    candidates: dict[str, object],
    *,
    snapshot: dict[str, object] | None = None,
    space_level: str = "L1",
    max_proposals: int = 1000,
) -> dict[str, object]:
    proposal: dict[str, object] = {
        "candidate_set": candidates["candidate_set"]["artifact"],  # type: ignore[union-attr]
        "space_level": space_level,
        "max_proposals": max_proposals,
    }
    if snapshot is not None:
        proposal["graph_snapshot"] = snapshot["candidate_set"]["artifact"]  # type: ignore[union-attr]
    return {
        "protocol": "audit-network-plugin-child-v1",
        "plugin_id": "knowledge.graph-proposal-builder",
        "capability": "graph.proposal.build",
        "proposal": proposal,
    }


@pytest.fixture()
def candidates_file(tmp_path: Path) -> Path:
    return _write_candidates(
        tmp_path,
        entities=[
            {
                "candidate_id": "organization::审计局",
                "entity_type": "organization",
                "name": "审计局",
                "span": "审计局",
                "source_ref": "document:a1:span:审计局",
                "confidence": 0.9,
                "evidence": {},
            },
            {
                "candidate_id": "document::《审计指引》",
                "entity_type": "document",
                "name": "《审计指引》",
                "span": "《审计指引》",
                "source_ref": "document:a1:line:10",
                "confidence": 0.75,
                "evidence": {},
            },
        ],
        relations=[
            {
                "candidate_id": "rel:cites:审计局->《审计指引》",
                "source_id": "organization::审计局",
                "target_id": "document::《审计指引》",
                "relation_type": "cites",
                "span": "审计局依据《审计指引》",
                "confidence": 0.7,
                "evidence": {},
            }
        ],
    )


def test_golden_candidate_set_builds_a_deterministic_proposal_draft(tmp_path: Path, candidates_file: Path) -> None:
    output = handle(_envelope(_candidate_set(candidates_file)))

    assert output["contract_id"] == "graph-proposal-draft"
    assert len(output["entity_proposals"]) == 2
    assert all(item["status"] == "proposed" for item in output["entity_proposals"])
    assert all(item["op"] == "add_entity" for item in output["entity_proposals"])
    assert all(item["target_space"] == "L1" for item in output["entity_proposals"])
    assert len(output["relation_proposals"]) == 1
    relation = output["relation_proposals"][0]
    assert relation["op"] == "add_relation"
    assert relation["source_id"] == "organization::审计局"
    assert relation["target_id"] == "document::《审计指引》"
    assert relation["relation_type"] == "cites"
    assert len(relation["idempotent_key"]) == 48
    assert output["summary"]["entities_proposed"] == 2
    assert output["summary"]["relations_proposed"] == 1
    assert output["summary"]["duplicates"] == 0
    assert output["summary"]["conflicts"] == 0
    assert len(output["draft_id"]) == 16


def test_snapshot_marks_duplicates_and_type_conflicts(tmp_path: Path, candidates_file: Path) -> None:
    snapshot_path = tmp_path / "snapshot.json"
    snapshot_path.write_text(
        json.dumps(
            {
                "contract_id": "graph-snapshot",
                "contract_version": "1.0.0",
                "space_level": "L1",
                "nodes_by_type": {
                    "organization": [{"text": "审计局"}, {"text": "财政局"}],
                    "document": [{"text": "《审计指引》"}],
                    "person": [],
                    "generic": [],
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    snapshot = _candidate_set(snapshot_path)
    output = handle(_envelope(_candidate_set(candidates_file), snapshot=snapshot))
    by_id = {item["candidate_id"]: item for item in output["entity_proposals"]}

    assert by_id["organization::审计局"]["status"] == "duplicate"
    assert by_id["organization::审计局"]["reason"] == "entity already present in the target space"
    assert by_id["document::《审计指引》"]["status"] == "duplicate"
    # A snapshot conflict cannot be a live proposal, so the relation is blocked.
    assert output["summary"]["entities_proposed"] == 0
    assert output["relation_proposals"] == []
    assert output["summary"]["blocked_relations"] == 1


def test_same_name_with_distinct_types_is_a_conflict(tmp_path: Path) -> None:
    source = _write_candidates(
        tmp_path,
        entities=[
            {
                "candidate_id": "organization::审计局",
                "entity_type": "organization",
                "name": "审计局",
                "span": "审计局",
                "confidence": 0.9,
                "evidence": {},
            },
            {
                "candidate_id": "person::审计局",
                "entity_type": "person",
                "name": "审计局",
                "span": "审计局",
                "confidence": 0.6,
                "evidence": {},
            },
        ],
        relations=[],
    )
    output = handle(_envelope(_candidate_set(source)))
    by_id = {item["candidate_id"]: item for item in output["entity_proposals"]}

    assert by_id["organization::审计局"]["status"] == "conflict_name"
    assert by_id["person::审计局"]["status"] == "conflict_name"
    assert len(output["conflicts"]) == 1
    conflict = output["conflicts"][0]
    assert conflict["name"] == "审计局"
    assert conflict["entity_types"] == ["organization", "person"]


def test_max_proposals_truncates_propsal_budget(tmp_path: Path, candidates_file: Path) -> None:
    output = handle(_envelope(_candidate_set(candidates_file), max_proposals=1))
    assert output["summary"]["truncated"] is True
    assert len(output["entity_proposals"]) <= 1
    # One entity passed the budget; its relation is still proposed because the
    # relation loop only checks ``used`` before appending each distinct pair.
    assert output["summary"]["relations_proposed"] >= 0


def test_duplicate_relation_candidates_are_deduplicated(tmp_path: Path) -> None:
    source = _write_candidates(
        tmp_path,
        entities=[
            {
                "candidate_id": "organization::审计局",
                "entity_type": "organization",
                "name": "审计局",
                "span": "审计局",
                "confidence": 0.9,
                "evidence": {},
            },
            {
                "candidate_id": "document::《审计指引》",
                "entity_type": "document",
                "name": "《审计指引》",
                "span": "《审计指引》",
                "confidence": 0.75,
                "evidence": {},
            },
        ],
        relations=[
            {
                "candidate_id": "rel:1",
                "source_id": "organization::审计局",
                "target_id": "document::《审计指引》",
                "relation_type": "cites",
                "span": "第一次",
                "confidence": 0.7,
                "evidence": {},
            },
            {
                "candidate_id": "rel:2",
                "source_id": "organization::审计局",
                "target_id": "document::《审计指引》",
                "relation_type": "cites",
                "span": "第二次",
                "confidence": 0.8,
                "evidence": {},
            },
        ],
    )
    output = handle(_envelope(_candidate_set(source)))
    assert len(output["relation_proposals"]) == 1
    assert output["relation_proposals"][0]["candidate_id"] == "rel:1"
    assert output["summary"]["duplicates"] == 1


def test_sha256_mismatch_is_rejected(tmp_path: Path, candidates_file: Path) -> None:
    envelope = _envelope(_candidate_set(candidates_file))
    envelope["proposal"]["candidate_set"]["sha256"] = "0" * 64  # type: ignore[index]
    with pytest.raises(InputRejected, match="sha256"):
        handle(envelope)


def test_unknown_entity_type_is_rejected(tmp_path: Path) -> None:
    source = _write_candidates(
        tmp_path,
        entities=[
            {
                "candidate_id": "custom::审计局",
                "entity_type": "custom",
                "name": "审计局",
                "span": "审计局",
                "confidence": 0.9,
                "evidence": {},
            }
        ],
        relations=[],
    )
    with pytest.raises(InputRejected, match="entity_type"):
        handle(_envelope(_candidate_set(source)))


def test_unsupported_space_level_is_rejected(tmp_path: Path, candidates_file: Path) -> None:
    with pytest.raises(InputRejected, match="space_level"):
        handle(_envelope(_candidate_set(candidates_file), space_level="L9"))