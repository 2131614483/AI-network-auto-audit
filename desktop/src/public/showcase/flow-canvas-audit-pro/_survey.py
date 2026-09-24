# -*- coding: utf-8 -*-
"""Full quantitative survey for the audit report (read-only)."""
import csv, collections, datetime, io, math, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

def rows(p):
    with open(p, newline="", encoding="utf-8-sig") as f:
        r = list(csv.reader(f))
        return r[0], [x for x in r[1:] if any(c.strip() for c in x)]

P = {
 "ledger": r"E:\数据\03-AI审计技能包\financeskills\projects\audit-system\data\raw_transactions.csv",
 "expense": r"E:\数据\03-AI审计技能包\financeskills\skills\ai-anomaly-detection\evals\files\expense_ledger.csv",
 "bank": r"E:\数据\03-AI审计技能包\financeskills\skills\automated-reconciliation\evals\files\bank_statement.csv",
 "gl": r"E:\数据\03-AI审计技能包\financeskills\skills\automated-reconciliation\evals\files\gl_cash.csv",
 "dirty": r"E:\数据\03-AI审计技能包\financeskills\skills\automated-reconciliation\evals\files\bank_dirty.csv",
 "controls": r"E:\数据\03-AI审计技能包\financeskills\skills\audit-checklist\evals\files\control_register.csv",
 "pay": r"E:\数据\03-AI审计技能包\financeskills\skills\forensic-accounting\evals\files\disbursements.csv",
 "aar": r"E:\数据\04-审计数据集与基准\Audit-Risk-Classification\audit_risk.csv",
}
print("=== 1. LEDGER (raw_transactions.csv) ===")
h, r = rows(P["ledger"])
print("header:", h)
vc = collections.Counter(x[2] for x in r)
vs = {}
for x in r:
    vs.setdefault(x[2], 0); vs[x[2]] += float(x[3])
tot = sum(vs.values())
for v, c in vc.most_common():
    print(f"  vendor {v!r:28} n={c:4d}  sum={vs[v]:12,.2f}  pct={100*vs[v]/tot:5.1f}%")
print("  total$ =", f"{tot:,.2f}")
amts = sorted(float(x[3]) for x in r)
print("  n=1000 min", amts[0], "p5", amts[50], "p50", amts[500], "p95", amts[950], "max", amts[-1])
late = sum(1 for x in r if x[1][11:13] >= "22" and x[1].endswith("PM") if "PM" in x[1])
print("  PM rows:", sum(1 for x in r if "PM" in x[1]), " AM rows:", sum(1 for x in r if "AM" in x[1]))
print("  date sample:", r[0][1], "| id sample:", r[0][0])

print("\n=== 2. EXPENSE (expense_ledger.csv) ===")
h3, r3 = rows(P["expense"])
print("header:", h3)
print("dates first/last:", r3[0][0], r3[-1][0], "n=", len(r3))
amm = [float(x[2]) for x in r3]
print("amount distn:", collections.Counter(amm).most_common(8))
q = lambda a, p: sorted(a)[min(len(a) - 1, int(len(a) * p))]
q1, q3 = q(amm, .25), q(amm, .75)
print(f"  q1={q1} q3={q3} iqr={q3-q1} low<{q1-1.5*(q3-q1)} high>{q3+1.5*(q3-q1)}")
for x in r3:
    try: datetime.date.fromisoformat(x[0])
    except ValueError: print("  INVALID DATE:", x)
    if float(x[2]) <= q1 - 1.5 * (q3 - q1) or float(x[2]) >= q3 + 1.5 * (q3 - q1):
        print("  OUTLIER AMT :", x)

print("\n=== 3. BANK RECON ===")
hb, rb = rows(P["bank"]); print("bank header:", hb, "n=", len(rb))
hg, rg = rows(P["gl"]); print("gl header  :", hg, "n=", len(rg))
for x in rb: print("  B:", x)
for x in rg: print("  G:", x)
print("\ndirty bank (bank_dirty.csv):")
hd, rd = rows(P["dirty"]); print("header:", hd, "n=", len(rd))
for x in rd: print("  D:", x)

