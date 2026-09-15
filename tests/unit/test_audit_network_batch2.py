"""Unit tests for network batch 2: nlp-process / rule-engine / ocr-extract
(foundation) and evidence-verify / suspicion-merge / remedy-progress-track
(business stages)."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

import pytest

from packages.plugin_runtime.runner import ArtifactInput
from plugins.builtin.audit_evidence_evidence_verify import runtime as evidence_verify
from plugins.builtin.audit_finding_suspicion_merge import runtime as suspicion_merge
from plugins.builtin.audit_foundation_nlp_process import runtime as nlp_process
from plugins.builtin.audit_foundation_ocr_extract import runtime as ocr_extract
from plugins.builtin.audit_foundation_rule_engine import runtime as rule_engine
from plugins.builtin.audit_remedy_remedy_progress_track import runtime as remedy_progress

ROOT = Path(__file__).resolve().parents[2]
SIM = ROOT / ".data" / "audit-sim"


@pytest.fixture(autouse=True)
def _read_roots(tmp_path: Path) -> None:
    os.environ["AUDIT_PLUGIN_READ_ROOTS"] = json.dumps([str(SIM), str(SIM.parent), str(tmp_path)])


def _input(path: Path, media_type: str = "application/json") -> ArtifactInput:
    raw = path.read_bytes()
    return ArtifactInput(
        artifact_id=uuid4(), tenant_id=uuid4(), uri=path.resolve().as_uri(),
        media_type=media_type, sha256=hashlib.sha256(raw).hexdigest(),
        size_bytes=len(raw), classification="audit_confidential",
    )


def _write(tmp_path: Path, name: str, data) -> ArtifactInput:
    path = tmp_path / name
    if isinstance(data, bytes):
        path.write_bytes(data)
        return _input(path, "application/octet-stream")
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return _input(path)


def _call(runtime, plugin_id: str, capability: str, port_name: str, artifact: ArtifactInput) -> dict:
    envelope = {
        "protocol": "audit-network-plugin-child-v1",
        "plugin_id": plugin_id,
        "capability": capability,
        "trace_id": str(uuid4()),
        port_name: {
            "contract_id": "artifact-ref", "contract_version": "1.0.0",
            "artifact": {"uri": artifact.uri, "sha256": artifact.sha256, "size_bytes": artifact.size_bytes},
        },
    }
    return runtime.handle(envelope)


# ---------- foundation: nlp-process ----------

def test_nlp_summary_truncates_and_scans(tmp_path: Path) -> None:
    payload = {"mode": "summary", "text": "审计发现异常。该供应商重复入账三次。涉及金额重大。建议整改。",
               "keywords": ["重复", "重大"]}
    result = _call(nlp_process, "audit.foundation.nlp-process", "audit.foundation.nlp-process",
                   "nlp-input", _write(tmp_path, "nlp.json", payload))
    assert result["contract_id"] == "nlp-output"
    assert result["mode"] == "summary"
    assert result["sentence_count"] == 4
    assert "审计发现异常。该供应商重复入账三次。" == result["text"]
    assert result["keywords"] == ["重复", "重大"]
    assert result["keyword_counts"]["重复"] == 1


def test_nlp_compose_fills_template(tmp_path: Path) -> None:
    payload = {"mode": "compose", "text": "证据A有效。证据B缺失。", "template": "{summary} 共{sentence_count}条结论"}
    result = _call(nlp_process, "audit.foundation.nlp-process", "audit.foundation.nlp-process",
                   "nlp-input", _write(tmp_path, "nlp2.json", payload))
    assert "证据A有效。证据B缺失。" in result["text"]
    assert "共2条结论" in result["text"]


# ---------- foundation: rule-engine ----------

def test_rule_engine_evaluates_ledger_rules(tmp_path: Path) -> None:
    rules = [
        {"rule_id": "R1", "rule_key": "outlier_amount", "operator": "gte", "field": "debit_amount", "value": 1000000},
        {"rule_id": "R2", "rule_key": "suspicious_desc", "operator": "contains", "field": "description", "value": "异常"},
    ]
    rows = [{"entry_id": "E9301", "debit_amount": "1500000", "description": "大额异常付款"},
            {"entry_id": "E0001", "debit_amount": "1250", "description": "正常入账1"},
            {"entry_id": "E9302", "debit_amount": "2000000", "description": "异常退货"}]
    result = _call(rule_engine, "audit.foundation.rule-engine", "audit.foundation.rule-engine",
                   "rule-input", _write(tmp_path, "rules.json", {"rules": rules, "rows": rows}))
    assert result["contract_id"] == "rule-evaluation"
    by_id = {r["rule_id"]: r for r in result["rules"]}
    assert by_id["R1"]["hits"] == 2
    assert by_id["R2"]["hits"] == 2
    assert result["summary"]["hits"] == 4


# ---------- foundation: ocr-extract ----------

def test_ocr_extract_text_and_fail_closed_image(tmp_path: Path) -> None:
    text_result = _call(ocr_extract, "audit.foundation.ocr-extract", "audit.foundation.ocr-extract",
                        "ocr-image", _write(tmp_path, "scan.txt", "发票号码 INV-2026-001\n金额 12,000.00"))
    assert text_result["extracted"] is True
    assert "INV-2026-001" in text_result["text"]

    image_result = _call(ocr_extract, "audit.foundation.ocr-extract", "audit.foundation.ocr-extract",
                         "ocr-image", _write(tmp_path, "scan.png", b"\x89PNG\r\n\x1a\nfakepixels"))
    assert image_result["extracted"] is False
    assert "no OCR engine" in image_result["reason"]


# ---------- business: evidence-verify ----------

def test_evidence_verify_catches_dangling_and_duplicates(tmp_path: Path) -> None:
    lineage = {
        "contract_id": "evidence-lineage", "contract_version": "1.0.0",
        "lineage_id": "L1", "release_sha256": "b" * 64, "truncated": False,
        "nodes": [
            {"node_id": "e1", "kind": "photo", "sha256": "c" * 64},
            {"node_id": "e2", "kind": "invoice", "sha256": "c" * 64},
            {"node_id": "e3", "kind": "ledger", "sha256": "d" * 64},
            {"node_id": "e4", "kind": "scan", "sha256": "bad-hash"},
        ],
        "edges": [
            {"edge_id": "x1", "source": "e1", "target": "e3"},
            {"edge_id": "x2", "source": "e2", "target": "e9"},
        ],
    }
    result = _call(evidence_verify, "audit.evidence.evidence-verify", "audit.evidence.evidence-verify",
                   "evidence-index", _write(tmp_path, "lineage.json", lineage))
    assert result["contract_id"] == "evidence-verdict"
    assert result["summary"]["checked_evidence"] == 4
    assert result["summary"]["valid"] is False
    assert result["summary"]["duplicate_digests"] == 1
    assert result["summary"]["missing_hashes"] == 1
    assert result["summary"]["closed_edges"] == 1
    by_id = {check["check_id"]: check for check in result["checks"]}
    assert by_id["referential_closure"]["status"] == "fail"
    assert by_id["duplicate_digest"]["status"] == "fail"
    assert by_id["evidence_hash"]["status"] == "fail"


# ---------- business: suspicion-merge ----------

def test_suspicion_merge_dedup_and_photos(tmp_path: Path) -> None:
    candidates = [
        {"rule_id": "R1", "rule_key": "dup", "severity": "high", "row_ref": "E9001", "score": 0.9},
        {"rule_id": "R1", "rule_key": "dup", "severity": "high", "row_ref": "E9001", "score": 0.9},
        {"rule_id": "R2", "rule_key": "outlier", "severity": "medium", "row_ref": "E9301", "score": 0.7},
        {"rule_id": "R3", "rule_key": "invalid", "severity": "low", "row_ref": "E9204", "score": 0.5},
    ]
    photos = {"photos": [{"photo_id": "P1", "row_ref": "E9301"}, {"photo_id": "P2", "row_ref": "E9999"}]}
    suspicion = _write(tmp_path, "sus.json", {"contract_id": "anomaly-candidates", "contract_version": "1.0.0",
                                              "ledger_sha256": "e" * 64, "schema_mapping_version": "1.0.0",
                                              "period": "2026-01", "rule_pack_sha256": "f" * 64,
                                              "summary": {}, "candidates": candidates})
    photo = _write(tmp_path, "photos.json", photos)
    result = _call(suspicion_merge, "audit.finding.suspicion-merge", "audit.finding.suspicion-merge",
                   "suspicion-set", suspicion)
    # photo-evidence is a second port: rebuild envelope manually
    envelope = {
        "protocol": "audit-network-plugin-child-v1",
        "plugin_id": "audit.finding.suspicion-merge",
        "capability": "audit.finding.suspicion-merge",
        "trace_id": str(uuid4()),
        "suspicion-set": {"contract_id": "anomaly-candidates", "contract_version": "1.0.0",
                          "artifact": {"uri": suspicion.uri, "sha256": suspicion.sha256, "size_bytes": suspicion.size_bytes}},
        "photo-evidence": {"contract_id": "artifact-ref", "contract_version": "1.0.0",
                           "artifact": {"uri": photo.uri, "sha256": photo.sha256, "size_bytes": photo.size_bytes}},
    }
    result = suspicion_merge.handle(envelope)
    assert result["contract_id"] == "merged-suspicion"
    assert result["summary"]["input_candidates"] == 4
    assert result["summary"]["merged_candidates"] == 3
    assert result["summary"]["duplicates_removed"] == 1
    assert result["summary"]["photos_attached"] == 1
    by_ref = {c["row_ref"]: c for c in result["candidates"]}
    assert by_ref["E9301"]["photo_refs"][0]["photo_id"] == "P1"
    assert "photo_refs" not in by_ref["E9001"]


# ---------- business: remedy-progress-track ----------

def test_remedy_progress_tracks_completion_and_overdue(tmp_path: Path) -> None:
    workflow = {
        "workflow_id": "W1", "tenant_id": "t1", "key": "remedy-2026-01", "version": "1",
        "input_schema": {}, "output_schema": {},
        "as_of": "2026-06-30",
        "nodes": [
            {"id": "t1", "name": "补录凭证", "props": {"owner": "张三", "due_date": "2026-06-01", "status": "done"}},
            {"id": "t2", "name": "调整分录", "props": {"owner": "李四", "due_date": "2026-06-10", "status": "done"}},
            {"id": "t3", "name": "回收资金", "props": {"owner": "王五", "due_date": "2026-07-20", "status": "in_progress"}},
            {"id": "t4", "name": "追责处理", "props": {"owner": "赵六", "due_date": "2026-05-01", "status": "not_started"}},
        ],
        "edges": [],
    }
    result = _call(remedy_progress, "audit.remedy.remedy-progress-track", "audit.remedy.remedy-progress-track",
                   "remedy-plan-approved", _write(tmp_path, "remedy.json", workflow))
    assert result["contract_id"] == "remedy-progress"
    assert result["summary"]["total"] == 4
    assert result["summary"]["done"] == 2
    assert result["summary"]["in_progress"] == 1
    assert result["summary"]["overdue"] == 1
    assert result["summary"]["completion_rate"] == 0.5
    assert result["overdue_tasks"][0]["task_id"] == "t4"
    assert result["series"][0]["points"][0]["value"] == 0.5
