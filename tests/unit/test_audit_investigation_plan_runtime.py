from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import uuid4

import pytest

from plugins.builtin.audit_investigation_plan.runtime import InputRejected, handle


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


def _write_evidence(tmp_path: Path, *, types: list[str]) -> Path:
    source = tmp_path / "evidence.json"
    source.write_text(
        json.dumps(
            {
                "contract_id": "audit-evidence-index",
                "contract_version": "1.0.0",
                "period": "2026-03",
                "evidence": [
                    {"evidence_id": f"ev:{idx}", "evidence_type": evidence_type, "source_ref": f"evidence:{idx}"}
                    for idx, evidence_type in enumerate(types)
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return source


def _investigation(artifact: dict[str, object], *, max_steps: int = 8) -> dict[str, object]:
    return {
        "protocol": "audit-network-plugin-child-v1",
        "plugin_id": "audit.investigation-plan",
        "capability": "audit.investigation.plan",
        "investigation": {"anomaly_candidates": artifact, "max_steps": max_steps},
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
                "score": 0.85,
            },
            {
                "rule_id": "outlier_amount:59",
                "rule_key": "outlier_amount",
                "severity": "high",
                "row_ref": "59",
                "source_ref": "journal:a1b2c3d4:row:59",
                "score": 0.9,
            },
        ],
    )


def test_golden_anomaly_set_builds_a_deterministic_investigation_draft(tmp_path: Path, anomalies_file: Path) -> None:
    output = handle(_investigation(_artifact(anomalies_file)))

    assert output["contract_id"] == "investigation-plan-draft"
    assert output["period"] == "2026-03"
    assert len(output["steps"]) == 2
    assert [step["rule_key"] for step in output["steps"]] == ["invalid_date", "outlier_amount"]
    assert [step["order"] for step in output["steps"]] == [1, 2]
    assert all(step["target_refs"] for step in output["steps"])
    # No evidence index was provided, so every required evidence family is a gap.
    assert len(output["evidence_gaps"]) >= 2
    assert len(output["counter_hypotheses"]) == 2
    assert all(item["verify_with"] for item in output["counter_hypotheses"])
    assert len(output["plan_id"]) == 16
    assert output["summary"]["covered_candidates"] == 2
    assert output["summary"]["covered_rules"] == 2
    assert output["summary"]["truncated"] is False


def test_the_draft_is_deterministic_for_the_same_input(tmp_path: Path, anomalies_file: Path) -> None:
    first = handle(_investigation(_artifact(anomalies_file)))
    second = handle(_investigation(_artifact(anomalies_file)))
    assert first == second


def test_evidence_index_removes_collected_evidence_from_gaps(tmp_path: Path, anomalies_file: Path) -> None:
    evidence = _write_evidence(tmp_path, types=["原始凭单", "原始凭证"])

    def envelope() -> dict[str, object]:
        payload = _investigation(_artifact(anomalies_file))
        payload["investigation"]["evidence_index"] = _artifact(evidence)  # type: ignore[index]
        return payload

    output = handle(envelope())
    gap_types = {item["evidence_type"] for item in output["evidence_gaps"]}
    assert "原始凭单" not in gap_types
    assert "原始凭证" not in gap_types
    assert output["evidence_bundle_sha256"] == hashlib.sha256(evidence.read_bytes()).hexdigest()


def test_max_steps_truncates_steps_but_keeps_counters_deterministic(tmp_path: Path, anomalies_file: Path) -> None:
    output = handle(_investigation(_artifact(anomalies_file), max_steps=1))
    assert output["summary"]["truncated"] is True
    assert len(output["steps"]) == 1
    assert output["steps"][0]["rule_key"] == "invalid_date"
    # The counter-hypothesis checklist stays complete: falsification is never
    # silently dropped just because the step budget was exhausted.
    assert len(output["counter_hypotheses"]) == 2


def test_unknown_rule_keys_are_counted_and_skipped(tmp_path: Path) -> None:
    source = _write_anomalies(
        tmp_path,
        candidates=[
            {"rule_id": "custom:1", "rule_key": "custom", "severity": "low", "row_ref": "1", "score": 0.1}
        ],
    )
    output = handle(_investigation(_artifact(source)))
    assert output["summary"]["unknown_rule_count"] == 1
    assert output["summary"]["covered_rules"] == 0
    assert output["steps"] == []
    assert output["counter_hypotheses"] == []


def test_max_steps_outside_budget_is_rejected(tmp_path: Path, anomalies_file: Path) -> None:
    with pytest.raises(InputRejected, match="max_steps"):
        handle(_investigation(_artifact(anomalies_file), max_steps=0))
    with pytest.raises(InputRejected, match="max_steps"):
        handle(_investigation(_artifact(anomalies_file), max_steps=65))


def test_sha256_mismatch_is_rejected(tmp_path: Path, anomalies_file: Path) -> None:
    envelope = _investigation(_artifact(anomalies_file))
    envelope["investigation"]["anomaly_candidates"]["sha256"] = "0" * 64  # type: ignore[index]
    with pytest.raises(InputRejected, match="sha256"):
        handle(envelope)


def test_missing_candidate_list_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "broken.json"
    source.write_text(json.dumps({"contract_id": "anomaly-candidates", "period": "2026-03", "rule_pack_sha256": "b" * 64}))
    with pytest.raises(InputRejected, match="candidate list"):
        handle(_investigation(_artifact(source)))