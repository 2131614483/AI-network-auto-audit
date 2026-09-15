"""Deterministic audit simulation data for the 100-plugin network.

Covers the 8 business stages with representative read-only fixtures under
.data/audit-sim/.  The main chain (ledger -> clean -> anomaly -> matrix ->
issues -> report) uses ledger.csv with seeded anomalies so every plugin in the
chain produces non-empty, assertable output.

Run: python .data/_gen_audit_sim_data.py
"""
from __future__ import annotations

import csv
import io
import json
from pathlib import Path

ROOT = Path(r"D:\pythonpro\audit_network")
OUT = ROOT / ".data" / "audit-sim"

PERIOD = "2026-01"
ACCOUNTS = [
    ("1001", "银行存款"), ("1101", "应收账款"), ("1201", "原材料"),
    ("1401", "应付账款"), ("2001", "主营业务收入"), ("2201", "管理费用"),
    ("2301", "销售费用"), ("3101", "固定资产"),
]


def _ledger_rows() -> list[dict]:
    """Deterministic 2026-01 ledger with seeded anomalies.

    Seeded anomalies (assertable counts):
      - 3 duplicate rows (E9001/E9002/E9003 appear twice each)
      - 4 out-of-period rows (2025-12-31 x2, 2026-02-01, invalid 2026-13-01)
      - 2 unbalanced entries (E9101 debit>credit, E9102 credit>debit)
      - 3 large amounts (>= 1_000_000 threshold)
    """
    rows: list[dict] = []
    seq = 0

    def add(entry_id: str, date: str, account: str, desc: str, debit: float, credit: float) -> None:
        nonlocal seq
        seq += 1
        rows.append({
            "entry_id": entry_id, "date": date, "account_code": account,
            "description": desc, "debit_amount": f"{debit:.2f}", "credit_amount": f"{credit:.2f}",
        })

    # balanced normal entries E0001..E0080
    for i in range(1, 81):
        acct_debit, acct_credit = ACCOUNTS[i % len(ACCOUNTS)], ACCOUNTS[(i + 3) % len(ACCOUNTS)]
        amount = 1000.0 + (i % 40) * 250.0
        add(f"E{i:04d}", f"2026-01-{(i % 28) + 1:02d}", acct_debit[0], f"正常入账{i}", amount, amount)

    # duplicates E9001/E9002/E9003 (each appears twice)
    for eid in ("E9001", "E9002", "E9003"):
        add(eid, "2026-01-10", "1001", f"重复入账{eid}", 8000.00, 8000.00)
        add(eid, "2026-01-10", "1001", f"重复入账{eid}", 8000.00, 8000.00)

    # out-of-period rows
    add("E9201", "2025-12-31", "2201", "跨期费用", 500.00, 500.00)
    add("E9202", "2025-12-31", "2201", "跨期费用", 500.00, 500.00)
    add("E9203", "2026-02-01", "2301", "提前入账", 300.00, 300.00)
    add("E9204", "2026-13-01", "2301", "无效日期", 300.00, 300.00)

    # unbalanced entries
    add("E9101", "2026-01-15", "1101", "借贷不平-借多", 12000.00, 10000.00)
    add("E9102", "2026-01-16", "1101", "借贷不平-贷多", 9000.00, 11000.00)

    # large amounts
    add("E9301", "2026-01-20", "2001", "大额收入", 1_200_000.00, 1_200_000.00)
    add("E9302", "2026-01-21", "2001", "大额收入", 1_500_000.00, 1_500_000.00)
    add("E9303", "2026-01-22", "1401", "大额应付", 1_000_000.00, 1_000_000.00)

    # zero-balance totals row (must not break balance checks)
    add("E9999", "2026-01-31", "2201", "期间汇总", 0.00, 0.00)
    return rows


def _purchase_rows() -> list[dict]:
    vendors = ["V-1001", "V-1002", "V-1003", "V-1004", "V-1005"]
    return [
        {"po_id": f"PO{i:04d}", "vendor": vendors[i % len(vendors)], "amount": 10_000.0 + i * 137.0,
         "receipt_status": "accepted" if i % 7 else "no_receipt", "po_date": f"2026-01-{(i % 28) + 1:02d}"}
        for i in range(1, 61)
    ]


def _sales_rows() -> list[dict]:
    return [
        {"invoice_id": f"INV{i:04d}", "customer": f"C-{1000 + i}", "amount": 5000.0 + i * 89.0,
         "discount_rate": 0.15 if i % 11 == 0 else 0.03, "invoice_date": f"2026-01-{(i % 28) + 1:02d}"}
        for i in range(1, 61)
    ]