print("\n=== 4. CONTROLS (control_register.csv) ===")
hc, rc = rows(P["controls"]); print("header:", hc, "n=", len(rc))
for x in rc: print("  C:", x)

print("\n=== 5. PAY (disbursements.csv) ===")
hp, rp = rows(P["pay"]); print("header:", hp, "n=", len(rp))
seg = collections.Counter(x[1] for x in rp)
segsum = collections.defaultdict(float)
for x in rp: segsum[x[1]] += float(x[2])
for k, c in seg.most_common():
    print(f"  seg {k!r:22} n={c:4d}  sum={segsum[k]:14,.2f} pct={100*segsum[k]/sum(segsum.values()):5.1f}%")
a = sorted(float(x[2]) for x in rp)
print("  n=850 min", a[0], "p50", a[425], "p95", a[807], "p99", a[841], "max", a[-1])
obs = collections.Counter(int(str(x)[0]) for x in a)
n = len(a)
print("  Benford: obs", dict(sorted(obs.items())))
chi = 0
row = []
for d in range(1, 10):
    e = n * math.log10(1 + 1 / d); o = obs.get(d, 0)
    chi += (o - e) ** 2 / e
    row.append((d, o, round(e, 1), round(o - e, 1)))
for d, o, e, dev in row: print(f"    d={d} obs={o:4d} exp={e:7.1f} dev={dev:+7.1f}")
print("  chi2 =", round(chi, 2), " crit(df=8,0.05)=", 15.51)

print("\n=== 6. AAR BASELINE (audit_risk.csv) ===")
h7, r7 = rows(P["aar"])
print("cols(27):", h7)
risk = h7.index("Risk"); loc = h7.index("LOCATION_ID")
high = sum(1 for x in r7 if x[risk] == 1)
print(f"  rows=776 high={high} ({100*high/776:.1f}%)  locations={len(set(x[loc] for x in r7))}")
# missing cells
missing = sum(1 for x in r7 for c in x if c is None or c == "")
print("  missing cells:", missing, "of", 776 * 27)
# corr with Risk
def num(a): return [v if isinstance(v, (int,float)) and not isinstance(v,bool) else float(v) for v in a]
riskv = num([x[risk] for x in r7])
res = []
for i, name in enumerate(h7):
    if i == risk or name in ("Score_B2", "Prob2"): 
        pass
    xs = num([x[i] for x in r7])
    pairs = [(x, y) for x, y in zip(xs, riskv) if x is not None]
    xs = [p[0] for p in pairs]; ys = [p[1] for p in pairs]
    mx = sum(xs)/len(xs); my = sum(ys)/len(ys)
    numv = sum((x-mx)*(y-my) for x, y in zip(xs, ys))
    dx = sum((x-mx)**2 for x in xs) ** .5; dy = sum((y-my)**2 for y in ys) ** .5
    res.append((name, numv/(dx*dy) if dx and dy else 0))
res.sort(key=lambda t: -abs(t[1]))
print("  |r| top7:", [(k, round(v, 3), round(abs(v), 3)) for k, v in res[:7]])
# AAR identity
ar_ = h7.index("Audit_Risk"); inr = h7.index("Inherent_Risk")
cr = h7.index("CONTROL_RISK"); dr = h7.index("Detection_Risk")
bad = sum(
    1 for x in r7
    if abs(float(x[inr]) * float(x[cr]) * float(x[dr]) - float(x[ar_])) > 1e-6
)
print("  AAR identity mismatches:", bad)
print("  dollar columns range:", sorted(map(float, h7[19:26])) if False else "n/a")
# distribution of Audit_Risk
arv = sorted(float(x[ar_]) for x in r7)
print("  Audit_Risk p50/p95/max:", arv[388], arv[737], arv[-1])