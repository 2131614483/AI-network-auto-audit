# -*- coding: utf-8 -*-
"""Part 2: AAR baseline details + pay details (read-only)."""
import csv, collections, io, math, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

def rows(p):
    with open(p, newline="", encoding="utf-8-sig") as f:
        r = list(csv.reader(f))
        return r[0], [x for x in r[1:] if any(c.strip() for c in x)]

h7, r7 = rows(r"E:\数据\04-审计数据集与基准\Audit-Risk-Classification\audit_risk.csv")
def coln(name): return h7.index(name)

risk = coln("Risk")
loc = coln("LOCATION_ID")
high = sum(1 for x in r7 if x[risk].strip() == "1")
print("high-risk count:", high, f"({100*high/776:.1f}%)")
locs = sorted(set(x[loc].strip() for x in r7 if x[loc].strip()))
print("locations:", len(locs), locs)
null_loc = sum(1 for x in r7 if not x[loc].strip())
print("null LOCATION rows:", null_loc)

# numeric columns only (skip text columns) & corr with Risk
def is_num(v):
    try: float(v); return True
    except (ValueError, TypeError): return False
res = []
riskv = [float(x[risk]) for x in r7]
for i, name in enumerate(h7):
    if i == risk or not all(is_num(x[i]) for x in r7):
        continue
    xs = [float(x[i]) for x in r7]
    mx, my = sum(xs)/len(xs), sum(riskv)/len(riskv)
    numv = sum((a-mx)*(b-my) for a, b in zip(xs, riskv))
    dx = sum((a-mx)**2 for a in xs)**.5
    dy = sum((b-my)**2 for b in riskv)**.5
    res.append((name, numv/(dx*dy) if dx and dy else 0))
res.sort(key=lambda t: -abs(t[1]))
print("corr with Risk (|r| top 10):")
for k, v in res[:10]:
    print(f"  {k:16} r={v:+.4f} |r|={abs(v):.4f}")

# AAR identity
ar_ = coln("Audit_Risk"); inr = coln("Inherent_Risk")
cr = coln("CONTROL_RISK"); dr = coln("Detection_Risk")
bad = 0; vals = []
for x in r7:
    lhs = float(x[inr]) * float(x[cr]) * float(x[dr])
    rhs = float(x[ar_])
    if abs(lhs - rhs) > 1e-6: bad += 1
    vals.append(rhs)
vals.sort()
print(f"AAR identity mismatches: {bad}/776")
def q(a, p): return a[min(len(a)-1, int(len(a)*p))]
print("Audit_Risk p25/p50/p75/p90/p100:", q(vals,.25), q(vals,.5), q(vals,.75), q(vals,.9), vals[-1])
risk_vals = [float(x[risk]) for x in r7]
print("Risk mean:", round(sum(risk_vals)/776, 4))

# totals sanity
tot_audit = sum(vals)
print("sum Audit_Risk:", round(tot_audit, 2), " n*:", round(776*1.0,2))

# pay by segment quantiles
hp, rp = rows(r"E:\数据\03-AI审计技能包\financeskills\skills\forensic-accounting\evals\files\disbursements.csv")
print("\npay segments:")
for seg in ("Operations", "Procurement-VendorX"):
    aa = sorted(float(x[2]) for x in rp if x[1] == seg)
    print(f"  {seg:22} n={len(aa):4d} sum={sum(aa):14,.2f} mean={sum(aa)/len(aa):10,.2f} min={aa[0]:.2f} p95={q(aa,.95):,.2f} max={aa[-1]:,.2f}")
ids = [x[0] for x in rp]
print("pay id sample:", ids[:2], "...", ids[-1], " dup ids:", len(ids)-len(set(ids)))