def _inventory_rows() -> list[dict]:
    return [
        {"sku": f"SKU-{i:03d}", "book_qty": 100 + i, "actual_qty": (100 + i - 5) if i % 9 == 0 else (100 + i),
         "unit_cost": 50.0 + i * 2.5}
        for i in range(1, 41)
    ]


def _master_vendors() -> list[dict]:
    return [
        {"vendor_id": v, "name": f"供应商{idx}", "credit_limit": 200_000 + idx * 50_000, "risk_level": "high" if idx in (2, 4) else "normal"}
        for idx, v in enumerate(["V-1001", "V-1002", "V-1003", "V-1004", "V-1005"], start=1)
    ]


def _master_accounts() -> list[dict]:
    return [{"account_code": code, "account_name": name, "category": "asset" if code[0] in "13" else "liability" if code[0] == "1" else "revenue" if code[0] == "2" and code[1] == "0" else "expense"} for code, name in ACCOUNTS]


def _process_map() -> dict:
    return {
        "contract_id": "process-map",
        "processes": [
            {"process_id": "P1", "name": "采购付款", "controls": [
                {"control_id": "C1-1", "name": "采购订单审批", "effective": True},
                {"control_id": "C1-2", "name": "收货验收单核对", "effective": False, "note": "断点：无验收单即付款"},
                {"control_id": "C1-3", "name": "供应商对账", "effective": True},
            ]},
            {"process_id": "P2", "name": "销售收款", "controls": [
                {"control_id": "C2-1", "name": "信用额度审批", "effective": True},
                {"control_id": "C2-2", "name": "折扣审批", "effective": False, "note": "断点：折扣率超限未审批"},
            ]},
        ],
    }


def _interviews() -> dict:
    return {
        "contract_id": "interview-set",
        "interviews": [
            {"interview_id": "IV1", "interviewee": "采购部-张", "date": "2026-01-18", "key_points": ["验收流程存在口头验收", "供应商选择未按制度轮换"]},
            {"interview_id": "IV2", "interviewee": "财务部-李", "date": "2026-01-19", "key_points": ["月末集中入账", "部分凭证后补审批"]},
        ],
    }


def _history_issues() -> list[dict]:
    categories = ["采购", "资金", "销售", "存货", "费用"]
    return [
        {"issue_id": f"HIS{i:04d}", "year": 2023 + i % 3, "category": categories[i % len(categories)], "amount": 1000.0 * i}
        for i in range(1, 41)
    ]


def _remedy_tasks() -> list[dict]:
    return [
        {"task_id": f"RT{i:04d}", "issue_ref": f"I{i:04d}", "owner": f"部门{1 + i % 4}",
         "status": "closed" if i % 3 else "overdue", "due_date": f"2026-0{(i % 9) + 1:02d}-15"}
        for i in range(1, 31)
    ]


def _report_template() -> dict:
    return {
        "contract_id": "report-frame",
        "sections": ["审计概况", "审计范围与方法", "主要问题", "审计建议", "整改要求", "审计结论"],
        "issue_sections": ["问题描述", "问题金额", "责任主体", "定性分级", "整改建议"],
    }


def _write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        raise ValueError(f"empty rows for {path.name}")
    with io.open(path, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    files: dict[str, object] = {
        "ledger.csv": _ledger_rows(),
        "purchase-orders.csv": _purchase_rows(),
        "sales-invoices.csv": _sales_rows(),
        "inventory.csv": _inventory_rows(),
        "master-vendors.csv": _master_vendors(),
        "master-accounts.csv": _master_accounts(),
    }
    for name, rows in files.items():
        assert isinstance(rows, list) and rows and isinstance(rows[0], dict)
        _write_csv(OUT / name, rows)
    (OUT / "process-map.json").write_text(json.dumps(_process_map(), ensure_ascii=False, indent=1), encoding="utf-8")
    (OUT / "interviews.json").write_text(json.dumps(_interviews(), ensure_ascii=False, indent=1), encoding="utf-8")
    (OUT / "report-template.json").write_text(json.dumps(_report_template(), ensure_ascii=False, indent=1), encoding="utf-8")
    _write_csv(OUT / "history-issues.csv", _history_issues())
    _write_csv(OUT / "remedy-tasks.csv", _remedy_tasks())

    ledger = _ledger_rows()
    dupes = sum(1 for eid in ("E9001", "E9002", "E9003") if sum(1 for r in ledger if r["entry_id"] == eid) == 2)
    print(f"audit-sim ready at {OUT}")
    print(f"  ledger rows={len(ledger)} duplicates_pairs={dupes} out_of_period=4 unbalanced=2 large=3")
    print("  files:", sorted(p.name for p in OUT.iterdir()))


if __name__ == "__main__":
    main()
