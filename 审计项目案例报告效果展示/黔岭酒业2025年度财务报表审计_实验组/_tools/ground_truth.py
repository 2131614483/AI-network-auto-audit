# -*- coding: utf-8 -*-
"""实验组「预期发现清单」（Ground Truth）。

这是**出题人的答案**：实验组数据集里到底埋了哪些坑、埋在哪、该用什么程序挖出来。
`gen_experiment_data.py` 按这份清单把证据写进资料文件；
`detect_issues.py` 按这份清单给程序打分（召回 / 漏检 / 误报）。

字段说明
--------
id            发现编号（F* 沿用对照组基础 10 项；E* 为实验组新增 32 项）
group         问题分组
title         问题描述
amount        涉及金额（元）
report_lines  影响的报表项目
assertion     影响的认定
nature        error（错报）/ fraud（舞弊）/ disclosure（披露不充分）/ control（内控与程序）
severity      高 / 中 / 低
disposition   需调整 / 仅披露 / 无需调整（记录内控）
evidence      证据所在文件
procedure     应实施的审计程序
auto_rule     可自动检出的规则名（detect_issues.py 的 procedure key）；None 表示需人工判断
"""

from __future__ import annotations

ISSUES: list[dict] = [
    # ===================== 基础 10 项（沿用对照组） =====================
    dict(id="F1", group="收入与应收", title="收入跨期（提前确认）：华东区经销收入于 2025-12-30 入账，"
         "出库单/签收单日期为 2026-01-05",
         amount="66000000", report_lines="营业收入、应收账款、合同负债", assertion="截止（期间归属）",
         nature="error", severity="高", disposition="需调整",
         evidence=["02_业务资料/销售明细_分产品分渠道分地区.csv",
                   "01_账务资料/记账凭证_2025年度明细.csv"],
         procedure="收入截止测试：比较记账日期与出库单/签收单日期", auto_rule="sales_cutoff"),
    dict(id="F2", group="关联方", title="关联方未披露：向黔岭物流支付运输费，其法定代表人为本公司董事之弟",
         amount="42000000", report_lines="销售费用、关联方及其交易附注", assertion="披露完整性",
         nature="disclosure", severity="高", disposition="仅披露",
         evidence=["02_业务资料/主要供应商及付款流水.csv", "03_治理资料/组织架构与关键管理人员.md"],
         procedure="关联方识别：股权穿透 + 关键管理人员近亲属核查", auto_rule="related_party"),
    dict(id="F3", group="收入与应收", title="坏账准备计提不足：1–2 年账龄 8,000,000 未按 30% 计提",
         amount="2400000", report_lines="应收账款、信用减值损失", assertion="计价与分摊",
         nature="error", severity="中", disposition="需调整",
         evidence=["02_业务资料/应收账款账龄明细表_管理层编制.csv"],
         procedure="账龄分析 + 坏账准备重算", auto_rule="aging_recompute"),
    dict(id="F4", group="存货与成本", title="存货跌价准备计提不足：春酿库存商品应提 16,000,000，账面仅提 4,000,000",
         amount="12000000", report_lines="存货、资产减值损失", assertion="计价与分摊",
         nature="error", severity="高", disposition="需调整",
         evidence=["02_业务资料/存货跌价测试表_管理层编制.csv", "02_业务资料/存货收发存明细表.csv"],
         procedure="存货跌价测试复核：可变现净值与成本孰低", auto_rule="nrv_test"),
    dict(id="F5", group="固定资产与在建工程", title="固定资产折旧漏提：2025-06-30 转固的高精度检测设备漏提 7–12 月折旧",
         amount="3325000", report_lines="管理费用、固定资产", assertion="计价与分摊",
         nature="error", severity="中", disposition="需调整",
         evidence=["02_业务资料/固定资产及折旧明细表.csv"],
         procedure="折旧重算（直线法，当月增加当月不提）", auto_rule="dep_recompute"),
    dict(id="F6", group="费用与负债", title="费用资本化错误：品牌广告制作费 15,000,000 计入长期待摊费用",
         amount="15000000", report_lines="销售费用、长期待摊费用", assertion="分类",
         nature="error", severity="中", disposition="需调整",
         evidence=["02_业务资料/无形资产及长期待摊费用明细表.csv"],
         procedure="资产确认条件测试：广告费是否带来未来经济利益", auto_rule="asset_condition"),
    dict(id="F7", group="关联方", title="资金占用：其他应收款–黔岭物流 25,000,000 长期挂账、无商业实质",
         amount="25000000", report_lines="其他应收款、信用减值损失", assertion="存在/计价",
         nature="error", severity="高", disposition="需调整",
         evidence=["01_账务资料/记账凭证_2025年度明细.csv", "03_治理资料/董事会决议汇编_2025.md"],
         procedure="其他应收款性质核查：有无协议、是否计息、账龄", auto_rule="related_party"),
    dict(id="F8", group="税务与补助", title="补助分类错误：与资产相关的技改专项补助 30,000,000 全额计入当期其他收益",
         amount="30000000", report_lines="其他收益、递延收益", assertion="分类",
         nature="error", severity="高", disposition="需调整",
         evidence=["06_其他资料/政府补助文件摘要.csv"],
         procedure="补助分类测试：与资产相关 / 与收益相关", auto_rule="gov_grant_class"),
    dict(id="F9", group="银行与资金", title="银行未达账项 5 笔，管理层未编制银行存款余额调节表",
         amount="87540000", report_lines="货币资金", assertion="存在/完整性",
         nature="control", severity="中", disposition="无需调整（记录内控缺陷）",
         evidence=["05_银行资料/银行存款日记账_工商银行基本户_202512.csv",
                   "05_银行资料/银行对账单_工商银行基本户_202512.csv"],
         procedure="编制银行存款余额调节表", auto_rule="bank_recon"),
    dict(id="F10", group="费用与负债", title="薪酬跨期：2025 年 12 月奖金 18,000,000 已批准未计提",
         amount="18000000", report_lines="管理费用、应付职工薪酬", assertion="完整性",
         nature="error", severity="高", disposition="需调整",
         evidence=["03_治理资料/董事会决议汇编_2025.md", "06_其他资料/资产负债表日后事项说明.md"],
         procedure="薪酬截止测试 + 期后事项调整事项判断", auto_rule="salary_cutoff"),

    # ===================== 实验组新增 E01–E08：账面层（有分录） =====================
    dict(id="E01", group="收入与应收", title="应收账款虚增：与关联商贸企业做对开交易 80,000,000，"
         "无实物流转、无出库单与签收单",
         amount="80000000", report_lines="应收账款、营业收入", assertion="存在/发生",
         nature="fraud", severity="高", disposition="需调整",
         evidence=["02_业务资料/销售明细_分产品分渠道分地区.csv",
                   "01_账务资料/记账凭证_2025年度明细.csv"],
         procedure="收入真实性与实物流转测试：穿透出库单、签收单、物流单据", auto_rule="goods_flow"),
    dict(id="E02", group="费用与负债", title="隐瞒费用：收到供应商返还款 36,000,000 挂账其他应付款，未冲减费用",
         amount="36000000", report_lines="其他应付款、营业成本", assertion="完整性",
         nature="fraud", severity="高", disposition="需调整",
         evidence=["02_业务资料/主要供应商及付款流水.csv", "01_账务资料/记账凭证_2025年度明细.csv"],
         procedure="其他应付款性质核查 + 期后事项（返还款是否冲减成本）", auto_rule="ap_lookup"),
    dict(id="E03", group="存货与成本", title="成本结转不实：少转主营业务成本 62,000,000，虚增毛利",
         amount="62000000", report_lines="营业成本、存货", assertion="准确/计价",
         nature="error", severity="高", disposition="需调整",
         evidence=["02_业务资料/存货收发存明细表.csv", "02_业务资料/销售明细_分产品分渠道分地区.csv"],
         procedure="毛利率分析性程序 + 成本倒推（产销量 × 单位成本）", auto_rule="cogs_recompute"),
    dict(id="E04", group="关联方", title="关联方资金占用：借予控股股东 45,000,000，无协议、不计息",
         amount="45000000", report_lines="其他应收款、关联方附注", assertion="存在/披露",
         nature="error", severity="高", disposition="需调整",
         evidence=["02_业务资料/其他应收款明细表.csv", "03_治理资料/组织架构与关键管理人员.md"],
         procedure="其他应收款明细检查 + 关联方穿透", auto_rule="related_party"),
    dict(id="E05", group="费用与负债", title="无票费用（白条入账）：8,600,000 元支出以内部单据列账，无发票",
         amount="8600000", report_lines="管理费用", assertion="准确/可抵扣性",
         nature="control", severity="中", disposition="需调整",
         evidence=["01_账务资料/记账凭证_2025年度明细.csv", "02_业务资料/主要供应商及付款流水.csv"],
         procedure="费用凭证抽查：发票与业务实质", auto_rule="voucher_review"),
    dict(id="E06", group="存货与成本", title="存货盘亏未处理：盘亏 14,000,000 挂账其他流动资产，未计入损益",
         amount="14000000", report_lines="其他流动资产、存货、管理费用", assertion="存在/计价",
         nature="error", severity="中", disposition="需调整",
         evidence=["02_业务资料/存货盘点表.csv", "01_账务资料/记账凭证_2025年度明细.csv"],
         procedure="存货监盘：账面数量与实盘数量核对", auto_rule="inventory_count"),
    dict(id="E07", group="收入与应收", title="票据置换应收账款：将 120,000,000 逾期应收账款置换为商业承兑汇票，"
         "规避账龄与坏账计提",
         amount="6000000", report_lines="应收票据、应收账款、信用减值损失", assertion="分类/计价",
         nature="error", severity="高", disposition="需调整",
         evidence=["02_业务资料/应收票据明细表.csv", "02_业务资料/应收账款账龄明细表_管理层编制.csv"],
         procedure="应收票据明细检查 + 置换交易实质判断（是否改善账龄）", auto_rule="note_reclass"),
    dict(id="E08", group="税务与补助", title="与资产相关政府补助（第二笔）20,000,000 全额计入当期其他收益",
         amount="20000000", report_lines="其他收益、递延收益", assertion="分类",
         nature="error", severity="中", disposition="需调整",
         evidence=["06_其他资料/政府补助文件摘要.csv"],
         procedure="补助分类测试", auto_rule="gov_grant_class"),

    # ===================== 实验组新增 E09–E25：单据/程序层（无分录） =====================
    dict(id="E09", group="收入与应收", title="收入完整性：期末前已发货并签收的 48,000,000 未确认收入",
         amount="48000000", report_lines="营业收入、应收账款", assertion="完整性",
         nature="error", severity="高", disposition="需调整",
         evidence=["02_业务资料/销售明细_分产品分渠道分地区.csv"],
         procedure="收入截止测试：出库单/签收单与收入登记核对（双向）", auto_rule="sales_cutoff"),
    dict(id="E10", group="收入与应收", title="期后退回未调整：2026-02 退回 2025-12-30 发出的货物 12,000,000",
         amount="12000000", report_lines="营业收入、应收账款", assertion="截止",
         nature="error", severity="中", disposition="需调整",
         evidence=["06_其他资料/资产负债表日后事项说明.md", "02_业务资料/期后销售退回清单.csv"],
         procedure="期后事项检查：销售退回、退货单", auto_rule="subsequent_return"),
    dict(id="E11", group="收入与应收", title="销售返利未计提：合同约定年度返利 28,000,000 未入账",
         amount="28000000", report_lines="营业收入、其他应付款", assertion="完整性/准确",
         nature="error", severity="高", disposition="需调整",
         evidence=["02_业务资料/重大销售与采购合同台账.csv", "02_业务资料/销售明细_分产品分渠道分地区.csv"],
         procedure="合同关键条款扫描：返利、折扣、退货条款", auto_rule="contract_clause"),
    dict(id="E12", group="披露与列报", title="收入集中度未披露：前五名客户销售占比 76.6%，未披露客户集中风险",
         amount="3265800000", report_lines="财务报表附注", assertion="披露完整性",
         nature="disclosure", severity="中", disposition="仅披露",
         evidence=["02_业务资料/主要客户清单_前五名.csv"],
         procedure="附注披露清单核对：客户集中度、信用风险集中", auto_rule="disclosure_lookup"),
    dict(id="E13", group="关联方", title="关联方定价非公允：对黔岭销售的内部调拨价低于同类市场价 22%，缺乏定价依据",
         amount="220000000", report_lines="营业收入、关联方附注", assertion="准确/披露",
         nature="disclosure", severity="中", disposition="仅披露",
         evidence=["02_业务资料/重大销售与采购合同台账.csv"],
         procedure="关联交易定价合理性测试：与第三方可比价格对比", auto_rule="contract_clause"),
    dict(id="E14", group="披露与列报", title="应收账款保理未披露：以 150,000,000 应收账款办理有追索权保理，未披露质押与担保",
         amount="150000000", report_lines="应收账款、短期借款、附注", assertion="披露完整性",
         nature="disclosure", severity="中", disposition="仅披露",
         evidence=["02_业务资料/应收账款保理合同摘要.md"],
         procedure="受限资产与融资安排检查", auto_rule="disclosure_lookup"),
    dict(id="E15", group="存货与成本", title="存货账实不符：成品库实盘比账面少 9,200,000，差异未查明也未账务处理",
         amount="9200000", report_lines="存货、管理费用", assertion="存在",
         nature="error", severity="中", disposition="需调整",
         evidence=["02_业务资料/存货盘点表.csv"],
         procedure="存货监盘 + 盘点差异分析", auto_rule="inventory_count"),
    dict(id="E16", group="披露与列报", title="存货质押未披露：以 380,000,000 存货为银行借款提供质押担保，未披露受限资产",
         amount="380000000", report_lines="存货、附注", assertion="披露完整性",
         nature="disclosure", severity="高", disposition="仅披露",
         evidence=["02_业务资料/受限资产与担保台账.csv"],
         procedure="受限资产检查：质押、抵押、查封", auto_rule="disclosure_lookup"),
    dict(id="E17", group="固定资产与在建工程", title="在建工程已达可使用状态未转固：包装生产线技改 2025-08-15 验收合格"
         "并投入使用，仍挂 186,000,000 在建工程（少提折旧 5,890,000）",
         amount="186000000", report_lines="在建工程、固定资产、管理费用", assertion="分类/计价",
         nature="error", severity="高", disposition="需调整",
         evidence=["02_业务资料/在建工程明细表.csv", "02_业务资料/工程验收单摘要.md"],
         procedure="在建工程转固检查：验收单、试运行记录与转固时点核对", auto_rule="cip_transfer"),
    dict(id="E18", group="固定资产与在建工程", title="闲置固定资产减值未计提：闲置包装线净值 41,000,000，"
         "可收回金额仅 12,000,000，未计提减值 29,000,000",
         amount="29000000", report_lines="固定资产、资产减值损失", assertion="计价",
         nature="error", severity="高", disposition="需调整",
         evidence=["02_业务资料/固定资产及折旧明细表.csv", "02_业务资料/资产减值测试表_管理层编制.csv"],
         procedure="固定资产减值迹象识别 + 可收回金额测试", auto_rule="impairment_test"),
    dict(id="E19", group="固定资产与在建工程", title="固定资产盘亏：实盘比账面少 3 台设备，账面价值 6,400,000，未作处理",
         amount="6400000", report_lines="固定资产、营业外支出", assertion="存在",
         nature="error", severity="中", disposition="需调整",
         evidence=["02_业务资料/固定资产盘点表.csv"],
         procedure="固定资产监盘 + 盘点差异分析", auto_rule="fa_count"),
    dict(id="E20", group="披露与列报", title="固定资产产权瑕疵：2 处房产（原值 246,000,000）未办妥产权证书，未披露",
         amount="246000000", report_lines="固定资产、附注", assertion="披露完整性",
         nature="disclosure", severity="中", disposition="仅披露",
         evidence=["02_业务资料/房屋产权登记情况表.csv"],
         procedure="固定资产权属证明检查", auto_rule="disclosure_lookup"),
    dict(id="E21", group="固定资产与在建工程", title="研发支出资本化条件不满足：开发支出 26,000,000 资本化，"
         "相关项目尚处于研究阶段、技术可行性未论证",
         amount="26000000", report_lines="开发支出、研发费用", assertion="分类",
         nature="error", severity="高", disposition="需调整",
         evidence=["02_业务资料/研发项目台账.csv"],
         procedure="研发支出资本化五项条件逐项测试", auto_rule="dev_cost_test"),
    dict(id="E22", group="费用与负债", title="应付账款未入账：期末后收到发票 42,000,000，对应存货已于 2025-12 验收入库",
         amount="42000000", report_lines="应付账款、存货", assertion="完整性",
         nature="error", severity="高", disposition="需调整",
         evidence=["02_业务资料/期后付款与发票清单.csv", "02_业务资料/存货收发存明细表.csv"],
         procedure="未入账负债检查（搜索未记录负债）：期后付款、期末在途发票", auto_rule="ap_lookup"),
    dict(id="E23", group="费用与负债", title="社保欠缴：2025 年 7–12 月社会保险费 6,800,000 未缴纳也未计提",
         amount="6800000", report_lines="应付职工薪酬、管理费用", assertion="完整性",
         nature="error", severity="中", disposition="需调整",
         evidence=["02_业务资料/社会保险缴纳情况表.csv", "02_业务资料/职工薪酬明细表.csv"],
         procedure="薪酬与社保交叉复核：计提基数 × 缴纳比例 vs 实缴数", auto_rule="payroll_recompute"),
    dict(id="E24", group="费用与负债", title="费用跨期：2026 年 1 月发生的服务费 7,200,000 于 2025-12-30 提前列支",
         amount="7200000", report_lines="管理费用、应付账款", assertion="截止",
         nature="error", severity="中", disposition="需调整",
         evidence=["01_账务资料/记账凭证_2025年度明细.csv"],
         procedure="费用截止测试：发票日期与记账日期比对", auto_rule="expense_cutoff"),
    dict(id="E25", group="费用与负债", title="大额预付款长期挂账：预付设备款 88,000,000 挂账超 2 年，设备未到货",
         amount="88000000", report_lines="预付款项、资产减值损失", assertion="存在/计价",
         nature="error", severity="中", disposition="需调整",
         evidence=["02_业务资料/预付款项账龄明细表.csv"],
         procedure="预付款账龄分析 + 供应商履约能力核查", auto_rule="aging_lookup"),

    # ===================== 实验组新增 E26–E32：治理、合并、税务、舞弊 =====================
    dict(id="E26", group="披露与列报", title="对外担保未披露：为关联方黔岭物流提供 300,000,000 借款担保，未在附注披露",
         amount="300000000", report_lines="或有事项附注", assertion="披露完整性",
         nature="disclosure", severity="高", disposition="仅披露",
         evidence=["02_业务资料/受限资产与担保台账.csv", "03_治理资料/董事会决议汇编_2025.md"],
         procedure="担保与或有事项检查：查询征信报告、董事会决议", auto_rule="disclosure_lookup"),
    dict(id="E27", group="关联方", title="关联方名单不完整：董事配偶持股 55% 的黔岭商贸未申报为关联方",
         amount="0", report_lines="关联方附注", assertion="披露完整性",
         nature="disclosure", severity="高", disposition="仅披露",
         evidence=["03_治理资料/组织架构与关键管理人员.md", "02_业务资料/关联方及关联交易清单_管理层提供.csv"],
         procedure="关联方完整性核查：董监高及其近亲属对外投资穿透", auto_rule="related_party"),
    dict(id="E28", group="披露与列报", title="关键管理人员薪酬未披露：全年 18,600,000 未按准则要求分项披露",
         amount="18600000", report_lines="关联方附注", assertion="披露完整性",
         nature="disclosure", severity="低", disposition="仅披露",
         evidence=["02_业务资料/职工薪酬明细表.csv"],
         procedure="关联方披露清单核对：关键管理人员薪酬", auto_rule="disclosure_lookup"),
    dict(id="E29", group="集团与合并", title="合并范围不当：持有黔岭商贸 55% 表决权并派驻董事，未纳入合并范围",
         amount="0", report_lines="合并财务报表范围", assertion="完整性",
         nature="error", severity="高", disposition="需调整",
         evidence=["01_账务资料/合并范围内子公司清单.csv", "02_业务资料/长期股权投资明细表.csv"],
         procedure="合并范围复核：表决权比例、董事会席位、实质控制判断", auto_rule="consolidation_scope"),
    dict(id="E30", group="集团与合并", title="内部往来不符：黔岭销售已付母公司货款 18,000,000，母公司未作收款记录",
         amount="18000000", report_lines="应收账款、应付账款", assertion="完整性/准确",
         nature="error", severity="中", disposition="需调整",
         evidence=["01_账务资料/科目余额表_P_黔岭酒业股份有限公司.csv",
                   "01_账务资料/科目余额表_S1_黔岭销售有限公司.csv"],
         procedure="内部往来对账：母公司账与子公司账逐笔核对", auto_rule="ic_reconcile"),
    dict(id="E31", group="费用与负债", title="费用虚列套取资金：向某咨询公司支付「管理咨询服务费」12,000,000，"
         "无合同、无成果交付、成立不足 3 个月",
         amount="12000000", report_lines="管理费用", assertion="存在/发生",
         nature="fraud", severity="高", disposition="需调整",
         evidence=["02_业务资料/主要供应商及付款流水.csv", "01_账务资料/记账凭证_2025年度明细.csv"],
         procedure="费用真实性检查：穿透合同、成果、供应商背景（新设、关联、无经营场所）", auto_rule="voucher_review"),
    dict(id="E32", group="收入与应收", title="阴阳合同：同一批货物存在两份销售合同（备案价 620,000,000 / 实际结算价 742,000,000），"
         "账务按低价结算",
         amount="122000000", report_lines="营业收入、应收账款", assertion="准确/发生",
         nature="fraud", severity="高", disposition="需调整",
         evidence=["02_业务资料/重大销售与采购合同台账.csv"],
         procedure="合同完整性检查：同一交易对手、同一期间的合同比对；函证实际结算金额", auto_rule="contract_clause"),
]


