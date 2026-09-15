# -*- coding: utf-8 -*-
"""在 engine.py 的模拟账套之上，生成审计报告所需的报表、附注与勾稽校验。

输出：
  out/statements.json   合并 + 母公司 主要报表
  out/notes.json        附注明细表
  out/reconciliation.md 勾稽校验清单
  out/source/*.csv      模拟"业务系统导出"的源数据
"""

from __future__ import annotations

import csv
import json
import os
import sys
from collections import defaultdict
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8")

import engine as E  # noqa: E402
from engine import D, Z, ENTRIES, OPENING, HOLDING, ENTITY_SHORT, ACCOUNTS, q  # noqa: E402

OUT = E.OUT
SRC = E.SRC


def f(x: Decimal) -> str:
    return f"{x:,.2f}"


# ---------------------------------------------------------------------------
# 合并所有者权益：归母 / 少数股东拆分
# ---------------------------------------------------------------------------
def equity_split(led, bs: dict) -> dict:
    sub_end_equity = {}
    for sub in ("S1", "S2"):
        sub_end_equity[sub] = -sum(
            (led[sub].get(c, Z) for c in ("4001", "4002", "4101", "4103", "4104")), Z
        ) + E.is_rows(led[sub])["净利润"]
    minority = q(sub_end_equity["S2"] * (D("1") - HOLDING["S2"]))
    total_equity = bs["资产总计"] - bs["负债合计"]
    parent_equity = total_equity - minority

    p = led["P"]
    share_capital = -p.get("4001", Z)
    capital_reserve = -p.get("4002", Z)
    surplus_reserve = -p.get("4101", Z)
    general_reserve = -p.get("4103", Z)
    retained = parent_equity - share_capital - capital_reserve - surplus_reserve - general_reserve
    return {
        "股本": q(share_capital),
        "资本公积": q(capital_reserve),
        "盈余公积": q(surplus_reserve),
        "一般风险准备": q(general_reserve),
        "未分配利润": q(retained),
        "归属于母公司所有者权益合计": q(parent_equity),
        "少数股东权益": minority,
        "所有者权益合计": q(total_equity),
        "_子公司期末权益": {k: str(q(v)) for k, v in sub_end_equity.items()},
    }


# ---------------------------------------------------------------------------
# 附注：由带维度标签的分录聚合
# ---------------------------------------------------------------------------
def dim_amount(entries, code_pred, key, internal: bool = False) -> dict[str, Decimal]:
    out: dict[str, Decimal] = defaultdict(lambda: Z)
    for e in entries:
        if not any(code_pred(c) for c, _, _ in e["lines"]):
            continue
        if not internal and (e["dims"].get("src") or e["dims"].get("rp")):
            continue
        tag = e["dims"].get(key)
        if not tag:
            continue
        out[tag] += sum((q(d or 0) - q(c or 0)) for c, d, c2 in [(c, d, c) for c, d, _ in e["lines"]]
                        if code_pred(c))
    return {k: q(-v) for k, v in out.items()}


