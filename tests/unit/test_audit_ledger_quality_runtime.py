from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import uuid4

import pytest

from plugins.builtin.audit_ledger_quality.runtime import InputRejected, handle


def _ledger_envelope(path: Path, *, mapping_version: str = "1.0.0", period: str = "2026-01", threshold: float | None = None) -> dict[str, object]:
    content = path.read_bytes()
    ledger: dict[str, object] = {
        "artifact": {
            "artifact_id": str(uuid4()),
            "tenant_id": str(uuid4()),
            "uri": path.resolve().as_uri(),
            "media_type": "text/csv",
            "sha256": hashlib.sha256(content).hexdigest(),
            "size_bytes": len(content),
            "classification": "audit_confidential",
        },
        "schema_mapping_version": mapping_version,
        "period": period,
    }
    if threshold is not None:
        ledger["amount_threshold"] = threshold
    return {"protocol": "audit-network-plugin-child-v1", "plugin_id": "audit.ledger-quality", "capability": "audit.ledger.validate", "ledger": ledger}


@pytest.fixture()
def read_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "ledger-root"
    root.mkdir()
    monkeypatch.setenv("AUDIT_PLUGIN_READ_ROOTS", json.dumps([str(root)]))
    return root


BALANCED_CSV = """entry_id,date,account_code,description,debit_amount,credit_amount
V-01,2026-01-05,1001,销售收入,50000.00,0.00
V-01,2026-01-05,6001,销售收入对冲,0.00,50000.00
V-02,2026-01-12,2001,采购付款,30000.00,0.00
V-02,2026-01-12,8001,采购付款对冲,0.00,30000.00
"""


def _write(root: Path, name: str, content: str) -> Path:
    path = root / name
    path.write_text(content, encoding="utf-8")
    return path


def test_golden_balanced_ledger_produces_no_candidates(read_roots: Path) -> None:
    path = _write(read_roots, "balanced.csv", BALANCED_CSV)
    output = handle(_ledger_envelope(path))
    assert output["contract_id"] == "audit-quality-candidates"
    assert output["summary"]["total_rows"] == 4
    assert output["candidates"] == []
    assert len(output["rule_pack_sha256"]) == 64
    assert "missing_header_columns" not in output["summary"]


def test_duplicate_and_large_amount_rows_are_candidates(read_roots: Path) -> None:
    csv_text = (
        "entry_id,date,account_code,description,debit_amount,credit_amount\n"
        "V-01,2026-01-05,1001,重复样本,2000000.00,0.00\n"
        "V-01,2026-01-05,1001,重复样本,2000000.00,0.00\n"
    )
    path = _write(read_roots, "dupes.csv", csv_text)
    output = handle(_ledger_envelope(path, threshold=1_000_000))
    keys = [candidate["rule_key"] for candidate in output["candidates"]]
    assert output["summary"]["duplicate_rows"] == 1
    assert "duplicate_row" in keys
    assert "large_amount" in keys
    assert any(candidate["reason_code"] == "DUPLICATE_ROW" for candidate in output["candidates"])


def test_unbalanced_entry_is_reported_with_row_span(read_roots: Path) -> None:
    csv_text = (
        "entry_id,date,account_code,description,debit_amount,credit_amount\n"
        "V-01,2026-01-05,1001,借方,1000.00,0.00\n"
        "V-01,2026-01-05,8001,贷方,0.00,900.00\n"
    )
    path = _write(read_roots, "unbalanced.csv", csv_text)
    output = handle(_ledger_envelope(path))
    unbalanced = [candidate for candidate in output["candidates"] if candidate["rule_key"] == "unbalanced_entry"]
    assert len(unbalanced) == 1
    assert unbalanced[0]["reason_code"] == "UNBALANCED_ENTRY"
    assert unbalanced[0]["severity"] == "high"
    assert unbalanced[0]["evidence"]["row_span"] == [1, 2]
    assert output["summary"]["unbalanced_entries"] == 1


def test_out_of_period_and_invalid_date_are_reported(read_roots: Path) -> None:
    csv_text = (
        "entry_id,date,account_code,description,debit_amount,credit_amount\n"
        "V-01,2026-02-05,1001,下月,10.00,0.00\n"
        "V-02,2026-01-99,1001,坏日期,10.00,0.00\n"
        "V-03,,1001,缺日期,10.00,0.00\n"
    )
    path = _write(read_roots, "period.csv", csv_text)
    output = handle(_ledger_envelope(path, period="2026-01"))
    codes = {candidate["reason_code"] for candidate in output["candidates"] if candidate["rule_key"] == "out_of_period"}
    assert {"OUT_OF_PERIOD", "INVALID_DATE", "MISSING_DATE"}.issubset(codes)
    assert output["summary"]["out_of_period_rows"] == 3


