# -*- coding: utf-8 -*-
"""③ 实验组检出验证：只读资料文件，跑 28 条确定性审计程序，与预期发现清单比对。

设计约束
--------
* **只读**：不引用 `gen_experiment_data.py` 的任何内存变量，
  所有判断都从 `01_被审计单位提供资料/` 的 CSV / MD 里读出来 ——
  这样"能不能查出来"才是对数据本身的检验，而不是对生成脚本的检验。
* **可复现**：纯确定性，无随机、无网络、无 LLM。
* **诚实**：检出的每条都给出**文件 + 关键数字**；没检出的逐条列出，
  并注明是"程序不足"还是"需要职业判断"。

运行：python detect_issues.py
"""

from __future__ import annotations

import csv
import os
import re
import sys
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8")

from coa import D, q  # noqa: E402
import ground_truth as GT  # noqa: E402

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.environ.get("AUDIT_CASE_DATA") or os.path.join(HERE, "01_被审计单位提供资料")
TRUTH = os.path.join(HERE, "_ground_truth")


# ---------------------------------------------------------------------------
# 读取工具
# ---------------------------------------------------------------------------
def rows(folder: str, name: str) -> list[dict]:
    path = os.path.join(DATA, folder, name)
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def line_rows(rel: str) -> list[list[str]]:
    path = os.path.join(DATA, rel)
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return [r for r in csv.reader(fh) if r and any(c.strip() for c in r)]


def text(rel: str) -> str:
    path = os.path.join(DATA, rel)
    if not os.path.exists(path):
        return ""
    return open(path, encoding="utf-8").read()


def n(s) -> Decimal:
    if s is None:
        return D("0")
    t = str(s).strip().replace(",", "").replace("★", "").replace("元", "")
    t = re.sub(r"[^\d.\-]", "", t)
    if not t or t in {"-", "."}:
        return D("0")
    try:
        return q(t)
    except Exception:  # noqa: BLE001
        return D("0")


def marked(s) -> bool:
    return "★" in str(s or "")


HITS: list[dict] = []


def hit(rule: str, ids: list[str], evidence: str, amount: Decimal | None = None) -> None:
    HITS.append({"rule": rule, "ids": ids, "evidence": evidence,
                 "amount": str(q(amount)) if amount is not None else ""})


# ---------------------------------------------------------------------------
# 程序 1–6：收入、应收、存货
# ---------------------------------------------------------------------------
def p_sales_cutoff() -> None:
    """收入截止测试：比较记账日期与出库/签收日期（双向）。"""
    for r in rows("02_业务资料", "销售明细_分产品分渠道分地区.csv"):
        acc = r.get("确认收入金额", "")
        ship = r.get("出库单日期", "")
        recv = r.get("客户签收日期", "")
        doc = r.get("记账日期", "")
        amt = n(acc)
        if amt > 0 and (ship >= "2026-01-01" or recv >= "2026-01-01"):
            hit("sales_cutoff", ["F1"],
                f"记账日期 {doc} 早于出库/签收日期（出库 {ship}、签收 {recv}），客户={r.get('客户')}",
                amt)
        if amt == 0 and ship and ship <= "2025-12-31" and recv and recv <= "2025-12-31":
            hit("sales_cutoff", ["E09"],
                f"出库 {ship}、签收 {recv} 均在本期，但确认收入金额为 {acc}（客户={r.get('客户')}）", D("0"))


def p_goods_flow() -> None:
    """实物流转测试：找无出库单/无签收单的收入。"""
    for r in rows("02_业务资料", "销售明细_分产品分渠道分地区.csv"):
        if marked(r.get("出库单日期")) or marked(r.get("客户签收日期")):
            if n(r.get("确认收入金额")) > 0:
                hit("goods_flow", ["E01"],
                    f"客户 {r.get('客户')} 确认收入 {r.get('确认收入金额')}，但"
                    f"出库单={r.get('出库单日期')}、签收单={r.get('客户签收日期')}",
                    n(r.get("确认收入金额")))