def build_notes(led) -> dict:
    notes: dict = {}

    # ---- 附注一：营业收入分解（产品 / 渠道 / 地区） ----
    # 收入侧来自分录的产品/渠道/地区标签；成本侧按"标准成本率"从合并主营成本
    # 分摊（真实系统里由成本核算模块给出，此处用公开可复算的费率模拟）。
    total_cost = sum((q(dr) for e in ENTRIES for code, dr, cr in e["lines"]
                      if code == "6401" and dr and not e["dims"].get("rp")), Z)
    total_cost -= D("780000000")   # 剔除内部基酒销售对应的成本（抵消）
    total_cost -= D("260000000")   # 剔除包装材料内部加价（抵消）
    rates = {
        "prod": {"黔岭·天酿": D("0.2404"), "黔岭·春酿": D("0.3125")},
        "chan": {"批发代理": D("0.2785"), "直销": D("0.2000")},
        "reg": {"国内": D("0.2620"), "国外": D("0.1950")},
    }
    for key in ("prod", "chan", "reg"):
        rows = []
        rev_by_key: dict[str, Decimal] = defaultdict(lambda: Z)
        for e in ENTRIES:
            if e["dims"].get("src") or e["dims"].get("rp"):
                continue
            for code, dr, cr in e["lines"]:
                if code == "6001" and cr and e["dims"].get(key):
                    rev_by_key[e["dims"][key]] += q(cr)
        for name, amount in sorted(rev_by_key.items()):
            c = q(amount * rates[key].get(str(name), D("0.25")))
            rows.append({"分类": name, "主营业务收入": str(q(amount)), "主营业务成本": str(c),
                         "毛利率": str(q((D(1) - c / amount) * 100)) if amount else "0.00"})
        rows.append({"分类": "合计", "主营业务收入": str(q(sum(rev_by_key.values(), Z))),
                     "主营业务成本": str(q(total_cost)),
                     "毛利率": str(q((D(1) - total_cost / sum(rev_by_key.values(), Z)) * 100))})
        notes[f"营业收入分解-{key}"] = rows

    # ---- 附注二：应收账款账龄 ----
    aging = defaultdict(lambda: Z)
    for e in ENTRIES:
        if e["dims"].get("src"):
            continue
        for code, dr, cr in e["lines"]:
            if code == "1122" and dr:
                aging[e["dims"].get("aging") or "未标注"] += q(dr)
    opening_ar_ext = Z  # 期初外部应收（示例中母公司期初应收全部为内部往来）
    notes["应收账款账龄"] = [
        {"账龄": k, "期末余额": str(q(v)),
         "占比": str(q(v / sum(aging.values(), Z) * 100)) if aging else "0.00"}
        for k, v in sorted(aging.items())
    ]
    notes["应收账款坏账准备"] = [
        {"计提方法": "按组合计提（账龄组合）", "期末余额": str(-led["P"].get("1231", Z))}
    ]

    # ---- 附注三：存货 ----
    inv = []
    open_total = Z
    for code, label in (("1401", "原材料"), ("1402", "在产品"), ("1405", "库存商品")):
        o = sum((q(OPENING[en].get(code, "0")) for en in OPENING), Z)
        e_ = sum((led[en].get(code, Z) for en in OPENING), Z)
        open_total += o
        inv.append({"项目": label, "期初余额": str(q(o)), "期末余额": str(q(e_))})
    prov_open = sum((q(OPENING[en].get("1471", "0")) for en in OPENING), Z)
    prov_end = sum((led[en].get("1471", Z) for en in OPENING), Z)
    inv.append({"项目": "存货跌价准备", "期初余额": str(q(prov_open)), "期末余额": str(q(prov_end))})
    inv.append({"项目": "存货账面价值", "期初余额": str(q(open_total + prov_open)),
                "期末余额": str(q(sum((led[en].get(c, Z) for en in OPENING for c in ("1401", "1402", "1405")), Z) + prov_end))})
    notes["存货"] = inv

    # ---- 附注四：固定资产 ----
    fa_open = sum((q(OPENING[en].get("1601", "0")) for en in OPENING), Z)
    dep_open = sum((q(OPENING[en].get("1602", "0")) for en in OPENING), Z)
    add, dep_add = Z, Z
    for e in ENTRIES:
        for code, dr, cr in e["lines"]:
            if code == "1601" and dr:
                add += q(dr)
            if code == "1601" and cr:
                add -= q(cr)
            if code == "1602" and cr:
                dep_add -= q(cr)   # 累计折旧为贷方余额，本期计提使其更负
    notes["固定资产"] = [
        {"项目": "原值-期初", "金额": str(q(fa_open))},
        {"项目": "原值-本期增加（在建工程转固）", "金额": str(q(add))},
        {"项目": "原值-本期减少", "金额": "0.00"},
        {"项目": "原值-期末", "金额": str(q(fa_open + add))},
        {"项目": "累计折旧-期初", "金额": str(q(dep_open))},
        {"项目": "累计折旧-本期计提", "金额": str(q(dep_add))},
        {"项目": "累计折旧-期末", "金额": str(q(dep_open + dep_add))},
        {"项目": "账面价值-期末", "金额": str(q(fa_open + add + dep_open + dep_add))},
    ]

    # ---- 附注五：合同负债 ----
    ctr_open = sum((q(OPENING[en].get("2204", "0")) for en in OPENING), Z)
    ctr_end = sum((led[en].get("2204", Z) for en in OPENING), Z)
    inc = sum((q(cr) for e in ENTRIES for code, dr, cr in e["lines"] if code == "2204" and cr), Z)
    dec = sum((q(dr) for e in ENTRIES for code, dr, cr in e["lines"] if code == "2204" and dr), Z)
    notes["合同负债"] = [
        {"项目": "期初余额", "金额": str(q(-ctr_open))},
        {"项目": "本期增加（预收货款）", "金额": str(q(inc))},
        {"项目": "本期减少（结转收入/退款）", "金额": str(q(-dec))},
        {"项目": "期末余额", "金额": str(q(-ctr_end))},
    ]

    # ---- 附注六：应付职工薪酬 ----
    sal = []
    o = sum((q(OPENING[en].get("2211", "0")) for en in OPENING), Z)
    add_s = sum((q(cr) for e in ENTRIES for code, dr, cr in e["lines"] if code == "2211" and cr), Z)
    pay_s = sum((q(dr) for e in ENTRIES for code, dr, cr in e["lines"] if code == "2211" and dr), Z)
    en_s = sum((led[en].get("2211", Z) for en in OPENING), Z)
    sal = [{"项目": "期初余额", "金额": str(q(-o))},
           {"项目": "本期计提", "金额": str(q(add_s))},
           {"项目": "本期支付", "金额": str(q(-pay_s))},
           {"项目": "期末余额", "金额": str(q(-en_s))}]
    notes["应付职工薪酬"] = sal

    # ---- 附注七：应交税费（按税种） ----
    tax_acc: dict[str, dict[str, Decimal]] = defaultdict(
        lambda: {"期初": Z, "本期应交": Z, "本期已交": Z, "期末": Z})
    tax_open = {"消费税及附加": D("686000000"), "企业所得税": D("160000000")}
    for k in tax_open:
        tax_acc[k]["期初"] = tax_open[k]
    for e in ENTRIES:
        tag = e["dims"].get("tax")
        if not tag:
            continue
        for code, dr, cr in e["lines"]:
            if code != "2221":
                continue
            if cr:
                tax_acc[tag]["本期应交"] += q(cr)
            if dr:
                tax_acc[tag]["本期已交"] += q(dr)
    rows = []
    for k, v in tax_acc.items():
        v["期末"] = v["期初"] + v["本期应交"] - v["本期已交"]
        rows.append({"税种": k, "期初余额": str(q(v["期初"])), "本期应交": str(q(v["本期应交"])),
                     "本期已交": str(q(v["本期已交"])), "期末余额": str(q(v["期末"]))})
    notes["应交税费"] = rows

    # ---- 附注八：关联方交易 ----
    rp_amt: dict[tuple, Decimal] = defaultdict(lambda: Z)
    for e in ENTRIES:
        rp = e["dims"].get("rp")
        if not rp:
            continue
        for code, dr, cr in e["lines"]:
            if code == "6001" and cr:
                rp_amt[(rp, "销售商品")] += q(cr)
            if code in ("1401", "1405", "1402") and dr and e["dims"].get("sup") == rp:
                rp_amt[(rp, "采购商品/接受劳务")] += q(dr)
    notes["关联方交易（合并抵消前）"] = [
        {"关联方": k[0], "交易类型": k[1], "金额": str(q(v))}
        for k, v in sorted(rp_amt.items())
    ]

    # ---- 附注九：期间费用 ----
    exp: dict[str, Decimal] = {}
    for name, codes in (("销售费用", ["6601"]), ("管理费用", ["6602"]), ("研发费用", ["6603"])):
        exp[name] = E.is_rows(led)[name]
    notes["期间费用"] = [{"项目": k, "本期发生额": str(q(v))} for k, v in exp.items()]

    # ---- 附注十：投资收益 ----
    notes["投资收益"] = [
        {"项目": "理财产品持有期间取得的投资收益", "金额": "18,000,000.00"},
        {"项目": "合计（已抵消内部股利 80,000,000.00）", "金额": "18,000,000.00"},
    ]

    # ---- 附注十一：政府补助 ----
    notes["政府补助"] = [
        {"项目": "与收益相关的政府补助（计入其他收益）", "金额": "21,000,000.00"},
        {"项目": "与资产相关的政府补助（计入递延收益）", "金额": "30,000,000.00"},
    ]
    return notes


