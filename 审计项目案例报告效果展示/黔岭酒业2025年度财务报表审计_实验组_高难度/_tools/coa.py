# -*- coding: utf-8 -*-
"""案例项目共享层：会计科目表、报表行映射、记账与报表工具。

被 gen_client_data.py（造客户账）与 audit_engine.py（审计作业）共同引用。
只做确定性计算，不读文件、不写文件。
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

D = Decimal
Z = D("0.00")
PCT = D("0.01")


def q(x) -> Decimal:
    return D(str(x)).quantize(D("0.01"))


# ---------------------------------------------------------------------------
# 会计科目表
# ---------------------------------------------------------------------------
ACCOUNTS: dict[str, tuple[str, str, str]] = {
    # 科目号: (名称, 类别, 报表归属)
    "1001": ("库存现金", "asset", "货币资金"),
    "1002": ("银行存款", "asset", "货币资金"),
    "1012": ("其他货币资金", "asset", "货币资金"),
    "1101": ("交易性金融资产", "asset", "交易性金融资产"),
    "1121": ("应收票据", "asset", "应收票据"),
    "1122": ("应收账款", "asset", "应收账款"),
    "1231": ("坏账准备", "asset_contra", "应收账款"),
    "1123": ("预付款项", "asset", "预付款项"),
    "1221": ("其他应收款", "asset", "其他应收款"),
    "1401": ("原材料", "asset", "存货"),
    "1402": ("生产成本", "asset", "存货"),
    "1405": ("库存商品", "asset", "存货"),
    "1471": ("存货跌价准备", "asset_contra", "存货"),
    "1501": ("长期股权投资", "asset", "长期股权投资"),
    "1601": ("固定资产", "asset", "固定资产"),
    "1602": ("累计折旧", "asset_contra", "固定资产"),
    "1603": ("固定资产减值准备", "asset_contra", "固定资产"),
    "1604": ("在建工程", "asset", "在建工程"),
    "1701": ("无形资产", "asset", "无形资产"),
    "1702": ("累计摊销", "asset_contra", "无形资产"),
    "1801": ("长期待摊费用", "asset", "长期待摊费用"),
    "1811": ("递延所得税资产", "asset", "递延所得税资产"),
    "1901": ("其他流动资产", "asset", "其他流动资产"),
    "2001": ("短期借款", "liability", "短期借款"),
    "2202": ("应付账款", "liability", "应付账款"),
    "2204": ("合同负债", "liability", "合同负债"),
    "2211": ("应付职工薪酬", "liability", "应付职工薪酬"),
    "2221": ("应交税费", "liability", "应交税费"),
    "2241": ("其他应付款", "liability", "其他应付款"),
    "2601": ("租赁负债", "liability", "租赁负债"),
    "2611": ("一年内到期的非流动负债", "liability", "一年内到期的非流动负债"),
    "2901": ("递延收益", "liability", "递延收益"),
    "4001": ("股本", "equity", "股本"),
    "4002": ("资本公积", "equity", "资本公积"),
    "4101": ("盈余公积", "equity", "盈余公积"),
    "4103": ("一般风险准备", "equity", "一般风险准备"),
    "4104": ("未分配利润", "equity", "未分配利润"),
    "6001": ("主营业务收入", "income", "营业收入"),
    "6051": ("其他业务收入", "income", "营业收入"),
    "6111": ("投资收益", "income", "投资收益"),
    "6117": ("其他收益", "income", "其他收益"),
    "6301": ("营业外收入", "income", "营业外收入"),
    "6401": ("主营业务成本", "expense", "营业成本"),
    "6402": ("其他业务成本", "expense", "营业成本"),
    "6403": ("税金及附加", "expense", "税金及附加"),
    "6601": ("销售费用", "expense", "销售费用"),
    "6602": ("管理费用", "expense", "管理费用"),
    "6603": ("研发费用", "expense", "研发费用"),
    "6604": ("财务费用", "expense", "财务费用"),
    "6701": ("信用减值损失", "expense", "信用减值损失"),
    "6702": ("资产减值损失", "expense", "资产减值损失"),
    "6711": ("营业外支出", "expense", "营业外支出"),
    "6801": ("所得税费用", "expense", "所得税费用"),
}

CASH = {"1001", "1002", "1012"}
INCOME_CODES = {"6001", "6051", "6111", "6117", "6301"}

BS_ASSET_ORDER = [
    "货币资金", "交易性金融资产", "应收票据", "应收账款", "预付款项", "其他应收款",
    "存货", "其他流动资产", "流动资产合计",
    "长期股权投资", "固定资产", "在建工程", "无形资产", "长期待摊费用",
    "递延所得税资产", "非流动资产合计", "资产总计",
]
CURRENT_ASSETS = ["货币资金", "交易性金融资产", "应收票据", "应收账款",
                  "预付款项", "其他应收款", "存货", "其他流动资产"]
NONCURRENT_ASSETS = ["长期股权投资", "固定资产", "在建工程", "无形资产",
                     "长期待摊费用", "递延所得税资产"]

BS_LIAB_ORDER = [
    "短期借款", "应付账款", "合同负债", "应付职工薪酬", "应交税费",
    "其他应付款", "一年内到期的非流动负债", "流动负债合计",
    "租赁负债", "递延收益", "非流动负债合计", "负债合计",
]
CURRENT_LIABS = ["短期借款", "应付账款", "合同负债", "应付职工薪酬",
                 "应交税费", "其他应付款", "一年内到期的非流动负债"]
NONCURRENT_LIABS = ["租赁负债", "递延收益"]

BS_EQUITY_ORDER = ["股本", "资本公积", "盈余公积", "一般风险准备", "未分配利润",
                   "归属于母公司所有者权益合计", "少数股东权益", "所有者权益合计"]

IS_ORDER = ["营业收入", "营业成本", "税金及附加", "销售费用", "管理费用", "研发费用",
            "财务费用", "其他收益", "投资收益", "信用减值损失", "资产减值损失",
            "营业利润", "营业外收入", "营业外支出", "利润总额", "所得税费用", "净利润"]

#: 报表行 -> 科目集合（由 ACCOUNTS 的第三列反推，避免两处维护）
LINE_TO_CODES: dict[str, list[str]] = {}
for _code, (_name, _kind, _line) in ACCOUNTS.items():
    LINE_TO_CODES.setdefault(_line, []).append(_code)


# ---------------------------------------------------------------------------
# 会计主体
# ---------------------------------------------------------------------------
ENTITY = {
    "P": {"name": "黔岭酒业股份有限公司", "short": "母公司"},
    "S1": {"name": "黔岭销售有限公司", "short": "黔岭销售"},
    "S2": {"name": "黔岭酒类包装有限公司", "short": "黔岭包装"},
}
HOLDING = {"S1": D("1.00"), "S2": D("0.80")}


# ---------------------------------------------------------------------------
# 记账
# ---------------------------------------------------------------------------
def build_ledger(opening: dict, entries: list[dict]) -> dict[str, dict[str, Decimal]]:
    """opening: {主体: {科目: 借正贷负}}；entries: 含 entity/lines 的分录列表。"""
    led: dict[str, dict[str, Decimal]] = {
        ent: {c: q(v) for c, v in bal.items()} for ent, bal in opening.items()}
    for e in entries:
        bal = led[e["entity"]]
        dr = sum((q(l[1]) for l in e["lines"] if l[1]), Z)
        cr = sum((q(l[2]) for l in e["lines"] if l[2]), Z)
        if dr != cr:
            raise AssertionError(f"分录不平：{e.get('memo')} 借{dr} 贷{cr}")
        for code, d, c in e["lines"]:
            bal[code] = bal.get(code, Z) + q(d or 0) - q(c or 0)
    return led


def total(led: dict[str, Decimal], codes) -> Decimal:
    return sum((led.get(c, Z) for c in codes), Z)


def balance_check(led: dict[str, Decimal]) -> Decimal:
    return sum(led.values(), Z)


# ---------------------------------------------------------------------------
# 合并工作底稿
# ---------------------------------------------------------------------------
def consolidate(led: dict[str, dict[str, Decimal]], *, internal_ar: dict[str, str],
                intercompany_sales: list[str], lti: str):
    """汇总三主体 + 抵消分录。返回 (合并试算平衡表, 抵消分录清单)。"""
    merged: dict[str, Decimal] = defaultdict(lambda: Z)
    for ent in led:
        for code, v in led[ent].items():
            merged[code] += v

    elims: list[dict] = []

    def elim(memo: str, kind: str, lines: list[tuple[str, str, str]]) -> None:
        for code, d, c in lines:
            merged[code] = merged.get(code, Z) + q(d or 0) - q(c or 0)
        elims.append({"memo": memo, "kind": kind,
                      "amount": str(sum((q(l[1]) for l in lines if l[1]), Z)),
                      "lines": [[l[0], l[1] or "0", l[2] or "0"] for l in lines]})

    for memo, amt in intercompany_sales:
        elim(memo, "内部购销", [("6001", amt, None), ("6401", None, amt)])
    for memo, amt in internal_ar.items():
        elim(f"抵消内部应收应付：{memo}", "内部往来", [("2202", amt, None), ("1122", None, amt)])
    elim("抵消内部股利与母公司投资收益", "内部股利",
         [("6111", "80000000", None), ("4104", None, "80000000")])
    elim("抵消母公司对子公司的长期股权投资", "权益抵消", [("1501", None, lti)])

    return {k: q(v) for k, v in merged.items()}, elims


# ---------------------------------------------------------------------------
# 报表编制
# ---------------------------------------------------------------------------
def minority_interest(led_entities: dict[str, dict[str, Decimal]],
                      profit_by_entity: dict[str, Decimal]) -> Decimal:
    """少数股东权益 = 少数股东持股比例 × 子公司期末权益（含本年利润）。"""
    out = Z
    for sub, ratio in HOLDING.items():
        eq = -total(led_entities[sub], ("4001", "4002", "4101", "4103", "4104"))
        eq += profit_by_entity.get(sub, Z)
        out += q(eq * (D(1) - ratio))
    return q(out)


def bs_rows(led: dict[str, Decimal], *, net_profit: Decimal = Z,
            minority: Decimal | None = None) -> dict[str, Decimal]:
    out: dict[str, Decimal] = {}
    for line in CURRENT_ASSETS + NONCURRENT_ASSETS:
        out[line] = total(led, LINE_TO_CODES[line])
    for line in ("短期借款", "应付账款", "合同负债", "应付职工薪酬", "应交税费",
                 "其他应付款", "一年内到期的非流动负债", "租赁负债", "递延收益"):
        out[line] = -total(led, LINE_TO_CODES[line])
    for line in ("股本", "资本公积", "盈余公积", "一般风险准备", "未分配利润"):
        out[line] = -total(led, LINE_TO_CODES[line])
    out["未分配利润"] += net_profit        # 损益尚未结转
    out["流动资产合计"] = sum((out[n] for n in CURRENT_ASSETS), Z)
    out["非流动资产合计"] = sum((out[n] for n in NONCURRENT_ASSETS), Z)
    out["资产总计"] = out["流动资产合计"] + out["非流动资产合计"]
    out["流动负债合计"] = sum((out[n] for n in CURRENT_LIABS), Z)
    out["非流动负债合计"] = sum((out[n] for n in NONCURRENT_LIABS), Z)
    out["负债合计"] = out["流动负债合计"] + out["非流动负债合计"]
    if minority is not None:
        total_eq = out["资产总计"] - out["负债合计"]
        out["少数股东权益"] = minority
        out["归属于母公司所有者权益合计"] = total_eq - minority
        out["所有者权益合计"] = total_eq
    return out


def is_rows(led: dict[str, Decimal]) -> dict[str, Decimal]:
    raw: dict[str, Decimal] = {}
    for line in ("营业收入", "营业成本", "税金及附加", "销售费用", "管理费用",
                 "研发费用", "财务费用", "其他收益", "投资收益",
                 "信用减值损失", "资产减值损失", "营业外收入", "营业外支出", "所得税费用"):
        codes = LINE_TO_CODES[line]
        v = total(led, codes)
        raw[line] = -v if set(codes) <= INCOME_CODES else v
    out = dict(raw)
    out["营业利润"] = (raw["营业收入"] - raw["营业成本"] - raw["税金及附加"]
                    - raw["销售费用"] - raw["管理费用"] - raw["研发费用"] - raw["财务费用"]
                    + raw["其他收益"] + raw["投资收益"]
                    - raw["信用减值损失"] - raw["资产减值损失"])
    out["利润总额"] = out["营业利润"] + raw["营业外收入"] - raw["营业外支出"]
    out["净利润"] = out["利润总额"] - raw["所得税费用"]
    return out


def cash_flow_direct(entries: list[dict], *, skip_src: set[str] | None = None) -> dict[str, Decimal]:
    """直接法：按分录上的 cf 标签聚合现金账户净流量。"""
    skip = skip_src or set()
    out: dict[str, Decimal] = defaultdict(lambda: Z)
    for e in entries:
        cf = e.get("dims", {}).get("cf")
        if not cf or e.get("dims", {}).get("src") in skip:
            continue
        net = sum((q(l[1] or 0) - q(l[2] or 0)) for l in e["lines"] if l[0] in CASH)
        if net:
            out[cf] += net
    return {k: q(v) for k, v in out.items()}


def merge_openings(opening: dict) -> dict[str, Decimal]:
    out: dict[str, Decimal] = defaultdict(lambda: Z)
    for bal in opening.values():
        for code, v in bal.items():
            out[code] += q(v)
    return {k: q(v) for k, v in out.items()}


def fmt(x) -> str:
    return f"{q(x):,.2f}"


def wan(x) -> str:
    """以万元显示，保留 2 位。"""
    return f"{(q(x) / D('10000')):,.2f}"