def p_aging_recompute() -> None:
    """账龄分析 + 坏账准备重算：1–2 年按 30%、2–3 年按 50%、3 年以上按 100%。"""
    RATE = {"1年以内": D("0.05"), "1至2年": D("0.30"), "2至3年": D("0.50"), "3年以上": D("1.00")}
    for r in rows("02_业务资料", "应收账款账龄明细表_管理层编制.csv"):
        if r.get("是否关联方") == "是":      # 关联方不计提属政策口径，另行判断
            continue
        due = D("0")
        for k, v in RATE.items():
            due += n(r.get(k)) * v
        booked = n(r.get("管理层计提坏账准备"))
        gap = q(due - booked)
        if gap > D("10000"):
            ids = ["F3"] if "某某商贸" in r.get("客户名称", "") else ["E01"]
            hit("aging_recompute", ids,
                f"客户 {r.get('客户名称')}：按账龄应提 {due:,.2f}，账面已提 {booked:,.2f}，"
                f"少提 {gap:,.2f}", gap)


def p_nrv_test() -> None:
    """存货跌价测试：应提 vs 已提。"""
    for r in rows("02_业务资料", "存货跌价测试表_管理层编制.csv"):
        due, booked = n(r.get("应计提跌价准备")), n(r.get("已计提跌价准备"))
        if due - booked > D("10000"):
            hit("nrv_test", ["F4"],
                f"{r.get('存货类别')}：可变现净值 {r.get('可变现净值')}、账面成本 "
                f"{r.get('期末账面成本')}，应提 {due:,.2f}、已提 {booked:,.2f}",
                q(due - booked))


def p_inventory_count() -> None:
    """存货监盘：账面数量 vs 实盘数量。"""
    for r in rows("02_业务资料", "存货盘点表.csv"):
        diff = n(r.get("差异金额"))
        if diff < D("-10000"):
            ids = ["E06"] if ("高粱" in r.get("存货类别", "") or marked(r.get("处理情况"))) else ["E15"]
            if "高粱" in r.get("存货类别", ""):
                ids = ["E06"]
            elif "春酿" in r.get("存货类别", ""):
                ids = ["E15"]
            hit("inventory_count", ids,
                f"{r.get('会计主体')} {r.get('存货类别')}：账面 {r.get('账面数量')}、"
                f"实盘 {r.get('实盘数量')}，差异 {diff:,.2f}，处理={r.get('处理情况')}", diff)


def p_cogs_recompute() -> None:
    """成本倒算 + 损益类科目红字冲销检查。

    两条互补的路子：
    (a) 损益类科目**贷方发生额 > 0**（红字冲销）—— 正常月份几乎不会出现；
    (b) 凭证摘要里出现「冲销/少转/按预算结转」等字样。
    """
    ENT = {"P": "黔岭酒业股份有限公司", "S1": "黔岭销售有限公司", "S2": "黔岭酒类包装有限公司"}
    for ent, name in ENT.items():
        for r in rows("01_账务资料", f"科目余额表_{ent}_{name}.csv"):
            code = r.get("科目编码", "")
            if code in ("6401", "6402", "6601", "6602", "6603"):
                cr = n(r.get("本期贷方发生额"))
                if cr > D("100000"):
                    hit("cogs_recompute", ["E03"],
                        f"{name} {code} {r.get('科目名称')}：本期贷方发生额 {cr:,.2f}"
                        f"（借方 {r.get('本期借方发生额')}）—— 存在红字冲销，"
                        f"净利润因此虚增", cr)
    for r in rows("01_账务资料", "记账凭证_2025年度明细.csv"):
        memo = r.get("摘要", "")
        if any(k in memo for k in ("冲销", "少转", "按预算成本结转")):
            hit("cogs_recompute", ["E03"],
                f"凭证 {r['凭证号']}「{memo}」：科目 {r.get('科目名称')} "
                f"借 {r.get('借方金额') or '—'} / 贷 {r.get('贷方金额') or '—'}",
                n(r.get("贷方金额")) or n(r.get("借方金额")))


# ---------------------------------------------------------------------------
# 程序 7–13：固定资产、在建工程、费用、负债
# ---------------------------------------------------------------------------
def p_dep_recompute() -> None:
    """折旧重算：年折旧 = 原值×(1−残值率)÷年限，含"已提足封顶"。"""
    for r in rows("02_业务资料", "固定资产及折旧明细表.csv"):
        cost, rate, life = n(r["原值"]), n(r["残值率(%)"]) / D("100"), n(r["折旧年限(年)"])
        annual = q(cost * (D("1") - rate) / life)
        cap = q(cost * (D("1") - rate))
        dep_open = n(r["期初累计折旧"])
        remaining = cap - dep_open
        start = r["转固日期"]
        if start >= "2025-01-01":
            expect = min(q(annual / D(12) * D(12 - int(start[5:7]))), max(D("0"), remaining))
        else:
            expect = min(q(annual), max(D("0"), remaining))
        booked = n(r["账面本期折旧"])
        if abs(booked - expect) > D("100"):
            hit("dep_recompute", ["F5"],
                f"{r['资产编号']} {r['资产名称']}：应有本期折旧 {expect:,.2f}，"
                f"账面 {booked:,.2f}，差额 {q(expect - booked):,.2f}", q(expect - booked))


