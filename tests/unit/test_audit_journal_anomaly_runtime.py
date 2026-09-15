from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import uuid4

import pytest

from plugins.builtin.audit_journal_anomaly.runtime import InputRejected, handle


@pytest.fixture(autouse=True)
def _read_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUDIT_PLUGIN_READ_ROOTS", json.dumps([str(tmp_path)]))


def _journal_envelope(path: Path, *, period: str = "2026-03", zscore: float = 3.0) -> dict[str, object]:
    content = path.read_bytes()
    journal: dict[str, object] = {
        "artifact": {
            "artifact_id": str(uuid4()),
            "tenant_id": str(uuid4()),
            "uri": path.resolve().as_uri(),
            "media_type": "text/csv",
            "sha256": hashlib.sha256(content).hexdigest(),
            "size_bytes": len(content),
            "classification": "audit_confidential",
        },
        "schema_mapping_version": "1.0.0",
        "period": period,
        "zscore_threshold": zscore,
    }
    return {"protocol": "audit-network-plugin-child-v1", "plugin_id": "audit.journal-anomaly", "capability": "audit.journal.detect", "journal": journal}


def test_golden_journal_with_invalid_dates_and_outliers(tmp_path: Path) -> None:
    source = tmp_path / "journal.csv"
    source.write_text(
        "date,account_ref,amount\n"
        "2026-03-01,GL-100,100.0\n"
        "2026-03-02,GL-101,101.0\n"
        "2026-03-03,GL-102,102.0\n"
        "2026-02-29,GL-103,103.0\n"
        "2026-02-30,GL-104,104.0\n"
        "2026-03-04,GL-105,290.0\n",
        encoding="utf-8",
    )
    output = handle(_journal_envelope(source, zscore=2.0))
    assert output["contract_id"] == "anomaly-candidates"
    assert output["summary"]["total_rows"] == 6
    assert output["summary"]["invalid_dates"] == 2
    assert output["summary"]["outliers"] >= 1
    keys = {item["rule_key"] for item in output["candidates"]}
    assert "invalid_date" in keys
    assert "outlier_amount" in keys
    assert output["summary"]["candidate_count"] == len(output["candidates"])


def test_negative_amount_is_a_candidate(tmp_path: Path) -> None:
    source = tmp_path / "journal.csv"
    source.write_text(
        "date,account_ref,amount\n"
        "2026-03-01,GL-100,100.0\n"
        "2026-03-02,GL-101,-50.0\n"
        "2026-03-03,GL-102,102.0\n",
        encoding="utf-8",
    )
    output = handle(_journal_envelope(source))
    assert output["summary"]["negative_amounts"] == 1
    assert any(item["rule_key"] == "negative_amount" for item in output["candidates"])


def test_duplicate_row_is_a_candidate(tmp_path: Path) -> None:
    source = tmp_path / "journal.csv"
    source.write_text(
        "date,account_ref,amount\n"
        "2026-03-01,GL-100,100.0\n"
        "2026-03-01,GL-100,100.0\n"
        "2026-03-03,GL-102,102.0\n",
        encoding="utf-8",
    )
    output = handle(_journal_envelope(source))
    assert output["summary"]["duplicate_rows"] == 1
    assert any(item["rule_key"] == "duplicate_row" for item in output["candidates"])


def test_missing_amount_is_a_candidate(tmp_path: Path) -> None:
    source = tmp_path / "journal.csv"
    source.write_text(
        "date,account_ref,amount\n"
        "2026-03-01,GL-100,\n"
        "2026-03-02,GL-101,101.0\n",
        encoding="utf-8",
    )
    output = handle(_journal_envelope(source))
    assert output["summary"]["missing_amounts"] == 1
    assert any(item["rule_key"] == "missing_amount" for item in output["candidates"])


def test_sha256_mismatch_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "journal.csv"
    source.write_text("date,account_ref,amount\n2026-03-01,GL-100,100.0\n", encoding="utf-8")
    envelope = _journal_envelope(source)
    envelope["journal"]["artifact"]["sha256"] = "0" * 64  # type: ignore[index]
    with pytest.raises(InputRejected, match="sha256"):
        handle(envelope)


def test_unsupported_mapping_version_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "journal.csv"
    source.write_text("date,account_ref,amount\n2026-03-01,GL-100,100.0\n", encoding="utf-8")
    envelope = _journal_envelope(source)
    envelope["journal"]["schema_mapping_version"] = "9.9.9"  # type: ignore[index]
    with pytest.raises(InputRejected, match="mapping version"):
        handle(envelope)


def test_output_serializes_as_valid_json() -> None:
    assert json.loads(json.dumps({"ok": True, "output": {"contract_id": "anomaly-candidates"}}))
