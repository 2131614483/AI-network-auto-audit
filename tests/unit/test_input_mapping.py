"""输入适配层：声明式映射。

这一层的价值全在**不猜**上：列名不对就报错、分区键取不到就跳过并计数。
一旦它开始"猜"（按位置对齐、模糊匹配列名、给残缺行编一个月份），
错误就会被埋进数据里，而下游看到的是"跑通了"——所以这几条不变量必须钉住。
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from packages.catalog.input_mapping import MappingError, apply_mapping, mapping

SPEC = {
    "target_contract": "ledger-artifact-ref",
    "source": {"glob": "**/记账凭证*.csv", "encoding": "utf-8-sig"},
    "partition_by": {"field": "period", "from": "记账日期", "transform": "year-month"},
    "columns": {
        "entry_id": "凭证号",
        "date": "记账日期",
        "account_code": "科目编码",
        "description": "摘要",
    },
    "amount_columns": {
        "debit_amount": "借方金额",
        "credit_amount": "贷方金额",
        "blank_value": "0.00",
    },
    "artifact": {"name": "ledger", "media_type": "text/csv", "classification": "audit_ledger"},
}

ROWS = [
    ["PZ00001", "2025-11-05", "1002", "收款", "180000000.00", ""],
    ["PZ00001", "2025-11-05", "6001", "收款", "", "180000000.00"],
    ["PZ00002", "2025-12-31", "1002", "付款", "1200.00", ""],
    ["PZ00003", "", "1002", "日期残缺", "1.00", ""],
]


def _write_source(root: Path) -> Path:
    target = root / "01_被审计单位提供资料" / "记账凭证_2025年度明细.csv"
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["凭证号", "记账日期", "科目编码", "摘要", "借方金额", "贷方金额"])
        writer.writerows(ROWS)
    return target


def test_partitions_by_month_and_applies_blank_value(tmp_path: Path) -> None:
    _write_source(tmp_path)
    result = apply_mapping(SPEC, tmp_path, tmp_path / "out")

    # 日期残缺的那一行被跳过，且**计数**在案 —— 不静默丢弃、也不给它编一个月份。
    assert result["total_rows"] == 4
    assert result["skipped_rows"] == 1
    assert sorted(result["artifacts"]) == ["2025-11", "2025-12"]

    november = result["artifacts"]["2025-11"]
    assert november["rows"] == 2
    assert november["groups"] == 1          # 同一张凭证的两条分录 = 一个分组
    assert november["classification"] == "audit_ledger"

    with november["path"].open(encoding="utf-8-sig", newline="") as handle:
        written = list(csv.DictReader(handle))
    # 借方分录没有贷方金额是正常记账：空白必须落成 0.00，否则会被判"缺金额"。
    assert written[0]["credit_amount"] == "0.00"
    assert written[1]["debit_amount"] == "0.00"
    assert [row["credit_amount"] for row in written] == ["0.00", "180000000.00"]


def test_missing_declared_column_is_an_error_not_a_guess(tmp_path: Path) -> None:
    """声明里写了、源文件没有的列必须报错 —— 按位置凑会把错误埋进数据。"""
    target = tmp_path / "01_被审计单位提供资料" / "记账凭证_2025年度明细.csv"
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["凭证号", "记账日期", "科目编码", "摘要", "借方金额"])   # 缺 贷方金额
        writer.writerow(ROWS[0])
    with pytest.raises(MappingError, match="缺少映射声明的列"):
        apply_mapping(SPEC, tmp_path, tmp_path / "out")


def test_missing_partition_column_is_an_error(tmp_path: Path) -> None:
    """分区列在源文件里不存在时必须报错 —— 不能退化成"猜一个月份出来"。"""
    target = tmp_path / "01_被审计单位提供资料" / "记账凭证_2025年度明细.csv"
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["凭证号", "科目编码", "摘要", "借方金额", "贷方金额"])   # 缺 记账日期
        writer.writerow(["PZ1", "1002", "x", "1.00", ""])
    # 把分区列从 columns 里摘掉，让"缺分区列"这条检查真的被走到
    # （若它同时在 columns 里，会先被缺列检查拦下 —— 两条都不猜，哪条先报都对）。
    spec = dict(SPEC)
    spec["columns"] = {k: v for k, v in SPEC["columns"].items() if k != "date"}
    with pytest.raises(MappingError, match="缺少分区列"):
        apply_mapping(spec, tmp_path, tmp_path / "out")


def test_no_source_file_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(MappingError, match="找不到源文件"):
        apply_mapping(SPEC, tmp_path, tmp_path / "out")


def test_unknown_transform_is_rejected(tmp_path: Path) -> None:
    _write_source(tmp_path)
    bad = dict(SPEC)
    bad["partition_by"] = {"field": "period", "from": "记账日期", "transform": "fiscal-quarter"}
    with pytest.raises(MappingError, match="未支持的分区变换"):
        apply_mapping(bad, tmp_path, tmp_path / "out")


def test_unknown_mapping_name_is_rejected() -> None:
    """用随包发布的真实声明文件，只把名字写错 —— 验的是"名字未知"，不是"文件不存在"。"""
    with pytest.raises(MappingError, match="未声明的输入映射"):
        mapping("no-such-mapping")


def test_shipped_mapping_declares_the_two_semantics_that_matter() -> None:
    """随包发布的声明必须把两处"做错就没意义"的语义写清楚，而不是留给读者推断。"""
    spec = mapping("audit-ledger-csv-v1")
    assert spec["columns"]["entry_id"] == "凭证号"        # 按凭证分组，不是按行
    assert spec["amount_columns"]["blank_value"] == "0.00"  # 空白是正常记账，不是缺金额
    assert spec["partition_by"]["transform"] == "year-month"
    assert spec["artifact"]["classification"] == "audit_ledger"
    for section in ("partition_by", "columns", "amount_columns", "row_filter"):
        assert "_why" in spec[section], f"{section} 缺少口径说明"