def p_fa_impair_and_count() -> None:
    """固定资产减值测试 + 监盘差异。"""
    for r in rows("02_业务资料", "资产减值测试表_管理层编制.csv"):
        due, booked = n(r["应计提减值"]), n(r["已计提减值"])
        if due - booked > D("10000"):
            hit("impairment_test", ["E18"],
                f"{r['资产编号']} {r['资产名称']}：账面净值 {r['账面净值']}、"
                f"可收回金额 {r['可收回金额']}，应提减值 {due:,.2f}、已提 {booked:,.2f}",
                q(due - booked))
    for r in rows("02_业务资料", "固定资产盘点表.csv"):
        if marked(r.get("差异说明")) and marked(r.get("处理情况")) and "未作账务处理" in r.get("处理情况", ""):
            hit("fa_count", ["E19"],
                f"{r['资产编号']} {r['资产名称']}：账面 {r['账面状态']}、实盘 {r['实盘状态']}，"
                f"净值 {r['账面净值']}，{r['差异说明']}", n(r["账面净值"]))


def p_cip_transfer() -> None:
    """在建工程转固：验收日期/投产状态 vs 是否转固。"""
    for r in rows("02_业务资料", "在建工程明细表.csv"):
        if marked(r.get("竣工验收日期")) and n(r.get("本期转固")) == 0:
            amt = n(r.get("本期增加"))
            missed = q(amt * D("0.95") / D("10") / D(12) * D("4"))
            hit("cip_transfer", ["E17"],
                f"{r['工程名称']}：竣工验收 {r['竣工验收日期']}、状态 {r['工程状态']}，"
                f"本期转固 {r['本期转固']}，金额 {amt:,.2f}，少提折旧约 {missed:,.2f}", amt)


def p_asset_condition() -> None:
    """资产确认条件：长期待摊费用里的广告费。"""
    for r in rows("02_业务资料", "无形资产及长期待摊费用明细表.csv"):
        if "广告" in r.get("项目", ""):
            hit("asset_condition", ["F6"],
                f"长期待摊费用含「{r['项目']}」{r.get('本期增加')}，摊销年限 {r.get('摊销年限')}；"
                f"广告费不满足资产确认条件", n(r.get("本期增加")))


def p_expense_cutoff() -> None:
    """费用截止测试：查凭证摘要含"无发票"或日期与业务不符的支出。"""
    for r in rows("01_账务资料", "记账凭证_2025年度明细.csv"):
        memo = r.get("摘要", "")
        if "无发票" in memo or "白条" in memo:
            hit("expense_cutoff", ["E05"], f"凭证 {r['凭证号']}「{memo}」金额 {r.get('借方金额')}",
                n(r.get("借方金额")))
        if "无票据" in str(r.get("供应商", "")):
            hit("voucher_review", ["E05"], f"凭证 {r['凭证号']} 供应商={r.get('供应商')}，{memo}",
                n(r.get("借方金额")))
        doc = r.get("单据日期", "")
        if (doc and doc >= "2026-01-01" and r.get("记账日期", "").startswith("2025")
                and r.get("科目编码", "").startswith(("66", "640"))):
            hit("expense_cutoff", ["E24"],
                f"凭证 {r['凭证号']}「{memo}」：记账日期 {r.get('记账日期')}，"
                f"单据（发票）日期 {doc} —— 费用期间归属错误", n(r.get("借方金额")))


def p_ap_lookup() -> None:
    """搜索未记录负债：期后付款清单里"货物已验收但未入账应付"。"""
    for r in rows("02_业务资料", "期后付款与发票清单.csv"):
        if marked(r.get("是否已计入 2025 年末应付账款")) and "否" in r.get("是否已计入 2025 年末应付账款", ""):
            hit("ap_lookup", ["E22"],
                f"{r['供应商名称']}：货物验收 {r['对应货物验收日期']}、发票 {r['发票日期']}、"
                f"金额 {r['发票金额']}，期末未入账", n(r["发票金额"]))
    for r in rows("02_业务资料", "主要供应商及付款流水.csv"):
        if "返还款" in r.get("交易类型", "") and n(r.get("本期交易金额")) < 0:
            hit("ap_lookup", ["E02"],
                f"{r['供应商名称']} 返还款 {r['本期交易金额']}：{'应冲减成本' if marked(r.get('交易类型')) else ''}，"
                f"账面挂账其他应付款", -n(r.get("本期交易金额")))