#: 需要生成新证据文件的清单（供 gen_experiment_data.py 使用）
NEW_FILES = [
    "02_业务资料/其他应收款明细表.csv",
    "02_业务资料/应收票据明细表.csv",
    "02_业务资料/存货盘点表.csv",
    "02_业务资料/固定资产盘点表.csv",
    "02_业务资料/资产减值测试表_管理层编制.csv",
    "02_业务资料/工程验收单摘要.md",
    "02_业务资料/房屋产权登记情况表.csv",
    "02_业务资料/研发项目台账.csv",
    "02_业务资料/期后付款与发票清单.csv",
    "02_业务资料/社会保险缴纳情况表.csv",
    "02_业务资料/预付款项账龄明细表.csv",
    "02_业务资料/受限资产与担保台账.csv",
    "02_业务资料/应收账款保理合同摘要.md",
    "02_业务资料/长期股权投资明细表.csv",
    "02_业务资料/期后销售退回清单.csv",
]


def by_rule() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for it in ISSUES:
        if it["auto_rule"]:
            out.setdefault(it["auto_rule"], []).append(it["id"])
    return out


def summary() -> dict:
    from collections import Counter
    return {
        "总数": len(ISSUES),
        "按性质": dict(Counter(i["nature"] for i in ISSUES)),
        "按严重程度": dict(Counter(i["severity"] for i in ISSUES)),
        "按处理方式": dict(Counter(i["disposition"] for i in ISSUES)),
        "需调整金额合计": sum(int(i["amount"]) for i in ISSUES if i["disposition"] == "需调整"),
        "可自动检出的项数": sum(1 for i in ISSUES if i["auto_rule"]),
    }


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    s = summary()
    for k, v in s.items():
        print(f"{k}: {v}")
    print("\n按规则分布：")
    for r, ids in sorted(by_rule().items()):
        print(f"  {r:<22} {len(ids):>2} 项  {','.join(ids)}")
