from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import uuid4

import pytest

from plugins.builtin.knowledge_retention_recommendation.runtime import InputRejected, handle

DOC_URI = "file:///G:/数据/04-审计数据集与基准/PCCA-Benchmark/benchmark_cases/bigcase/BCSA/BCSA_big_01/fault_tickets.json"


def _envelope(path: Path, **overrides: object) -> dict[str, object]:
    content = path.read_bytes()
    artifact = {
        "artifact_id": str(uuid4()),
        "tenant_id": str(uuid4()),
        "uri": path.resolve().as_uri(),
        "media_type": "application/json",
        "sha256": hashlib.sha256(content).hexdigest(),
        "size_bytes": len(content),
        "classification": "internal",
    }
    envelope: dict[str, object] = {
        "protocol": "audit-network-plugin-child-v1",
        "plugin_id": "knowledge.retention-recommendation",
        "capability": "knowledge.retention.recommend",
        "retention": {
            "document": artifact,
            "last_access_days": 30,
            "ref_count": 3,
            "classification": "internal",
            "archive_after_days": 365,
            "purge_candidate_after_days": 730,
            "min_refs_to_retain": 1,
        },
    }
    envelope.update(overrides)
    return envelope


@pytest.fixture()
def read_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "retention-root"
    root.mkdir()
    monkeypatch.setenv("AUDIT_PLUGIN_READ_ROOTS", json.dumps([str(root)]))
    return root


def _write(root: Path, name: str) -> Path:
    path = root / name
    path.write_text(json.dumps({"source": "benchmark", "rows": 12}, ensure_ascii=False), encoding="utf-8")
    return path


def test_active_document_with_references_is_retained(read_roots: Path) -> None:
    path = _write(read_roots, "doc-active.json")
    output = handle(_envelope(path))

    assert output["contract_id"] == "retention-recommendation"
    assert output["contract_version"] == "1.0.0"
    assert output["status"] == "proposed"
    assert len(output["recommendation_id"]) == 16
    assert output["recommendation"] == "retain"
    assert "ref_count_above_threshold" in output["rationale"]["matched_rules"]
    assert output["summary"] == {"retain": 1, "archive": 0, "delete_candidate": 0}
    assert output["signature"] == {"recommended_by": "knowledge.retention-recommendation@0.1.0", "reviewed": False}
    assert output["constraints"] == {
        "no_delete": True,
        "evidence_immutable": True,
        "requires_human_approval": True,
    }
    assert output["provenance"]["source_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert output["provenance"]["plugin"] == "knowledge.retention-recommendation@0.1.0"


def test_idle_document_without_references_is_archived(read_roots: Path) -> None:
    path = _write(read_roots, "doc-idle.json")
    output = handle(
        _envelope(
            path,
            retention={
                "document": _document(path),
                "last_access_days": 400,
                "ref_count": 0,
                "classification": "internal",
                "archive_after_days": 365,
                "purge_candidate_after_days": 730,
                "min_refs_to_retain": 1,
            },
        )
    )

    assert output["recommendation"] == "archive"
    assert "idle_beyond_archive_threshold" in output["rationale"]["matched_rules"]
    assert output["summary"] == {"retain": 0, "archive": 1, "delete_candidate": 0}


def test_very_idle_document_is_only_a_delete_candidate(read_roots: Path) -> None:
    path = _write(read_roots, "doc-stale.json")
    output = handle(
        _envelope(
            path,
            retention={
                "document": _document(path),
                "last_access_days": 900,
                "ref_count": 0,
                "classification": "internal",
                "archive_after_days": 365,
                "purge_candidate_after_days": 730,
                "min_refs_to_retain": 1,
            },
        )
    )

    assert output["recommendation"] == "delete_candidate"
    assert "idle_beyond_purge_threshold" in output["rationale"]["matched_rules"]
    assert output["summary"] == {"retain": 0, "archive": 0, "delete_candidate": 1}


def test_sensitive_document_is_retained_regardless_of_idle_time(read_roots: Path) -> None:
    path = _write(read_roots, "doc-sensitive.json")
    output = handle(
        _envelope(
            path,
            retention={
                "document": _document(path),
                "last_access_days": 1000,
                "ref_count": 0,
                "classification": "audit_confidential",
                "archive_after_days": 365,
                "purge_candidate_after_days": 730,
                "min_refs_to_retain": 1,
            },
        )
    )

    assert output["recommendation"] == "retain"
    assert "sensitive_classification" in output["rationale"]["matched_rules"]


def test_recommendation_is_deterministic_over_the_same_inputs(read_roots: Path) -> None:
    path = _write(read_roots, "doc-deterministic.json")
    first = handle(_envelope(path))
    second = handle(_envelope(path))

    assert first["recommendation_id"] == second["recommendation_id"]
    assert first["recommendation"] == second["recommendation"]


def test_tampered_sha256_is_rejected(read_roots: Path) -> None:
    path = _write(read_roots, "doc-tampered.json")
    document = _document(path)
    document["sha256"] = "0" * 64

    with pytest.raises(InputRejected, match="sha256 does not match"):
        handle(
            _envelope(
                path,
                retention={
                    "document": document,
                    "last_access_days": 30,
                    "ref_count": 0,
                    "classification": "internal",
                },
            )
        )


def _document(path: Path) -> dict[str, object]:
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