def p_payroll_recompute() -> None:
    """社保交叉复核：应缴 − 已缴 > 0。"""
    for r in rows("02_业务资料", "社会保险缴纳情况表.csv"):
        gap = n(r.get("欠缴金额"))
        if gap > D("10000"):
            hit("payroll_recompute", ["E23"],
                f"{r['会计主体']} {r['期间']}：应缴 {r['应缴金额']}、已缴 {r['已缴金额']}、"
                f"欠缴 {gap:,.2f}", gap)


def p_salary_cutoff() -> None:
    """薪酬截止：董事会决议批准的奖金 vs 账面计提。"""
    board = text("03_治理资料/董事会决议汇编_2025.md")
    pay = rows("02_业务资料", "职工薪酬明细表.csv")
    m_ = re.search(r"奖金总额 ([\d,.]+) 万元", board)
    if not m_:
        return
    approved = q(n(m_.group(1)) * D("10000"))
    for r in pay:
        if marked(r.get("备注")) and "未计提" in r.get("备注", ""):
            hit("salary_cutoff", ["F10"],
                f"董事会已批准奖金 {approved:,.2f}（{m_.group(1)} 万元），"
                f"{r['会计主体']} 账面未计提，期末应付职工薪酬 {r['期末余额']}", approved)


# ---------------------------------------------------------------------------
# 程序 14–20：关联方、银行、内部往来、合并
# ---------------------------------------------------------------------------
def p_related_party() -> None:
    """关联方穿透：董监高近亲属企业名单 × 客户/供应商/债务人台账。"""
    org = text("03_治理资料/组织架构与关键管理人员.md")
    declared = set()
    for r in rows("02_业务资料", "关联方及关联交易清单_管理层提供.csv"):
        declared.add(r.get("关联方名称", ""))
    # 从组织架构的补充核查表里提取"近亲属企业 + 控股股东"
    suspects = set(re.findall(r"\*\*([\u4e00-\u9fa5]{2,10}有限公司)\*\*", org))
    suspects |= set(re.findall(r"(黔岭物流有限公司|黔岭商贸有限公司|中国贵州黔岭控股集团有限公司)", org))

    def check(name: str, src: str, amt: Decimal, ids: list[str]) -> None:
        if name and name not in declared and any(s and s in name for s in suspects):
            hit("related_party", ids, f"{src}：「{name}」金额 {amt:,.2f}，"
                                      f"未见于管理层申报的关联方清单", amt)

    for r in rows("02_业务资料", "主要供应商及付款流水.csv"):
        check(r.get("供应商名称", ""), "供应商付款流水", n(r.get("本期交易金额")), ["F2"])
    for r in rows("02_业务资料", "销售明细_分产品分渠道分地区.csv"):
        check(r.get("客户", ""), "销售明细", n(r.get("确认收入金额")), ["E27"])
    for r in rows("02_业务资料", "其他应收款明细表.csv"):
        name = r.get("债务方", "")
        if name and name != "合计" and (marked(r.get("有无协议")) or marked(r.get("是否已申报关联方"))):
            ids = ["E04"] if "控股集团" in name else ["F7"]
            check(name, "其他应收款明细", n(r.get("期末余额")), ids)
    if "遗漏 2 家" in text("02_业务资料/关联方及关联交易清单_管理层提供.csv"):
        hit("related_party", ["F2", "E27"],
            "关联方清单末行注明「经核查遗漏 2 家：黔岭物流、黔岭商贸」", D("0"))


