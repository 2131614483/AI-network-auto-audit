# -*- coding: utf-8 -*-
"""审计组视角的数据交叉核对：**只读 CSV 文件**，不引用生成器的内存变量。

用途：证明这批资料是"可独立复核的" —— 审计师拿到文件夹后，
不依赖造数据的人，只读文件就能把各个明细表与总账对上。

运行：python verify_client_data.py
"""

from __future__ import annotations

import csv
import os
import sys
from decimal import Decimal

sys.stdout.reconfigure(encoding="utf-8")
from coa import D, q  # noqa: E402

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(HERE, "01_被审计单位提供资料")


def rows(folder: str, name: str) -> list[dict]:
    path = os.path.join(DATA, folder, name)
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def num(s) -> Decimal:
    if s is None:
        return D("0")
    t = str(s).strip().replace(",", "")
    if not t or t in {"—", "-", "待提供"}:
        return D("0")
    t = t.rstrip("%")
    try:
        return q(t)
    except Exception:  # noqa: BLE001 - 客户资料里可能有"未确定"之类文字
        return D("0")


RESULTS: list[tuple[str, Decimal, Decimal, str]] = []


def check(name: str, got: Decimal, exp: Decimal, note: str = "") -> None:
    ok = abs(got - exp) <= D("0.05")
    RESULTS.append((name, got, exp, "✓" if ok else "✗"))
    mark = "✓" if ok else "✗"
    print(f"  {mark} {name}")
    print(f"      明细/复算 = {got:>22,.2f}   总账 = {exp:>22,.2f}"
          + (f"   {note}" if note else ""))


