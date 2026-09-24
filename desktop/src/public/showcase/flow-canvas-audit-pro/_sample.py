# -*- coding: utf-8 -*-
"""Sample a few real rows per source for interface examples (read-only)."""
import csv, io, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

def rows(p, n=3):
    with open(p, newline="", encoding="utf-8-sig") as f:
        r = list(csv.reader(f))
    return r[0], r[1:n+1]

for k, p in {
 "ledger": r"E:\数据\03-AI审计技能包\financeskills\projects\audit-system\data\raw_transactions.csv",
 "expense": r"E:\数据\03-AI审计技能包\financeskills\skills\ai-anomaly-detection\evals\files\expense_ledger.csv",
 "pay": r"E:\数据\03-AI审计技能包\financeskills\skills\forensic-accounting\evals\files\disbursements.csv",
}.items():
    h, r = rows(p)
    print(f"== {k}")
    print("  header:", h)
    for row in r:
        print("  row:", row)

h, r = rows(r"E:\数据\04-审计数据集与基准\Audit-Risk-Classification\audit_risk.csv", 2)
print("== baseline")
print("  cols(27):", h)
for row in r:
    print("  row(27):", row)