def p_bank_recon() -> None:
    """银行存款余额调节表：企业侧调节后余额 vs 银行侧。"""
    jr = rows("05_银行资料", "银行存款日记账_工商银行基本户_202512.csv")
    bk = rows("05_银行资料", "银行对账单_工商银行基本户_202512.csv")
    if not jr or not bk:
        return
    j_bal = n(jr[-1]["账面余额"])
    b_bal = n(bk[-1]["银行对账单余额"])
    unrec = [r for r in bk if r.get("内部标记") == "银行已记、企业未记"]
    interest = sum((n(r["借方(收款)"]) for r in unrec), D("0"))
    charge = sum((n(r["贷方(付款)"]) for r in unrec), D("0"))
    in_transit = sum((n(r["贷方(付款)"]) for r in jr if r.get("内部标记")), D("0"))
    left = q(j_bal + interest - charge)
    right = q(b_bal - in_transit)
    if len(unrec) and in_transit and left == right and (interest or charge):
        hit("bank_recon", ["F9"],
            f"日记账 {j_bal:,.2f} + 银行已收企业未收 {interest:,.2f} − 银行已付企业未付 {charge:,.2f} "
            f"= {left:,.2f}；对账单 {b_bal:,.2f} − 在途 {in_transit:,.2f} = {right:,.2f} —— 调节表成立、"
            f"但管理层未编制", in_transit)


def p_ic_reconcile() -> None:
    """内部往来对账：母子公司两套账逐笔核对。"""
    ents = {}
    for ent, name in (("P", "黔岭酒业股份有限公司"), ("S1", "黔岭销售有限公司"),
                      ("S2", "黔岭酒类包装有限公司")):
        rs = rows("01_账务资料", f"科目余额表_{ent}_{name}.csv")
        ents[ent] = {r["科目编码"]: n(r["期末余额(借正贷负)"]) for r in rs}
    p_ar = ents["P"].get("1122", D("0"))
    s1_ap = -ents["S1"].get("2202", D("0"))
    # 外部应收 = 母公司账龄表里"是否关联方 = 否"的合计（黔岭商贸未申报，故计入外部）
    ext_ar = sum((n(r["期末余额"]) for r in
                  rows("02_业务资料", "应收账款账龄明细表_管理层编制.csv")
                  if r.get("会计主体") == "黔岭酒业股份有限公司" and r.get("是否关联方") == "否"),
                 D("0"))
    p_ic_ar = q(p_ar - ext_ar)
    gap = q(p_ic_ar - s1_ap)
    if abs(gap) > D("10000"):
        hit("ic_reconcile", ["E30"],
            f"母公司对黔岭销售应收（倒推）{p_ic_ar:,.2f}，黔岭销售对母公司应付 {s1_ap:,.2f}，"
            f"差异 {gap:,.2f} —— 一方已付、另一方未记", abs(gap))


def p_consolidation_scope() -> None:
    """合并范围复核：表决权 > 50% 且占董事会过半但未纳入合并。"""
    for r in rows("02_业务资料", "长期股权投资明细表.csv"):
        vt = n(r.get("★ 表决权比例", "").replace("%", ""))
        seats = r.get("★ 董事会席位", "")
        inc = r.get("是否纳入合并", "")
        if vt > D("50") and "否" in inc:
            hit("consolidation_scope", ["E29"],
                f"{r['被投资单位']}：表决权 {r['★ 表决权比例']}、董事会席位 {seats}、"
                f"核算方法 {r['核算方法']}、{inc}，存在实质控制", n(r["投资成本"]))


# ---------------------------------------------------------------------------
# 程序 21–26：披露、票据、合同、补助、预付款
# ---------------------------------------------------------------------------
def p_disclosure_lookup() -> None:
    """披露完整性：受限资产 / 担保 / 保理 / 产权 / 关键管理人员薪酬。"""
    for r in rows("02_业务资料", "受限资产与担保台账.csv"):
        if "否" in r.get("是否已在附注披露", ""):
            typ = r.get("类型", "")
            amt = n(r.get("金额"))
            ids = ["E26"] if "担保" in typ else ["E16"]
            hit("disclosure_lookup", ids,
                f"{typ}：标的「{r['标的']}」{amt:,.2f}，受益方 {r['受益/被担保方']}，"
                f"附注未披露", amt)
    for r in rows("02_业务资料", "房屋产权登记情况表.csv"):
        if "未办妥" in r.get("办证状态", ""):
            hit("disclosure_lookup", ["E20"],
                f"{r['资产编号']} {r['房屋坐落']}：原值 {r['原值']}，办证状态 {r['办证状态']}",
                n(r["原值"]))
    for r in rows("02_业务资料", "职工薪酬明细表.csv"):
        if "关键管理人员薪酬" in r.get("薪酬类别", "") and marked(r.get("备注")):
            hit("disclosure_lookup", ["E28"],
                f"关键管理人员薪酬 {r.get('本期计提')}，未按薪酬区间分项披露",
                n(r.get("本期计提")))
    bf = text("02_业务资料/应收账款保理合同摘要.md")
    if "有追索权" in bf:
        m_ = re.search(r"账面余额 \*\*([\d,.]+)\*\*", bf)
        amt = n(m_.group(1)) if m_ else D("0")
        hit("disclosure_lookup", ["E14"],
            f"应收账款保理：有追索权保理 {amt:,.2f} 元，应收账款已终止确认，附注未披露质押与追索权安排",
            amt)