def test_missing_required_header_emits_mapping_candidate(read_roots: Path) -> None:
    csv_text = "entry_id,date,account_code,description\nV-01,2026-01-05,1001,只有四列\n"
    path = _write(read_roots, "missing-header.csv", csv_text)
    output = handle(_ledger_envelope(path))
    missing = [candidate for candidate in output["candidates"] if candidate["rule_key"] == "missing_header"]
    assert len(missing) == 1
    assert "debit_amount" in missing[0]["evidence"]["missing_columns"]
    assert output["summary"]["missing_header_columns"] == ["debit_amount", "credit_amount"]


def test_missing_or_invalid_amount_is_a_candidate(read_roots: Path) -> None:
    csv_text = (
        "entry_id,date,account_code,description,debit_amount,credit_amount\n"
        "V-01,2026-01-05,1001,空金额,,\n"
        "V-02,2026-01-05,1001,坏金额,abc,100.00\n"
    )
    path = _write(read_roots, "bad-amount.csv", csv_text)
    output = handle(_ledger_envelope(path))
    amounts = [candidate for candidate in output["candidates"] if candidate["rule_key"] == "missing_or_invalid_amount"]
    assert len(amounts) == 2


def test_duplicate_request_with_same_immutable_input_is_idempotent(read_roots: Path) -> None:
    path = _write(read_roots, "idem.csv", BALANCED_CSV)
    first = handle(_ledger_envelope(path))
    second = handle(_ledger_envelope(path))
    assert first["ledger_sha256"] == second["ledger_sha256"]
    assert first["rule_pack_sha256"] == second["rule_pack_sha256"]
    assert first["candidates"] == second["candidates"]


def test_tampered_sha256_is_rejected(read_roots: Path) -> None:
    path = _write(read_roots, "tampered.csv", BALANCED_CSV)
    envelope = _ledger_envelope(path)
    envelope["ledger"]["artifact"]["sha256"] = "0" * 64  # type: ignore[index]
    with pytest.raises(InputRejected, match="sha256"):
        handle(envelope)


def test_size_mismatch_is_rejected(read_roots: Path) -> None:
    path = _write(read_roots, "sized.csv", BALANCED_CSV)
    envelope = _ledger_envelope(path)
    envelope["ledger"]["artifact"]["size_bytes"] = 1  # type: ignore[index]
    with pytest.raises(InputRejected, match="size"):
        handle(envelope)


def test_unsupported_schema_mapping_version_is_rejected(read_roots: Path) -> None:
    path = _write(read_roots, "mapping.csv", BALANCED_CSV)
    with pytest.raises(InputRejected, match="schema mapping version"):
        handle(_ledger_envelope(path, mapping_version="0.9.0"))


def test_invalid_period_is_rejected(read_roots: Path) -> None:
    path = _write(read_roots, "period-bad.csv", BALANCED_CSV)
    with pytest.raises(InputRejected, match="period"):
        handle(_ledger_envelope(path, period="2026-13"))


def test_non_csv_artifact_is_rejected(read_roots: Path) -> None:
    path = _write(read_roots, "not-ledger.txt", "not a ledger")
    with pytest.raises(InputRejected, match="CSV"):
        handle(_ledger_envelope(path))


def test_empty_ledger_has_zero_rows(read_roots: Path) -> None:
    path = _write(read_roots, "empty.csv", "entry_id,date,account_code,description,debit_amount,credit_amount\n")
    output = handle(_ledger_envelope(path))
    assert output["summary"]["total_rows"] == 0
    assert output["candidates"] == []


def test_artifact_outside_declared_read_roots_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUDIT_PLUGIN_READ_ROOTS", json.dumps([str(tmp_path / "allowed")]))
    outside = tmp_path / "ledger-root-outside.csv"
    outside.write_text(BALANCED_CSV, encoding="utf-8")
    with pytest.raises(InputRejected, match="read roots"):
        handle(_ledger_envelope(outside))


def test_environment_without_read_roots_is_rejected(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("AUDIT_PLUGIN_READ_ROOTS", raising=False)
    path = tmp_path / "no-env.csv"
    path.write_text(BALANCED_CSV, encoding="utf-8")
    with pytest.raises(InputRejected, match="read roots"):
        handle(_ledger_envelope(path))


def test_candidate_cap_marks_summary_truncated(read_roots: Path) -> None:
    header = "entry_id,date,account_code,description,debit_amount,credit_amount"
    rows = []
    for i in range(10_001):
        rows.append(f"V-{i:05d},2026-01-05,1001,缺金额,,")
    csv_text = header + "\n" + "\n".join(rows) + "\n"
    path = _write(read_roots, "many-candidates.csv", csv_text)
    output = handle(_ledger_envelope(path))
    assert output["summary"]["truncated"] is True
    assert output["summary"]["candidate_count"] == 10_000
    assert output["summary"]["total_rows"] == 10_001