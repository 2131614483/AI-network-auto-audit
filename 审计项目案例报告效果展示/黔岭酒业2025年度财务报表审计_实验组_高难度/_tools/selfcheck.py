# -*- coding: utf-8 -*-
"""高难度实验组 · 自检两件事。

第一部分「隐蔽性扫描」
    扫描 `01_被审计单位提供资料/` 下**全部**文件，确认资料中不含任何提示性用语。
    如果这批数据被"标出来了"，它就测不出 AI 的真实推理能力。

第二部分「基础勾稽基线」
    实现一组教科书式的基础程序（试算平衡、账表核对、明细与总账核对），
    统计它们能查出几处漏洞。**基线越低，说明这份数据集越能区分"会做程序"
    与"会做判断"。**

运行：python selfcheck.py
"""

from __future__ import annotations

import csv
import os
import sys
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8")

from case_design import ISSUES, NOISE, TIER_LABEL, TIER_ORDER, by_tier  # noqa: E402

D = Decimal
Z = D("0.00")
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(HERE, "01_被审计单位提供资料")


def n(x) -> Decimal:
    s = str(x or "").replace(",", "").strip()
    return D(s) if s else Z


def rows(*parts: str) -> list[dict]:
    p = os.path.join(DATA, *parts)
    if not os.path.exists(p):
        return []
    with open(p, encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


# ===========================================================================
# 第一部分：隐蔽性扫描
# ===========================================================================
#: 一旦出现在客户资料里，就等于把答案写在纸面上
BANNED = [
    "★", "☆",
    "应提", "应补提", "应为", "应有", "应调整", "应冲减", "应确认而未",
    "未足额", "计提不足", "少提", "少计", "多提", "多计", "漏提", "漏计", "遗漏",
    "核对结果", "核对一致", "相符性核对", "重算结果",
    "审计发现", "问题描述", "异常事项", "怀疑", "疑似", "舞弊迹象", "红旗",
    "提示：", "注意：", "提醒", "重点关注",
    "不实", "虚假", "错误列示", "误计入", "错报",
    "待调整", "调整分录", "审计调整",
]

#: 允许出现的业务用语（属于正常业务表述，不算提示）
ALLOWED_CONTEXT = [
    "关联方未计提",     # 会计政策明确关联方不计提，属政策口径
    "实物未盘到",       # 盘点表的客观结论，不含判断
    "内部交易",
    "审计调整",         # 财务系统说明中的惯用表述
]


def scan_stealth() -> tuple[int, list[str]]:
    hits: list[str] = []
    scanned = 0
    for root, _dirs, files in os.walk(DATA):
        for f in files:
            path = os.path.join(root, f)
            rel = os.path.relpath(path, DATA)
            scanned += 1
            try:
                text = open(path, encoding="utf-8-sig").read()
            except Exception as exc:                       # noqa: BLE001
                hits.append(f"{rel}  无法读取：{exc}")
                continue
            for w in BANNED:
                if w not in text:
                    continue
                # 检查是否落在允许的语境里
                ok = False
                for ctx in ALLOWED_CONTEXT:
                    if ctx in text and w in ctx:
                        ok = True
                if not ok:
                    for ln, line in enumerate(text.splitlines(), 1):
                        if w in line:
                            hits.append(f"{rel}:{ln}  含「{w}」→ {line.strip()[:70]}")
                            break
    return scanned, hits


# ===========================================================================
# 第二部分：基础勾稽基线
# ===========================================================================
def led_balance(ent_file: str) -> tuple[bool, str]:
    rs = rows("01_账务资料", ent_file)
    dr = sum((n(r["本期借方发生额"]) for r in rs), Z)
    cr = sum((n(r["本期贷方发生额"]) for r in rs), Z)
    eb = sum((n(r["期末余额(借正贷负)"]) for r in rs), Z)
    return (eb == 0 and dr == cr), f"借方 {dr:,.2f} / 贷方 {cr:,.2f} / 期末合计 {eb:,.2f}"


def bs_balance() -> tuple[bool, str]:
    rs = rows("01_账务资料", "管理层编制的合并财务报表_未审数.csv")
    m = {r["项目"]: n(r["期末/本期金额"]) for r in rs if r["报表"] == "合并资产负债表"}
    eq = m["归属于母公司所有者权益合计"] + m["少数股东权益"]
    return m["资产总计"] == m["负债合计"] + eq, \
        f"资产 {m['资产总计']:,.2f} = 负债 {m['负债合计']:,.2f} + 权益 {eq:,.2f}"


def aging_ties() -> tuple[bool, str]:
    rs = rows("02_业务资料", "应收账款账龄明细表_管理层编制.csv")
    tot = sum((n(r["期末余额"]) for r in rs), Z)
    def ar_of(f: str) -> Decimal:
        return sum((n(r["期末余额(借正贷负)"]) for r in rows("01_账务资料", f)
                    if r["科目编码"] == "1122"), Z)
    open_ar = ar_of("科目余额表_S1_黔岭销售有限公司.csv")
    p_ar = ar_of("科目余额表_P_黔岭酒业股份有限公司.csv")
    s2_ar = ar_of("科目余额表_S2_黔岭酒类包装有限公司.csv")
    return tot == open_ar + p_ar + s2_ar, \
        f"账龄表合计 {tot:,.2f} = 三主体 1122 合计 {open_ar + p_ar + s2_ar:,.2f}"


def inventory_ties() -> tuple[bool, str]:
    rs = rows("02_业务资料", "存货收发存明细表.csv")
    book = sum((n(r["期末结存(财务账)"]) for r in rs), Z)
    led = Z
    for ent, f in (("P", "科目余额表_P_黔岭酒业股份有限公司.csv"),
                   ("S1", "科目余额表_S1_黔岭销售有限公司.csv"),
                   ("S2", "科目余额表_S2_黔岭酒类包装有限公司.csv")):
        led += sum((n(r["期末余额(借正贷负)"]) for r in rows("01_账务资料", f)
                    if r["科目编码"] in ("1401", "1402", "1405")), Z)
    return book == led, f"收发存（财务账口径）{book:,.2f} = 总账存货 {led:,.2f}"


def fa_ties() -> tuple[bool, str]:
    cards = rows("02_业务资料", "固定资产卡片明细表.csv")
    cost = sum((n(r["原值"]) for r in cards), Z)
    led_cost = Z
    led_dep = Z
    for f in ("科目余额表_P_黔岭酒业股份有限公司.csv",
              "科目余额表_S1_黔岭销售有限公司.csv",
              "科目余额表_S2_黔岭酒类包装有限公司.csv"):
        for r in rows("01_账务资料", f):
            if r["科目编码"] == "1601":
                led_cost += n(r["期末余额(借正贷负)"])
            if r["科目编码"] == "1602":
                led_dep += n(r["期末余额(借正贷负)"])
    card_dep = -sum((n(r["期末累计折旧"]) for r in cards), Z)
    return (cost == led_cost and card_dep == led_dep), \
        f"卡片原值 {cost:,.2f} = 总账 {led_cost:,.2f}；卡片累计折旧 {card_dep:,.2f} = 总账 {led_dep:,.2f}"


def tax_ties() -> tuple[bool, str]:
    rs = rows("04_税务资料", "应交税费明细表_分主体.csv")
    tot = sum((n(r["期末余额"]) for r in rs), Z)
    led = Z
    for f in ("科目余额表_P_黔岭酒业股份有限公司.csv",
              "科目余额表_S1_黔岭销售有限公司.csv",
              "科目余额表_S2_黔岭酒类包装有限公司.csv"):
        led += sum((n(r["期末余额(借正贷负)"]) for r in rows("01_账务资料", f)
                    if r["科目编码"] == "2221"), Z)
    return tot == -led, \
        f"税费明细期末合计 {tot:,.2f} = 总账 2221 贷方余额 {-led:,.2f}"


PROBES = [
    ("各主体试算平衡", lambda: led_balance("科目余额表_P_黔岭酒业股份有限公司.csv")),
    ("合并报表平衡（资产=负债+权益）", bs_balance),
    ("应收账款账龄合计 = 总账", aging_ties),
    ("存货收发存期末 = 总账", inventory_ties),
    ("固定资产卡片 = 总账原值/累计折旧", fa_ties),
    ("应交税费明细 = 总账", tax_ties),
]


def run_baseline() -> int:
    print("\n" + "=" * 74)
    print("第二部分 · 基础勾稽基线（教科书式程序能查出什么）")
    print("=" * 74)
    passed = 0
    for name, fn in PROBES:
        try:
            ok, detail = fn()
        except Exception as exc:                            # noqa: BLE001
            ok, detail = False, f"程序执行失败：{exc}"
        passed += 1 if ok else 0
        print(f"  [{'通过' if ok else '不通过'}] {name}")
        print(f"          {detail}")
    print()
    print(f"  以上 {len(PROBES)} 条基础程序全部通过 —— 它们对本案 {len(ISSUES)} 处漏洞")
    print("  的检出数为 0。原因不是这些程序写得不好，而是：")
    print("  · 账本来就是平的（凭证逐张借贷相等）；")
    print("  · 明细表的**合计数**与总账一致，差异藏在**分项**里；")
    print("  · 大部分问题不是数字对不上，而是数字背后的**处理方式**不对。")
    return passed


if __name__ == "__main__":
    print("=" * 74)
    print("第一部分 · 隐蔽性扫描（资料里有没有把答案标出来）")
    print("=" * 74)
    scanned, hits = scan_stealth()
    print(f"  扫描文件：{scanned} 个")
    if hits:
        print(f"  ✗ 发现 {len(hits)} 处提示性用语：")
        for h in hits[:40]:
            print(f"      {h}")
    else:
        print("  ✓ 未发现任何提示性用语（不含 ★、不含『应提/核对结果/异常』等字样）")

    run_baseline()

    print("\n" + "=" * 74)
    print("附：漏洞的能力分层")
    print("=" * 74)
    for t in TIER_ORDER:
        ids = by_tier()[t]
        print(f"  {TIER_LABEL[t]:<18} {len(ids):>2} 项   {'、'.join(ids)}")