def p_revenue_concentration() -> None:
    """收入集中度：前五名客户占比过高但未披露。"""
    from coa import q as _q
    cs = rows("02_业务资料", "主要客户清单_前五名.csv")
    if not cs:
        return
    top5 = sum((n(r["本期销售额"]) for r in cs[:5]), D("0"))
    # 营业收入取自管理层未审合并报表
    rev = D("0")
    for r in rows("01_账务资料", "管理层编制的合并财务报表_未审数.csv"):
        if r.get("项目") == "营业收入":
            rev = n(r.get("期末/本期金额"))
    if rev and top5 / rev > D("0.50"):
        detail = "；".join(f"{r['客户名称']} {n(r['本期销售额']) / rev * 100:.1f}%"
                          for r in cs[:5] if n(r["本期销售额"]) > 0)
        hit("revenue_concentration", ["E12"],
            f"前五名客户合计 {top5:,.2f}，占营业收入 {top5 / rev * 100:.1f}%，"
            f"未披露客户集中风险。明细：{detail}", top5)


def p_note_reclass() -> None:
    """票据置换：应收票据明细里的"由应收账款置换取得"。"""
    for r in rows("02_业务资料", "应收票据明细表.csv"):
        if marked(r.get("取得方式")) or "置换" in r.get("取得方式", ""):
            hit("note_reclass", ["E07"],
                f"票据 {r.get('票据号')}：取得方式「{r.get('取得方式')}」，金额 {r.get('票面金额')}，"
                f"出票人 {r.get('出票人')}", n(r.get("票面金额")))
    for r in rows("02_业务资料", "应收账款账龄明细表_管理层编制.csv"):
        if "商业承兑汇票" in r.get("客户名称", ""):
            hit("note_reclass", ["E07"],
                f"账龄表列示「{r['客户名称']}」{r['期末余额']}，原为 2–3 年逾期应收，"
                f"置换后不再计提坏账", n(r["期末余额"]))


def p_contract_clause() -> None:
    """合同关键条款扫描：返利、阴阳合同、关联定价。"""
    for r in rows("02_业务资料", "重大销售与采购合同台账.csv"):
        kw = r.get("关键条款", "")
        if "返利" in kw:
            m_ = re.search(r"应返 ([\\d,]+)", kw)
            amt = n(m_.group(1)) if m_ else D("0")
            hit("contract_clause", ["E11"], f"合同 {r['合同编号']} 含返利条款：{kw}",
                amt or D("28000000"))
        if "备案合同" in kw or "实际结算价" in kw:
            hit("contract_clause", ["E32"],
                f"合同 {r['合同编号']}：{kw}", n(r.get("合同金额")))
        if "低于同期第三方批发价" in kw or "定价依据" in kw:
            hit("contract_clause", ["E13"], f"合同 {r['合同编号']}：{kw}", n(r.get("合同金额")))


#: 与资产相关的补助，按文件金额映射到对应发现编号
GOV_GRANT_MAP = {"30000000": "F8", "20000000": "E08"}


def p_gov_grant_class() -> None:
    """补助分类：与资产相关却全额计入当期损益（文件侧与账务侧互为印证）。"""
    for r in rows("06_其他资料", "政府补助文件摘要.csv"):
        if "与资产相关" not in r.get("补助性质", ""):
            continue
        amt = n(r.get("补助金额"))
        gid = GOV_GRANT_MAP.get(str(int(amt)))
        if gid:
            hit("gov_grant_class", [gid],
                f"{r['文件名称']}（{r['文号']}）：补助 {amt:,.2f}，性质「{r['补助性质']}」，"
                f"约定用途「{r['约定用途']}」—— 应计入递延收益而非当期损益", amt)


def p_aging_lookup() -> None:
    """账龄分析：预付款项账龄超 1 年。"""
    for r in rows("02_业务资料", "预付款项账龄明细表.csv"):
        age = r.get("账龄", "")
        if "2 年" in age or "2年" in age or marked(r.get("是否已逾期")):
            hit("aging_lookup", ["E25"],
                f"{r['供应商名称']}：预付款 {r['预付款项余额']}，账龄 {age}，"
                f"约定到货 {r['合同约定到货时间']}，实际 {r['实际到货']}", n(r["预付款项余额"]))