def main() -> None:
    print("=" * 96)
    print("审计组交叉核对（仅读文件，不引用生成脚本）")
    print("=" * 96)

    tb: dict[str, dict[str, Decimal]] = {}
    for ent in ("P", "S1", "S2"):
        fn = [f for f in os.listdir(os.path.join(DATA, "01_账务资料")) if f.startswith(f"科目余额表_{ent}_")][0]
        tb[ent] = {r["科目编码"]: num(r["期末余额(借正贷负)"])
                   for r in rows("01_账务资料", fn)}

    # 1 试算平衡
    print("\n【1】三主体试算平衡（借方合计 − 贷方合计 = 0）")
    for ent, name in (("P", "母公司"), ("S1", "黔岭销售"), ("S2", "黔岭包装")):
        check(f"{name} 试算平衡", sum(tb[ent].values(), D("0")), D("0"))

    # 2 应收账款账龄 vs 总账
    print("\n【2】应收账款账龄明细 vs 总账 1122（按主体）")
    aging_by_entity: dict[str, Decimal] = {"黔岭酒业股份有限公司": D("0"),
                                           "黔岭销售有限公司": D("0"),
                                           "黔岭酒类包装有限公司": D("0")}
    for r in rows("02_业务资料", "应收账款账龄明细表_管理层编制.csv"):
        aging_by_entity[r["会计主体"]] += num(r["期末余额"])
    for ent, cn in (("P", "黔岭酒业股份有限公司"), ("S1", "黔岭销售有限公司"),
                    ("S2", "黔岭酒类包装有限公司")):
        check(f"{cn} 应收账款", aging_by_entity[cn], tb[ent].get("1122", D("0")))

    # 3 账龄合计与坏账准备
    print("\n【3】账龄区间合计 = 期末余额；坏账准备 vs 总账 1231")
    bucket = D("0")
    prov = D("0")
    for r in rows("02_业务资料", "应收账款账龄明细表_管理层编制.csv"):
        bucket += (num(r["1年以内"]) + num(r["1至2年"]) + num(r["2至3年"]) + num(r["3年以上"]))
        prov += num(r["管理层计提坏账准备"])
    check("账龄区间合计", bucket, sum(aging_by_entity.values(), D("0")))
    check("管理层计提坏账准备合计", prov, -sum((tb[e].get("1231", D("0")) for e in tb), D("0")))

    # 4 存货收发存 vs 总账
    print("\n【4】存货收发存期末 vs 总账 1401+1402+1405（按主体）")
    inv: dict[str, Decimal] = {}
    for r in rows("02_业务资料", "存货收发存明细表.csv"):
        inv[r["会计主体"]] = inv.get(r["会计主体"], D("0")) + num(r["期末余额"])
    for ent, cn in (("P", "黔岭酒业股份有限公司"), ("S1", "黔岭销售有限公司"),
                    ("S2", "黔岭酒类包装有限公司")):
        check(f"{cn} 存货", inv.get(cn, D("0")),
              sum((tb[ent].get(c, D("0")) for c in ("1401", "1402", "1405")), D("0")))

    # 5 固定资产卡片 vs 总账
    print("\n【5】固定资产卡片合计 vs 总账 1601 / 1602（按主体）")
    cost: dict[str, Decimal] = {}
    dep: dict[str, Decimal] = {}
    for r in rows("02_业务资料", "固定资产及折旧明细表.csv"):
        cost[r["会计主体"]] = cost.get(r["会计主体"], D("0")) + num(r["原值"])
        dep[r["会计主体"]] = dep.get(r["会计主体"], D("0")) + num(r["期末累计折旧"])
    for ent, cn in (("P", "黔岭酒业股份有限公司"), ("S1", "黔岭销售有限公司"),
                    ("S2", "黔岭酒类包装有限公司")):
        check(f"{cn} 固定资产原值", cost.get(cn, D("0")), tb[ent].get("1601", D("0")))
        check(f"{cn} 累计折旧", dep.get(cn, D("0")), -tb[ent].get("1602", D("0")))

    # 6 折旧重算
    print("\n【6】固定资产折旧重算（直线法：原值×(1−残值率)÷年限）")
    bad = []
    for r in rows("02_业务资料", "固定资产及折旧明细表.csv"):
        cost_ = num(r["原值"])
        rate = num(r["残值率(%)"]) / D("100")
        life = num(r["折旧年限(年)"])
        annual = q(cost_ * (D("1") - rate) / life)
        book = num(r["账面本期折旧"])
        due = num(r["应有本期折旧"])
        start = r["转固日期"]
        cap = q(cost_ * (D("1") - rate))
        dep_open = num(r["期初累计折旧"])
        remaining = cap - dep_open
        if start >= "2025-01-01":
            months = D(12) - D(start[5:7])
            expect = min(q(annual / D(12) * months), max(D("0"), remaining))
        else:
            expect = min(q(annual), max(D("0"), remaining))
        if expect != due:
            bad.append((r["资产编号"], expect, due))
    if bad:
        for b in bad:
            print(f"  ✗ {b[0]} 重算 {b[1]:,.2f} ≠ 表列应有 {b[2]:,.2f}")
    else:
        print("  ✓ 10 张卡片的「应有本期折旧」全部可由卡片参数重算得出")

    # 7 期间费用 vs 总账
    print("\n【7】期间费用明细小计 vs 总账 6601 / 6602 / 6603（三主体合计）")
    exp_sub: dict[str, Decimal] = {}
    for r in rows("02_业务资料", "期间费用明细表_按性质分类.csv"):
        if r["费用项目"].endswith("小计"):
            exp_sub[r["费用项目"].replace("小计", "")] = num(r["本期发生额"])
    for code, label in (("6601", "销售费用"), ("6602", "管理费用"), ("6603", "研发费用")):
        check(f"{label}", exp_sub.get(label, D("0")),
              sum((tb[e].get(code, D("0")) for e in tb), D("0")))

    # 8 应交税费 vs 总账
    print("\n【8】应交税费按税种期末 vs 总账 2221（三主体合计）")
    tax = sum((num(r["期末余额"]) for r in rows("04_税务资料", "应交税费明细表_按税种.csv")), D("0"))
    check("应交税费期末", tax, -sum((tb[e].get("2221", D("0")) for e in tb), D("0")))

    # 9 应付职工薪酬 vs 总账
    print("\n【9】职工薪酬明细期末 vs 总账 2211")
    sal: dict[str, Decimal] = {}
    for r in rows("02_业务资料", "职工薪酬明细表.csv"):
        sal[r["会计主体"]] = sal.get(r["会计主体"], D("0")) + num(r["期末余额"])
    for ent, cn in (("P", "黔岭酒业股份有限公司"), ("S1", "黔岭销售有限公司"),
                    ("S2", "黔岭酒类包装有限公司")):
        check(f"{cn} 应付职工薪酬", sal.get(cn, D("0")), -tb[ent].get("2211", D("0")))

    # 10 银行余额调节表
    print("\n【10】银行存款余额调节表")
    journal = rows("05_银行资料", "银行存款日记账_工商银行基本户_202512.csv")
    bank = rows("05_银行资料", "银行对账单_工商银行基本户_202512.csv")
    j_bal = num(journal[-1]["账面余额"])
    b_bal = num(bank[-1]["银行对账单余额"])
    unrecorded = [r for r in bank if r["内部标记"] == "银行已记、企业未记"]
    interest = sum((num(r["借方(收款)"]) for r in unrecorded), D("0"))
    charge = sum((num(r["贷方(付款)"]) for r in unrecorded), D("0"))
    in_transit = sum((num(r["贷方(付款)"]) for r in journal if r["内部标记"]), D("0"))
    reconciled = j_bal + interest - charge
    print(f"      企业日记账 {j_bal:,.2f} + 银行已收企业未收 {interest:,.2f} "
          f"− 银行已付企业未付 {charge:,.2f} = {reconciled:,.2f}")
    check("调节后余额（企业侧 A+B−C = 银行侧 E−F）",
          b_bal - in_transit, reconciled, f"在途未达 {in_transit:,.2f}")

    # 11 无负数资产
    print("\n【11】资产类科目无贷方余额")
    from coa import ACCOUNTS
    neg = []
    for ent in tb:
        for code, v in tb[ent].items():
            kind = ACCOUNTS.get(code, ("", "", ""))[1]
            if kind == "asset" and v < 0:
                neg.append((ent, code, v))
    if neg:
        for n in neg:
            print(f"  ✗ {n[0]}-{n[1]} = {n[2]:,.2f}")
    else:
        print("  ✓ 全部资产类科目期末余额为借方（无负数资产）")

    # 汇总
    fail = [r for r in RESULTS if r[3] == "✗"] + [("固定资产折旧重算", D(0), D(0), "✗") for _ in bad]
    print("\n" + "=" * 96)
    print(f"核对项 {len(RESULTS)} 项，通过 {len(RESULTS) - len(fail)} 项，未通过 {len(fail)} 项")
    print("=" * 96)


if __name__ == "__main__":
    main()