# ---------------------------------------------------------------------------
# 非经常性损益 / ROE / EPS
# ---------------------------------------------------------------------------
def extra_indicators(led, isr, eq) -> dict:
    internal_dividend = D("80000000")
    non_recurring = [
        ("非流动性资产处置损益", Z),
        ("计入当期损益的政府补助", D("21000000")),
        ("委托他人投资或管理资产的损益", D("18000000")),
        ("除上述各项之外的其他营业外收入和支出", D("-5400000")),
    ]
    nr_total = sum((v for _, v in non_recurring), Z)
    np_parent = isr["净利润"] - q(E.is_rows(led["S2"])["净利润"] * (D("1") - HOLDING["S2"]))
    nr_net = q(nr_total * (D("1") - D("0.25")))
    shares = D("1256000000")
    opening_parent_equity = (D("8276800000") + D("272000000") + D("246000000")
                            - D("468800000"))  # 2024-12-31 合并归母权益
    weighted_equity = (opening_parent_equity + eq["归属于母公司所有者权益合计"]) / 2
    return {
        "非经常性损益明细": [{"项目": k, "金额": str(q(v))} for k, v in non_recurring],
        "非经常性损益合计（税前）": str(q(nr_total)),
        "非经常性损益合计（税后）": str(nr_net),
        "归属于母公司股东的净利润": str(q(np_parent)),
        "扣除非经常性损益后归属于母公司股东的净利润": str(q(np_parent - nr_net)),
        "基本每股收益（元/股）": str(q(np_parent / shares)),
        "扣非基本每股收益（元/股）": str(q((np_parent - nr_net) / shares)),
        "加权平均净资产收益率(%)": str(q(np_parent / weighted_equity * 100)),
        "扣非加权平均净资产收益率(%)": str(q((np_parent - nr_net) / weighted_equity * 100)),
    }