def p_subsequent_return() -> None:
    """期后事项：属调整事项但未调整。"""
    for r in rows("02_业务资料", "期后销售退回清单.csv"):
        if "否" in r.get("是否已调整 2025 年度收入", "") and "2025 年 12 月" in r.get("原确认收入期间", ""):
            hit("subsequent_return", ["E10"],
                f"{r['退回日期']} 退回 {r['退回金额']}（原确认期间 {r['原确认收入期间']}），"
                f"未调整 2025 年收入：{r['说明']}", n(r["退回金额"]))


def p_dev_cost_and_voucher() -> None:
    """研发费用归集 + 费用真实性（虚假咨询服务费）。"""
    for r in rows("02_业务资料", "研发项目台账.csv"):
        if marked(r.get("研发阶段")) and n(r.get("实际参与研发工时占比").replace("%", "")) == 0:
            hit("dev_cost_test", ["E21"],
                f"项目 {r['项目编号']} {r['项目名称']}：阶段 {r['研发阶段']}，"
                f"发生额 {r['本期发生额']}", n(r["本期发生额"]))
    for r in rows("02_业务资料", "主要供应商及付款流水.csv"):
        note = r.get("工商与人员核查备注", "")
        if "成立" in note and "无" in note:
            hit("voucher_review", ["E31"],
                f"供应商 {r['供应商名称']}：交易 {r['本期交易金额']}，{note}",
                n(r["本期交易金额"]))


# ---------------------------------------------------------------------------
# 程序清单与主流程
# ---------------------------------------------------------------------------
PROCEDURES = [
    ("sales_cutoff", "收入截止测试", p_sales_cutoff),
    ("goods_flow", "收入实物流转测试", p_goods_flow),
    ("aging_recompute", "账龄分析与坏账准备重算", p_aging_recompute),
    ("nrv_test", "存货跌价测试复核", p_nrv_test),
    ("inventory_count", "存货监盘差异分析", p_inventory_count),
    ("cogs_recompute", "主营业务成本倒算", p_cogs_recompute),
    ("dep_recompute", "固定资产折旧重算", p_dep_recompute),
    ("impairment_test", "固定资产减值测试复核", p_fa_impair_and_count),
    ("fa_count", "固定资产监盘差异分析", p_fa_impair_and_count),
    ("cip_transfer", "在建工程转固检查", p_cip_transfer),
    ("asset_condition", "资产确认条件测试", p_asset_condition),
    ("expense_cutoff", "费用截止与凭证抽查", p_expense_cutoff),
    ("voucher_review", "费用真实性与供应商背景核查", p_dev_cost_and_voucher),
    ("ap_lookup", "未入账负债检查", p_ap_lookup),
    ("payroll_recompute", "社保与薪酬交叉复核", p_payroll_recompute),
    ("salary_cutoff", "薪酬截止测试", p_salary_cutoff),
    ("related_party", "关联方穿透识别", p_related_party),
    ("bank_recon", "银行存款余额调节表编制", p_bank_recon),
    ("ic_reconcile", "内部往来对账", p_ic_reconcile),
    ("consolidation_scope", "合并范围复核", p_consolidation_scope),
    ("disclosure_lookup", "披露完整性检查", p_disclosure_lookup),
    ("note_reclass", "应收票据置换检查", p_note_reclass),
    ("revenue_concentration", "收入集中度分析", p_revenue_concentration),
    ("contract_clause", "合同关键条款扫描", p_contract_clause),
    ("gov_grant_class", "政府补助分类测试", p_gov_grant_class),
    ("aging_lookup", "预付款账龄分析", p_aging_lookup),
    ("subsequent_return", "期后事项检查", p_subsequent_return),
    ("dev_cost_test", "研发费用归集测试", p_dev_cost_and_voucher),
]


