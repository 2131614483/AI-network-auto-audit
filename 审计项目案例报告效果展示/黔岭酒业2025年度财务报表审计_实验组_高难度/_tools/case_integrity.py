# -*- coding: utf-8 -*-
"""高难度案例的只读完整性校验与证据绑定。

本工具不读取生成器内存状态，也不修改客户资料。它为全部原始输入建立
SHA-256 清单，并按每张 CSV 的业务键普查重复记录；同时校验审计报告是否
绑定当前 ``audit_evidence.json`` 的哈希值。

运行：python case_integrity.py
输出：_tools/source_integrity.json（可重复生成的本机证据快照）
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
DEFAULT_CASE_ROOT = HERE.parent
SOURCE_DIR_NAME = "01_被审计单位提供资料"
EVIDENCE_FILE_NAME = "audit_evidence.json"
VERIFIER_FILE_NAME = "audit_verify_hard.py"
REPORT_NAME = "审计报告-黔岭酒业2025年度财务报表审计(实验组_高难度).md"

# 每张 CSV 的业务唯一键。无法由单一业务编号识别的汇总表采用其业务维度组合，
# 防止把正常的同日多笔流水误判为重复。
PRIMARY_KEYS: dict[str, tuple[str, ...]] = {
    "管理层编制的合并财务报表_未审数.csv": ("报表", "项目"),
    "合并范围内子公司清单.csv": ("统一社会信用代码",),
    "记账凭证_2025年度明细.csv": (
        "凭证号", "主体代码", "科目编码", "借方金额", "贷方金额", "摘要", "单据日期", "合同号"
    ),
    "科目余额表_P_黔岭酒业股份有限公司.csv": ("科目编码",),
    "科目余额表_S1_黔岭销售有限公司.csv": ("科目编码",),
    "科目余额表_S2_黔岭酒类包装有限公司.csv": ("科目编码",),
    "采购发票登记簿.csv": ("发票号",),
    "产销量与销量明细.csv": ("会计主体", "季度", "产品", "出库日期", "销售金额"),
    "存货入库明细表.csv": ("入库单号",),
    "存货收发存明细表.csv": ("会计主体", "存货类别"),
    "固定资产卡片明细表.csv": ("资产编号",),
    "关联方及关联交易清单.csv": ("关联方名称", "本期交易类型"),
    "期间费用明细表.csv": ("费用项目",),
    "无形资产及长期待摊费用明细表.csv": ("项目",),
    "销售出库单清单.csv": ("出库单号",),
    "销售明细_分产品分渠道分地区.csv": ("出库单号",),
    "应付账款账龄明细表.csv": ("会计主体", "供应商名称"),
    "应收账款明细表_按客户.csv": ("会计主体", "客户名称", "原销售合同"),
    "应收账款账龄明细表_管理层编制.csv": ("会计主体", "客户名称"),
    "预付账款账龄明细表.csv": ("会计主体", "供应商", "款项性质", "发生日期"),
    "在建工程明细表.csv": ("工程名称",),
    "职工薪酬明细表.csv": ("会计主体", "薪酬类别"),
    "重大销售与采购合同台账.csv": ("合同编号",),
    "主要供应商及付款流水.csv": ("序号",),
    "主要客户清单_前五名.csv": ("排名",),
    "个人所得税申报缴纳表.csv": ("所属期", "扣缴义务人", "缴款主体"),
    "纳税申报与缴纳汇总.csv": ("税种",),
    "应交税费明细表_分主体.csv": ("会计主体", "税种"),
    "借款合同与质押清单.csv": ("合同号",),
    "银行存款日记账_募集资金专户_2025.csv": ("日期", "摘要", "借方(收款)", "贷方(付款)"),
    "银行存款日记账_黔岭销售基本户_2025.csv": ("日期", "摘要", "借方(收款)", "贷方(付款)"),
    "银行对账单_黔岭销售基本户_2025.csv": ("日期", "摘要", "借方(收款)", "贷方(付款)"),
    "银行账户清单.csv": ("账号",),
    "年末资产盘点表.csv": ("资产编号",),
    "政府补助文件摘要.csv": ("文号",),
}


def sha256_file(path: Path) -> str:
    """Return the SHA-256 of a local file without loading it all into memory."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_sha256(value: Any) -> str:
    """Hash a JSON value in a stable, encoding-explicit form."""
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_source_inventory(source_root: Path) -> list[dict[str, Any]]:
    """Create a deterministic fingerprint inventory for every customer source file."""
    return [
        {
            "path": path.relative_to(source_root).as_posix(),
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }
        for path in sorted(source_root.rglob("*"))
        if path.is_file()
    ]