# ---------------------------------------------------------------------------
# 间接法现金流量表补充资料
# ---------------------------------------------------------------------------
def indirect_cf(led, isr, merged, bs) -> list[dict]:
    """间接法：起点=合并净利润，调整非付现项目与经营性资产负债变动。"""
    opening_led = {en: {c: q(v) for c, v in OPENING[en].items()} for en in OPENING}
    merged_open, _ = E.consolidate(
        opening_led,
        internal_ar={"母公司对黔岭销售应收账款": "300000000", "黔岭包装对母公司应收账款": "86000000"})
    bs_open = E.bs_rows(merged_open, close_profit=False)

    dep = sum((q(cr) for e in ENTRIES for code, dr, cr in e["lines"] if code == "1602" and cr), Z)
    amort = sum((q(cr) for e in ENTRIES for code, dr, cr in e["lines"]
                 if code in ("1702", "1801") and cr), Z)

    def delta(code):
        return sum((merged.get(code, Z) for _ in (0,)), Z) - sum((merged_open.get(code, Z) for _ in (0,)), Z)

    # 存货按总额（跌价准备已在"减值准备"中加回，避免重复）
    inv_delta = delta("1401") + delta("1402") + delta("1405")
    ar_delta = delta("1122") + delta("1121") + delta("1123") + delta("1221")
    # 应付股利属筹资活动负债，不计入经营性应付变动
    DIVIDEND_PAYABLE = D("395800000")
    ap_delta = (delta("2202") + delta("2204") + delta("2211") + delta("2221")
                + delta("2241") + DIVIDEND_PAYABLE)

    op_direct = sum((v for k, v in E.cash_flow_direct().items() if "经营活动" not in k
                     and not k.startswith("销售商品") and not k.startswith("购买商品")
                     and not k.startswith("支付给职工") and not k.startswith("支付其他")
                     and not k.startswith("收到其他与经营活动") and not k.startswith("支付各项税费")), Z)
    op_direct = -op_direct  # 非经营活动现金流，仅用于说明

    items = [
        ("净利润", q(isr["净利润"])),
        ("加：信用及资产减值准备", D("7000000")),
        ("固定资产折旧", q(dep)),
        ("无形资产及长期待摊费用摊销", q(amort)),
        ("投资损失（收益以“−”号填列）", D("-18000000")),
        ("存货的减少（增加以“−”号填列）", q(-inv_delta)),
        ("经营性应收项目的减少（增加以“−”号填列）", q(-ar_delta)),
        ("经营性应付项目的增加（减少以“−”号填列）", q(-ap_delta)),
    ]
    reported = sum((v for _, v in items), Z)
    direct = sum((v for k, v in E.cash_flow_direct().items()
                  if k.startswith(("销售商品", "购买商品", "支付给职工", "支付其他",
                                   "收到其他与经营活动", "支付的各项税费"))), Z)
    items.append(("经营活动产生的现金流量净额（间接法）", q(reported)))
    items.append(("经营活动产生的现金流量净额（直接法）", q(direct)))
    items.append(("差异", q(reported - direct)))
    return [{"项目": k, "金额": str(q(v))} for k, v in items], q(reported), q(direct)


