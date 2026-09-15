# -*- coding: utf-8 -*-
"""Dump raw-looking sample rows for the report (read-only)."""
import csv, io, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

def dump(p, n=5, tail="", label=""):
    with open(p, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))
    print(f"--- {label or p} ({len(rows)-1} data rows) ---")
    for i, r in enumerate(rows[:n+1]):
        mark = "HEADER" if i == 0 else f"row{i:02d}"
        print(f"[{mark}] " + " | ".join(r))
    if tail:
        print("  ...")
        for r in rows[-tail:]:
            print("  [tail ] " + " | ".join(r))
    print()

B = r"E:\数据"
dump(rf"{B}\03-AI审计技能包\financeskills\projects\audit-system\data\raw_transactions.csv", 6, 0, "S1 交易流水 raw_transactions.csv (1,000×5)")
dump(rf"{B}\03-AI审计技能包\financeskills\skills\ai-anomaly-detection\evals\files\expense_ledger.csv", 5, 0, "S2 费用台账 expense_ledger.csv (120×3)")
dump(rf"{B}\03-AI审计技能包\financeskills\skills\automated-reconciliation\evals\files\bank_statement.csv", 9, 0, "S3 银行对账单 bank_statement.csv (9×4)")
dump(rf"{B}\03-AI审计技能包\financeskills\skills\automated-reconciliation\evals\files\gl_cash.csv", 10, 0, "S4 现金日记账 gl_cash.csv (10×4)")
dump(rf"{B}\03-AI审计技能包\financeskills\skills\automated-reconciliation\evals\files\bank_dirty.csv", 5, 0, "S5 银行脏队列 bank_dirty.csv (5×4)")
dump(rf"{B}\03-AI审计技能包\financeskills\skills\audit-checklist\evals\files\control_register.csv", 5, 0, "S6 内控登记 control_register.csv (5×8)")
dump(rf"{B}\03-AI审计技能包\financeskills\skills\forensic-accounting\evals\files\disbursements.csv", 7, 0, "S7 付款单据 disbursements.csv (850×3)")
dump(rf"{B}\04-审计数据集与基准\Audit-Risk-Classification\audit_risk.csv", 3, 0, "S8 审计风险基准 audit_risk.csv (776×27)")