def scan_csv_key_duplicates(source_root: Path) -> list[dict[str, Any]]:
    """Scan every CSV against its declared business key and retain duplicate evidence."""
    scans: list[dict[str, Any]] = []
    for path in sorted(source_root.rglob("*.csv")):
        with path.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            headers = tuple(reader.fieldnames or ())
            key_fields = PRIMARY_KEYS.get(path.name)
            if key_fields is None:
                scans.append(
                    {
                        "path": path.relative_to(source_root).as_posix(),
                        "status": "review_required",
                        "reason": "未声明业务唯一键",
                        "row_count": 0,
                        "key_fields": [],
                        "duplicate_keys": [],
                    }
                )
                continue
            missing_fields = [field for field in key_fields if field not in headers]
            if missing_fields:
                scans.append(
                    {
                        "path": path.relative_to(source_root).as_posix(),
                        "status": "fail",
                        "reason": f"业务键字段缺失：{', '.join(missing_fields)}",
                        "row_count": 0,
                        "key_fields": list(key_fields),
                        "duplicate_keys": [],
                    }
                )
                continue

            counts: Counter[tuple[str, ...]] = Counter()
            row_count = 0
            for row in reader:
                row_count += 1
                key = tuple((row.get(field) or "").strip() for field in key_fields)
                counts[key] += 1
            duplicates = [
                {"key": list(key), "count": count}
                for key, count in sorted(counts.items())
                if count > 1
            ]
            scans.append(
                {
                    "path": path.relative_to(source_root).as_posix(),
                    "status": "pass" if not duplicates else "fail",
                    "row_count": row_count,
                    "key_fields": list(key_fields),
                    "duplicate_keys": duplicates,
                }
            )
    return scans


def report_evidence_binding(report_path: Path, evidence_sha256: str) -> tuple[bool, str | None]:
    """Confirm the report explicitly pins the exact evidence JSON hash it cites."""
    if not report_path.is_file():
        return False, None
    text = report_path.read_text(encoding="utf-8")
    matched = re.search(r"audit_evidence\.json SHA-256：`([0-9a-f]{64})`", text)
    if matched is None:
        return False, None
    recorded_hash = matched.group(1)
    return recorded_hash == evidence_sha256, recorded_hash


def build_manifest(case_root: Path = DEFAULT_CASE_ROOT) -> dict[str, Any]:
    """Return a deterministic, read-only integrity and evidence-binding manifest."""
    source_root = case_root / SOURCE_DIR_NAME
    evidence_path = case_root / "_tools" / EVIDENCE_FILE_NAME
    verifier_path = case_root / "_tools" / VERIFIER_FILE_NAME
    # 高难度案例的审计报告就放在案例根目录下，而同系列另外两个案例放在父目录的
    # 「07_审计成果/」子目录里；两处都探测，避免把布局差异当成「报告缺失」。
    report_path = case_root / REPORT_NAME
    if not report_path.is_file():
        report_path = case_root.parent / REPORT_NAME

    source_files = build_source_inventory(source_root)
    csv_key_scans = scan_csv_key_duplicates(source_root)
    evidence_sha256 = sha256_file(evidence_path)
    bound, recorded_hash = report_evidence_binding(report_path, evidence_sha256)
    source_inventory_sha256 = canonical_sha256(
        {"source_files": source_files, "csv_key_scans": csv_key_scans}
    )
    return {
        "schema_version": "1.0.0",
        "case": case_root.name,
        "source_file_count": len(source_files),
        "source_files": source_files,
        "csv_key_scan_count": len(csv_key_scans),
        "csv_key_scans": csv_key_scans,
        "source_inventory_sha256": source_inventory_sha256,
        "evidence_binding": {
            "audit_evidence_sha256": evidence_sha256,
            "audit_verify_hard_sha256": sha256_file(verifier_path),
            "report_path": report_path.name,
            "report_recorded_evidence_sha256": recorded_hash,
            "report_binds_current_evidence": bound,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="生成高难度案例的只读完整性快照")
    parser.add_argument(
        "--output", type=Path, default=HERE / "source_integrity.json", help="输出 JSON 文件路径"
    )
    arguments = parser.parse_args()
    manifest = build_manifest()
    arguments.output.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    failed = [item for item in manifest["csv_key_scans"] if item["status"] != "pass"]
    binding = manifest["evidence_binding"]["report_binds_current_evidence"]
    print(
        f"源文件 {manifest['source_file_count']} 个；CSV 业务键普查 "
        f"{manifest['csv_key_scan_count']} 张；失败 {len(failed)} 张；"
        f"报告证据绑定 {'通过' if binding else '未通过'}。"
    )
    return 0 if not failed and binding else 1


if __name__ == "__main__":
    raise SystemExit(main())
