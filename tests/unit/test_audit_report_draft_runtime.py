from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import uuid4

import pytest

from plugins.builtin.audit_report_draft.runtime import InputRejected, handle

ENGAGEMENT_ID = "4c8e2a6d-b5f1-4d9a-a37e-8b0c2d4f6e1a"
FINDING_ID_1 = "0f2c4e6a-8d59-4b7c-a21e-7d9f0b3a6c52"
FINDING_ID_2 = "1a3b5c7d-9e0f-4a1b-8c2d-3e4f5a6b7c8d"
CLAIM_ID_1 = "2c4e6a8d-59f3-4b7c-a21e-7d9f0b3a6c52"
CLAIM_ID_2 = "3b2d4f6a-8c1e-4a5b-9d7e-1f2a3b4c5d6e"


def _finding(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "finding_id": FINDING_ID_1,
        "title": "采购凭证金额越界（单笔超阈值）",
        "severity": "high",
        "status": "confirmed",
        "claim_id": CLAIM_ID_1,
        "claim": "凭证 PO-202609-0017 金额越界且无审批留痕。",
        "reviewer_label": "审计-张三",
        "evidence_refs": ["file:///G:/数据/expense_ledger.csv"],
        "confirmed_at": "2026-09-05T10:30:00+08:00",
    }
    payload.update(overrides)
    return payload


def _finding_set(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "contract_id": "finding-set",
        "contract_version": "1.0.0",
        "engagement_id": ENGAGEMENT_ID,
        "engagement_name": "BCSA 大案例 01 - 2026-09 报告草稿验收",
        "findings": [_finding()],
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


def _envelope(path: Path, **overrides: object) -> dict[str, object]:
    envelope: dict[str, object] = {
        "protocol": "audit-network-plugin-child-v1",
        "plugin_id": "audit.report-draft",
        "capability": "audit.report.draft",
        "report": {"finding_set": _artifact(path), "template_version": "1.2.0"},
    }
    envelope.update(overrides)
    return envelope


@pytest.fixture()
def read_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "report-root"
    root.mkdir()
    monkeypatch.setenv("AUDIT_PLUGIN_READ_ROOTS", json.dumps([str(root)]))
    return root


def _write(root: Path, name: str, finding_set: dict[str, object]) -> Path:
    path = root / name
    path.write_text(json.dumps(finding_set, ensure_ascii=False), encoding="utf-8")
    return path


def test_confirmed_findings_produce_an_unsigned_report_draft(read_roots: Path) -> None:
    path = _write(read_roots, "finding-set.json", _finding_set())
    output = handle(_envelope(path))

    assert output["contract_id"] == "report-draft"
    assert output["contract_version"] == "1.0.0"
    assert output["report_kind"] == "report"
    assert output["status"] == "draft"
    assert len(output["report_id"]) == 16
    assert output["engagement_id"] == ENGAGEMENT_ID
    assert output["template_version"] == "1.2.0"
    assert output["body"]["cover"]["finding_count"] == 1
    assert output["body"]["cover"]["severity_summary"] == {"high": 1, "medium": 0, "low": 0}
    assert output["body"]["executive_summary"].startswith("本报告草稿基于 1 项已确认发现生成")
    assert len(output["body"]["findings"]) == 1
    assert output["body"]["findings"][0]["finding_id"] == FINDING_ID_1
    assert output["signature"] == {
        "draft_by": "audit.report-draft@0.1.0",
        "signed": False,
        "issuer": "audit_engagement_lead",
    }
    assert output["summary"] == {"findings": 1, "high": 1, "medium": 0, "low": 0, "evidence_refs": 1, "truncated": False}
    assert output["constraints"] == {
        "no_overwrite": True,
        "requires_human_approval": True,
        "immutable_source": True,
        "human_issuance_required": True,
    }
    assert output["provenance"]["source_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert output["provenance"]["template_version"] == "1.2.0"
    assert output["provenance"]["plugin"] == "audit.report-draft@0.1.0"


def test_report_draft_is_deterministic_over_the_same_finding_set(read_roots: Path) -> None:
    first_path = _write(read_roots, "a.json", _finding_set())
    second_path = _write(read_roots, "b.json", _finding_set())
    first = handle(_envelope(first_path))
    second = handle(_envelope(second_path))
    assert first["report_id"] == second["report_id"]
    assert first["body"]["cover"] == second["body"]["cover"]
    assert first["body"]["findings"] == second["body"]["findings"]
    assert first["summary"] == second["summary"]


def test_template_version_is_part_of_the_report_identity(read_roots: Path) -> None:
    path = _write(read_roots, "finding-set.json", _finding_set())
    base = handle(_envelope(path))
    changed = handle(_envelope(path, report={"finding_set": _artifact(path), "template_version": "2.0.0"}))
    assert base["report_id"] != changed["report_id"]
    assert base["provenance"]["template_version"] == "1.2.0"
    assert changed["provenance"]["template_version"] == "2.0.0"


def test_sha256_mismatch_is_rejected(read_roots: Path) -> None:
    path = _write(read_roots, "tampered.json", _finding_set())
    artifact = _artifact(path)
    artifact["sha256"] = "f" * 64
    envelope = {
        "protocol": "audit-network-plugin-child-v1",
        "plugin_id": "audit.report-draft",
        "capability": "audit.report.draft",
        "report": {"finding_set": artifact, "template_version": "1.2.0"},
    }
    with pytest.raises(InputRejected, match="sha256"):
        handle(envelope)


def test_unsupported_contract_version_is_rejected(read_roots: Path) -> None:
    path = _write(read_roots, "v2.json", _finding_set(contract_version="2.0.0"))
    with pytest.raises(InputRejected, match="contract version"):
        handle(_envelope(path))


def test_unconfirmed_finding_is_rejected(read_roots: Path) -> None:
    path = _write(read_roots, "proposed.json", _finding_set(findings=[_finding(status="proposed")]))
    with pytest.raises(InputRejected, match="not confirmed"):
        handle(_envelope(path))


def test_invalid_severity_is_rejected(read_roots: Path) -> None:
    finding = _finding(severity="explosive")
    path = _write(read_roots, "bad-sev.json", _finding_set(findings=[finding]))
    with pytest.raises(InputRejected, match="severity"):
        handle(_envelope(path))


def test_path_escape_outside_read_root_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "report-root"
    root.mkdir()
    monkeypatch.setenv("AUDIT_PLUGIN_READ_ROOTS", json.dumps([str(root)]))
    outside = tmp_path / "outside"
    outside.mkdir()
    path = _write(outside, "finding-set.json", _finding_set())
    with pytest.raises(InputRejected, match="outside declared read roots"):
        handle(_envelope(path))


def test_wrong_plugin_identity_is_rejected(read_roots: Path) -> None:
    envelope = _envelope(_write(read_roots, "finding-set.json", _finding_set()))
    envelope["plugin_id"] = "audit.workpaper-export"
    with pytest.raises(InputRejected, match="plugin identity"):
        handle(envelope)