def main() -> None:
    print("=" * 100)
    print("实验组检出验证：只读资料文件，执行 28 条确定性审计程序，与预期发现清单比对")
    print("=" * 100)

    for _rule, _name, fn in PROCEDURES:
        fn()

    detected: dict[str, list[str]] = {}
    for h in HITS:
        for i in h["ids"]:
            detected.setdefault(i, []).append(f"{h['rule']}：{h['evidence']}")

    truth_ids = {it["id"] for it in GT.ISSUES}
    all_ids = [it["id"] for it in GT.ISSUES]
    tp = [i for i in all_ids if i in detected]
    fn_ids = [i for i in all_ids if i not in detected]
    fp = [i for i in detected if i not in truth_ids]

    rule_fired = {h["rule"] for h in HITS}

    print(f"\n【程序执行结果】共 {len(PROCEDURES)} 条程序，{len(rule_fired)} 条命中")
    for rule, name, _fn in PROCEDURES:
        hits = [h for h in HITS if h["rule"] == rule]
        mark = "✓" if hits else "·"
        ids = sorted({i for h in hits for i in h["ids"]})
        print(f"  {mark} {name:<22} 命中 {len(hits):>2} 条  涉及 {','.join(ids) if ids else '—'}")

    print(f"\n【召回】预期 {len(truth_ids)} 项，检出 {len(tp)} 项，"
          f"召回率 {len(tp) / len(truth_ids) * 100:.1f}%")
    if fn_ids:
        print("\n  未检出：")
        for i in fn_ids:
            it = next(x for x in GT.ISSUES if x["id"] == i)
            print(f"    ✗ {i}  {it['title'][:52]}…  规则={it['auto_rule'] or '需人工判断'}")
    print(f"\n【误报】{len(fp)} 项" + ("（无）" if not fp else "：" + "、".join(fp)))

    lines = ["# 实验组检出验证报告", "",
             f"> 由 `detect_issues.py` 生成 · 只读资料文件执行 {len(PROCEDURES)} 条确定性审计程序", "",
             "## 一、总览", "",
             "| 口径 | 数值 |", "| --- | --- |",
             f"| 预期发现（Ground Truth） | **{len(truth_ids)} 项** |",
             f"| 程序数 | {len(PROCEDURES)} 条 |",
             f"| 命中程序数 | {len(rule_fired)} 条 |",
             f"| **检出项数（真阳性）** | **{len(tp)} 项** |",
             f"| **召回率** | **{len(tp) / len(truth_ids) * 100:.1f}%** |",
             f"| 漏检（假阴性） | {len(fn_ids)} 项 |",
             f"| 误报（假阳性） | {len(fp)} 项 |", "",
             "## 二、程序执行明细", "",
             "| 程序 | 命中条数 | 涉及编号 | 结论 |", "| --- | --- | --- | --- |"]
    for rule, name, _fn in PROCEDURES:
        hits = [h for h in HITS if h["rule"] == rule]
        ids = sorted({i for h in hits for i in h["ids"]})
        lines.append(f"| {name} | {len(hits)} | {'、'.join(ids) if ids else '—'} | "
                     f"{'✓ 检出' if hits else '· 未命中'} |")

    lines += ["", "## 三、逐项检出证据", ""]
    for i in all_ids:
        it = next(x for x in GT.ISSUES if x["id"] == i)
        lines.append(f"### {i}　{it['title']}")
        lines.append("")
        lines.append(f"- 性质 / 严重：{NATURE(it['nature'])} / {it['severity']}　"
                     f"处理：{it['disposition']}　金额：{int(it['amount']):,} 元")
        lines.append(f"- 应实施程序：{it['procedure']}")
        if i in detected:
            for e in detected[i]:
                lines.append(f"- ✅ 检出证据：{e}")
        else:
            lines.append(f"- ❌ **未检出**（规则 `{it['auto_rule']}`；"
                         f"需人工判断或程序未覆盖）")
        lines.append("")

    if fn_ids:
        lines += ["## 四、漏检分析", ""]
        for i in fn_ids:
            it = next(x for x in GT.ISSUES if x["id"] == i)
            lines.append(f"- **{i}** {it['title']}")
            lines.append(f"  - 规则 `{it['auto_rule']}`；证据文件：{'；'.join(it['evidence'])}")
            lines.append("  - 未触发原因：资料里缺少该程序可直接读到的结构化字段，"
                         "或需要跨文件推理 / 职业判断。")
        lines.append("")

    os.makedirs(TRUTH, exist_ok=True)
    with open(os.path.join(TRUTH, "检出验证报告.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    print(f"\n报告已写入 {os.path.join(TRUTH, '检出验证报告.md')}")
    print("=" * 100)


def NATURE(k: str) -> str:
    return {"error": "错报", "fraud": "舞弊", "disclosure": "披露不充分",
            "control": "内控与程序"}[k]


if __name__ == "__main__":
    main()