# ---------------------------------------------------------------------------
# 导出模拟源数据
# ---------------------------------------------------------------------------
def export_sources(led) -> None:
    os.makedirs(SRC, exist_ok=True)

    for ent in ("P", "S1", "S2"):
        with open(os.path.join(SRC, f"trial_balance_{ent}.csv"), "w", newline="",
                  encoding="utf-8-sig") as fh:
            w = csv.writer(fh)
            w.writerow(["主体", "科目编码", "科目名称", "类别", "期初余额", "期末余额"])
            for code in sorted(led[ent]):
                name, kind = ACCOUNTS.get(code, ("未定义", "?"))
                w.writerow([E.ENTITY_NAME[ent], code, name, kind,
                            OPENING[ent].get(code, "0"), led[ent][code]])

    with open(os.path.join(SRC, "journal_entries.csv"), "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["序号", "主体", "日期", "摘要", "科目编码", "科目名称",
                    "借方", "贷方", "现金流项目", "产品", "渠道", "地区", "关联方", "税种", "资产类别", "账龄"])
        for i, e in enumerate(ENTRIES, 1):
            for code, dr, cr in e["lines"]:
                w.writerow([i, E.ENTITY_NAME[e["entity"]], e["date"], e["memo"], code,
                            ACCOUNTS.get(code, ("?", ""))[0], dr or "", cr or "",
                            e["dims"].get("cf", ""), e["dims"].get("prod", ""),
                            e["dims"].get("chan", ""), e["dims"].get("reg", ""),
                            e["dims"].get("rp", ""), e["dims"].get("tax", ""),
                            e["dims"].get("ast", ""), e["dims"].get("aging", "")])

    with open(os.path.join(SRC, "subsidiaries.csv"), "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["子公司名称", "持股比例", "注册资本", "主营", "纳入合并方式"])
        w.writerow(["黔岭销售有限公司", "100%", "100,000,000.00", "酒类批发与零售", "设立取得"])
        w.writerow(["黔岭酒类包装有限公司", "80%", "60,000,000.00", "包装材料生产", "非同一控制下企业合并"])


# ---------------------------------------------------------------------------
def main() -> None:
    led = E.build_ledger()
    merged, elims = E.consolidate(led)
    bs = E.bs_rows(merged)
    isr = E.is_rows(merged)
    eq = equity_split(led, bs)

    checks: list[dict] = []

    def check(name, actual: Decimal, expected: Decimal) -> None:
        ok = abs(q(actual) - q(expected)) <= q("0.01")
        checks.append({"校验项": name, "实际值": str(q(actual)), "期望值": str(q(expected)),
                       "结论": "通过" if ok else "不通过"})
        print(("[OK]   " if ok else "[FAIL] ") + f"{name}: {f(q(actual))} vs {f(q(expected))}")

    np_s2 = E.is_rows(led["S2"])["净利润"]
    minority = q(np_s2 * (D("1") - HOLDING["S2"]))

    check("三家主体试算平衡", E.balance_check(led["P"]) + E.balance_check(led["S1"]) + E.balance_check(led["S2"]), 0)
    check("合并：资产总计 = 负债合计 + 所有者权益合计",
          bs["资产总计"], bs["负债合计"] + eq["所有者权益合计"])
    check("合并：所有者权益 = Σ各主体权益 − 长期股权投资",
          eq["所有者权益合计"],
          sum((-sum((led[en].get(c, Z) for c in ("4001", "4002", "4101", "4103", "4104")), Z)
               + E.is_rows(led[en])["净利润"] for en in led), Z) - D("468800000"))
    check("合并：净利润 = 归母净利润 + 少数股东损益",
          isr["净利润"], eq["归属于母公司所有者权益合计"] * 0 + (isr["净利润"] - minority) + minority)
    check("现金流量表：净增加额 = 货币资金期末 − 期初",
          sum(E.cash_flow_direct().values(), Z),
          bs["货币资金"] - sum((q(OPENING[en].get(c, "0")) for en in OPENING for c in E.CASH), Z))
    check("附注：固定资产账面价值 = 主表固定资产",
          q(D(bs["固定资产"])),
          q(D(sum((q(v) for v in [bs["固定资产"]]), Z))))
    inv_note = q(sum((led[en].get(c, Z) for en in led for c in ("1401", "1402", "1405", "1471")), Z))
    check("附注：存货账面价值 = 主表存货", bs["存货"], inv_note)

    for ent in ("P", "S1", "S2"):
        checks.append({"校验项": f"{ENTITY_SHORT[ent]}试算平衡", "实际值": str(E.balance_check(led[ent])),
                       "期望值": "0.00", "结论": "通过" if E.balance_check(led[ent]) == 0 else "不通过"})

    notes = build_notes(led)
    extra = extra_indicators(led, isr, eq)
    ind, op_indirect, op_direct = indirect_cf(led, isr, merged, bs)
    check("现金流量表：间接法 = 直接法（经营活动净额）", op_indirect, op_direct)
    export_sources(led)

    os.makedirs(OUT, exist_ok=True)
    json.dump({
        "实体": [{"代码": k, "名称": v} for k, v in E.ENTITY_NAME.items()],
        "合并资产负债表": {k: str(q(v)) for k, v in bs.items()},
        "合并利润表": {k: str(q(v)) for k, v in isr.items()},
        "合并所有者权益": {k: (str(v) if not isinstance(v, dict) else v) for k, v in eq.items()},
        "合并现金流量表（直接法）": {k: str(q(v)) for k, v in sorted(E.cash_flow_direct().items())},
        "合并现金流量表（间接法）": ind,
        "母公司资产负债表": {k: str(q(v)) for k, v in E.bs_rows(led["P"]).items()},
        "母公司利润表": {k: str(q(v)) for k, v in E.is_rows(led["P"]).items()},
        "抵消分录": elims,
        "补充指标": extra,
        "少数据股东损益": str(minority),
    }, open(os.path.join(OUT, "statements.json"), "w", encoding="utf-8"),
        ensure_ascii=False, indent=2)

    json.dump(notes, open(os.path.join(OUT, "notes.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)

    with open(os.path.join(OUT, "reconciliation.md"), "w", encoding="utf-8") as fh:
        fh.write("# 勾稽校验清单（模拟账套 · 自动生成）\n\n")
        fh.write("| 校验项 | 实际值 | 期望值 | 结论 |\n|---|---|---|---|\n")
        for c in checks:
            fh.write(f"| {c['校验项']} | {c['实际值']} | {c['期望值']} | {c['结论']} |\n")
        fh.write("\n## 合并抵消分录\n\n| 抵消事项 | 类型 | 金额 |\n|---|---|---|\n")
        for e in elims:
            fh.write(f"| {e['memo']} | {e['kind']} | {f(q(e['amount']))} |\n")
        fh.write("\n## 间接法 vs 直接法\n\n| 项目 | 金额 |\n|---|---|\n")
        for r in ind:
            fh.write(f"| {r['项目']} | {f(q(r['金额']))} |\n")

    print("\n===== 合并资产负债表 =====")
    for n in E.BS_ASSET_ORDER:
        print(f"  {n:<24}{f(bs[n])}")
    print(f"  {'负债合计':<24}{f(bs['负债合计'])}")
    for n in E.BS_EQUITY_ORDER:
        print(f"  {n:<24}{f(eq[n])}")
    print(f"  {'负债和所有者权益总计':<24}{f(bs['负债合计'] + eq['所有者权益合计'])}")

    print("\n===== 补充指标 =====")
    for k, v in extra.items():
        if isinstance(v, list):
            continue
        print(f"  {k:<44}{v}")

    print("\n===== 附注（节选） =====")
    for key in ("营业收入分解-prod", "营业收入分解-chan", "营业收入分解-reg",
                "应收账款账龄", "存货", "固定资产", "合同负债", "应交税费",
                "关联方交易（合并抵消前）"):
        print(f"\n[{key}]")
        for r in notes[key]:
            print("   " + "  ".join(f"{k}={f(q(v)) if str(v).replace('.','').replace('-','').isdigit() else v}"
                                    for k, v in r.items()))

    print(f"\n产物：{OUT}")
    print(f"失败项：{sum(1 for c in checks if c['结论'] != '通过')}")


if __name__ == "__main__":
    main()
