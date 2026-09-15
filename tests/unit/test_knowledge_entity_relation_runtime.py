from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import uuid4

import pytest

from plugins.builtin.knowledge_entity_relation_candidate.runtime import InputRejected, handle


@pytest.fixture(autouse=True)
def _read_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUDIT_PLUGIN_READ_ROOTS", json.dumps([str(tmp_path)]))


def _document_envelope(path: Path, *, language: str = "zh") -> dict[str, object]:
    content = path.read_bytes()
    document: dict[str, object] = {
        "artifact": {
            "artifact_id": str(uuid4()),
            "tenant_id": str(uuid4()),
            "uri": path.resolve().as_uri(),
            "media_type": "text/markdown",
            "sha256": hashlib.sha256(content).hexdigest(),
            "size_bytes": len(content),
            "classification": "internal",
        },
        "language": language,
    }
    return {
        "protocol": "audit-network-plugin-child-v1",
        "plugin_id": "knowledge.entity-relation-candidate",
        "capability": "knowledge.extract.relations",
        "document": document,
    }


def test_golden_document_extracts_entities_and_related_party_relation(tmp_path: Path) -> None:
    source = tmp_path / "memo.md"
    source.write_text(
        "审计备忘：甲公司与中国审计局存在关联交易，乙公司控股丙集团。\n"
        "依据《企业会计准则第36号》，引用《审计指引》。\n",
        encoding="utf-8",
    )
    output = handle(_document_envelope(source))
    assert output["contract_id"] == "graph-candidate-set"
    entity_names = {item["name"] for item in output["entity_candidates"]}
    assert "甲公司" in entity_names
    assert "中国审计局" in entity_names
    assert "丙集团" in entity_names
    assert any(item["name"] == "企业会计准则第36号" for item in output["entity_candidates"] if item["entity_type"] == "document")
    relation_types = {item["relation_type"] for item in output["relation_candidates"]}
    assert "related_party" in relation_types
    assert "controls" in relation_types
    assert "cites" in relation_types
    assert output["summary"]["entities"] == len(output["entity_candidates"])
    assert output["summary"]["relations"] == len(output["relation_candidates"])


def test_entity_candidate_budget_truncation_is_marked(tmp_path: Path) -> None:
    source = tmp_path / "many.md"
    lines = "\n".join(f"Org{i}公司控股Org{i + 1}集团。" for i in range(30))
    source.write_text(lines, encoding="utf-8")
    envelope = _document_envelope(source)
    envelope["document"]["max_entities"] = 5  # type: ignore[index]
    output = handle(envelope)
    assert output["summary"]["truncated"] is True
    assert len(output["entity_candidates"]) <= 5


def test_sha256_mismatch_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "memo.md"
    source.write_text("甲公司控股乙公司。", encoding="utf-8")
    envelope = _document_envelope(source)
    envelope["document"]["artifact"]["sha256"] = "0" * 64  # type: ignore[index]
    with pytest.raises(InputRejected, match="sha256"):
        handle(envelope)


def test_unsupported_language_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "memo.md"
    source.write_text("甲公司控股乙公司。", encoding="utf-8")
    envelope = _document_envelope(source, language="ja")
    with pytest.raises(InputRejected, match="language"):
        handle(envelope)
