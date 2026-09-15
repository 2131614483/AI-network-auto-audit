from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import uuid4

import pytest

from plugins.builtin.audit_finding_draft.runtime import InputRejected, handle


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
        "classification": "audit_confidential",
    }


def _write_anomalies(
    tmp_path: Path,
    *,
    candidates: list[dict[str, object]],
    period: str = "2026-03",
) -> Path:
    source = tmp_path / "anomalies.json"
    source.write_text(
        json.dumps(
            {
                "contract_id": "anomaly-candidates",
                "contract_version": "1.0.0",
                "ledger_sha256": "a" * 64,
                "schema_mapping_version": "1.0.0",
                "period": period,
                "rule_pack_sha256": "b" * 64,
                "rules": sorted({str(item["rule_key"]) for item in candidates}),
                "summary": {"total_rows": len(candidates), "candidate_count": len(candidates)},
                "candidates": candidates,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return source


def _write_plan(tmp_path: Path, *, counter_hypotheses: list[dict[str, object]], evidence_gaps: list[dict[str, object]]) -> Path:
    source = tmp_path / "plan.json"
    source.write_text(
        json.dumps(
            {
                "contract_id": "investigation-plan-draft",
                "contract_version": "1.0.0",
                "anomaly_set_sha256": "a" * 64,
                "plan_id": "c" * 16,
                "period": "2026-03",
                "rule_pack_sha256": "b" * 64,
                "steps": [],
                "evidence_gaps": evidence_gaps,
                "counter_hypotheses": counter_hypotheses,
                "summary": {"covered_candidates": 0, "covered_rules": 0, "steps": 0, "evidence_gaps": len(evidence_gaps), "counter_hypotheses": len(counter_hypotheses), "truncated": False},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return source


def _finding(artifact: dict[str, object]) -> dict[str, object]:
    return {
        "protocol": "audit-network-plugin-child-v1",
        "plugin_id": "audit.finding-draft",
        "capability": "audit.finding.draft",
        "finding": {"anomaly_candidates": artifact},
    }


@pytest.fixture()
def anomalies_file(tmp_path: Path) -> Path:
    return _write_anomalies(
        tmp_path,
        candidates=[
            {
                "rule_id": "invalid_date:60",
                "rule_key": "invalid_date",
                "severity": "high",
                "row_ref": "60",
                "source_ref": "journal:a1b2c3d4:row:60",
                "reason_code": "INVALID_DATE",
                "score": 0.85,
                "evidence": {"source_sha256": "a" * 64, "date": "2026-02-30"},
            },
            {
                "rule_id": "outlier_amount:59",
                "rule_key": "outlier_amount",
                "severity": "low",
                "row_ref": "59",
                "source_ref": "journal:a1b2c3d4:row:59",
                "reason_code": "OUTLIER_AMOUNT",
                "score": 0.55,
                "evidence": {"source_sha256": "a" * 64, "amount": 290.0},
            },
        ],
    )


def test_golden_anomaly_set_builds_a_deterministic_finding_draft(tmp_path: Path, anomalies_file: Path) -> None:
    output = handle(_finding(_artifact(anomalies_file)))

    assert output["contract_id"] == "finding-draft"
    assert output["period"] == "2026-03"
    assert len(output["findings"]) == 2
    assert [finding["rule_key"] for finding in output["findings"]] == ["invalid_date", "outlier_amount"]
    assert all(finding["status"] == "proposed" for finding in output["findings"])
    assert all(len(finding["claims"]) >= 2 for finding in output["findings"])
    assert all(finding["counter_hypotheses"] for finding in output["findings"])
    # Claim basis stays inside the contract vocabulary.
    bases = {claim["basis"] for finding in output["findings"] for claim in finding["claims"]}
    assert bases <= {"anomaly", "gap", "counter"}
    # Evidence refs are carried from source refs + evidence hashes.
    assert all(finding["evidence_refs"] for finding in output["findings"])
    # High severity maps to escalate, low maps to investigate.
    assert next(finding["proposed_action"] for finding in output["findings"] if finding["rule_key"] == "invalid_date") == "escalate"
    assert next(finding["proposed_action"] for finding in output["findings"] if finding["rule_key"] == "outlier_amount") == "investigate"
    assert len(output["draft_id"]) == 16
    assert output["summary"]["findings"] == 2
    assert output["summary"]["covered_candidates"] == 2
    assert output["summary"]["truncated"] is False


def test_the_draft_is_deterministic_for_the_same_input(tmp_path: Path, anomalies_file: Path) -> None:
    first = handle(_finding(_artifact(anomalies_file)))
    second = handle(_finding(_artifact(anomalies_file)))
    assert first == second


def test_investigation_plan_feeds_counter_hypotheses_and_gap_claims(tmp_path: Path, anomalies_file: Path) -> None:
    plan = _write_plan(
        tmp_path,
        counter_hypotheses=[
            {
                "hypothesis_id": "f" * 16,
                "hypothesis": "日期换算/跨期调整审批完备，非人为改期。",
                "rule_key": "invalid_date",
                "verify_with": ["原始业务单据", "日切日志"],
                "severity": "high",
            }
        ],
        evidence_gaps=[{"gap_id": "d" * 16, "evidence_type": "原始凭单", "purpose": "验证", "rule_keys": ["invalid_date"]}],
    )

    def envelope() -> dict[str, object]:
        payload = _finding(_artifact(anomalies_file))
        payload["finding"]["investigation_plan"] = _artifact(plan)  # type: ignore[index]
        return payload

    output = handle(envelope())
    finding = next(item for item in output["findings"] if item["rule_key"] == "invalid_date")
    assert finding["counter_hypotheses"][0]["hypothesis"] == "日期换算/跨期调整审批完备，非人为改期。"
    assert "原始凭单" in finding["claims"][1]["statement"]
    assert output["plan_sha256"] == hashlib.sha256(plan.read_bytes()).hexdigest()


def test_unknown_rule_keys_are_counted_and_skipped(tmp_path: Path) -> None:
    source = _write_anomalies(
        tmp_path,
        candidates=[
            {"rule_id": "custom:1", "rule_key": "custom", "severity": "low", "row_ref": "1", "reason_code": "CUSTOM", "score": 0.1}
        ],
    )
    output = handle(_finding(_artifact(source)))
    assert output["summary"]["unknown_rule_count"] == 1
    assert output["findings"] == []


def test_sha256_mismatch_is_rejected(tmp_path: Path, anomalies_file: Path) -> None:
    envelope = _finding(_artifact(anomalies_file))
    envelope["finding"]["anomaly_candidates"]["sha256"] = "0" * 64  # type: ignore[index]
    with pytest.raises(InputRejected, match="sha256"):
        handle(envelope)


def test_missing_candidate_list_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "broken.json"
    source.write_text(json.dumps({"contract_id": "anomaly-candidates", "period": "2026-03", "rule_pack_sha256": "b" * 64}))
    with pytest.raises(InputRejected, match="candidate list"):
        handle(_finding(_artifact(source)))


def test_unexpected_capability_is_rejected(tmp_path: Path, anomalies_file: Path) -> None:
    envelope = _finding(_artifact(anomalies_file))
    envelope["capability"] = "audit.investigation.plan"  # type: ignore[index]
    with pytest.raises(InputRejected, match="capability"):
        handle(envelope)
