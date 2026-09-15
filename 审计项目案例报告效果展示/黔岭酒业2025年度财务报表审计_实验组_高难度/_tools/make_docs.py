# -*- coding: utf-8 -*-
"""生成高难度实验组的配套文档：

  _ground_truth/预期发现清单.md / .csv    —— 标准答案
  _ground_truth/AI胜任力评估矩阵.md        —— 这份数据到底能测出 AI 的什么
  _ground_truth/隐蔽性设计说明.md          —— 每处漏洞"怎么藏的"

运行：python make_docs.py
"""

from __future__ import annotations

import csv
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8")

from case_design import (ISSUES, NOISE, MATERIALITY, NATURE_CN, SAD_LIMIT,  # noqa: E402
                         TIER_LABEL, TIER_ORDER, by_group, by_rule, by_tier,
                         summary)

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(HERE, "_ground_truth")

#: 每层能力对 AI 的含义（写评估矩阵时用）
TIER_CAPABILITY = {
    "T1": ("规则可检", "会写并运行确定性程序即可；难点在于**想到要做累积评价**（SAD）"),
    "T2": ("跨表勾稽", "能把两张以上不同来源的表放在一起求差、逐笔匹配，而不是核对合计数"),
    "T3": ("比率与结构", "会做趋势与比率分析，并有『这个数应该长什么样』的先验"),
    "T4": ("准则与行业知识", "掌握收入确认总额/净额、新租赁准则、售后回购实质、"
                            "白酒消费税复合计征等具体规则，并主动拿规则去套业务"),
    "T5": ("职业判断与沟通", "能从红旗线索出发提出假设，再靠访谈、函证、工商查询取证；"
                            "结论无法由程序单独得出"),
}

#: 每层的"AI 可胜任性"结论
TIER_VERDICT = {
    "T1": "可稳定胜任", "T2": "可胜任，但易漏做", "T3": "可胜任，取决于分析主动性",
    "T4": "可胜任，取决于知识覆盖", "T5": "仅能发现线索，结论需人工",
}


def issue_rows_csv() -> list[list]:
    out = []
    for it in ISSUES:
        out.append([
            it["id"], it["tier"], TIER_CAPABILITY[it["tier"]][0], it["group"], it["title"],
            it["amount"], it["report_lines"], it["assertion"], NATURE_CN[it["nature"]],
            it["severity"], it["disposition"], it["stealth"], it["why_hard"], it["path"],
            "；".join(it["evidence"]), it["procedure"], it["auto_rule"] or "需人工判断",
            it["capability"],
        ])
    return out


def write_csv() -> None:
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "预期发现清单.csv"), "w", newline="",
              encoding="utf-8-sig") as fh:
        wr = csv.writer(fh)
        wr.writerow(["编号", "能力分层", "能力类型", "分组", "问题描述", "涉及金额",
                     "影响报表项目", "影响认定", "性质", "严重程度", "处理方式",
                     "隐蔽手法", "为什么难", "检出路径", "证据文件", "应实施程序",
                     "可自动检出规则", "需要的能力"])
        wr.writerows(issue_rows_csv())
    with open(os.path.join(OUT, "噪音项清单.csv"), "w", newline="",
              encoding="utf-8-sig") as fh:
        wr = csv.writer(fh)
        wr.writerow(["编号", "分组", "事项", "为什么不是漏洞", "证据文件"])
        for it in NOISE:
            wr.writerow([it["id"], it["group"], it["title"], it["why_noise"],
                         "；".join(it["evidence"])])


def write_findings_md() -> None:
    s = summary()
    L: list[str] = []
    A = L.append

    A("# 高难度实验组 · 预期发现清单（Ground Truth）")
    A("")
    A("> 本文件是**出题人的答案**，不对被测方披露。")
    A("> 本数据集埋入 **%d 处漏洞**，另混入 **%d 项无害噪音**，用于同时测"
      "『漏检』与『误报』。" % (s["总数"], s["噪音项数"]))
    A("")
    A("---")
    A("")
    A("## 一、总览")
    A("")
    A("| 口径 | 数值 |")
    A("| --- | --- |")
    A("| 漏洞总数 | **%d 项** |" % s["总数"])
    A("| 按性质 | 错报 %d ／ 舞弊 %d ／ 披露不充分 %d |"
      % (s["按性质"].get("error", 0), s["按性质"].get("fraud", 0),
         s["按性质"].get("disclosure", 0)))
    A("| 按严重程度 | 高 %d ／ 中 %d ／ 低 %d |"
      % (s["按严重程度"].get("高", 0), s["按严重程度"].get("中", 0),
         s["按严重程度"].get("低", 0)))
    A("| 按处理方式 | 需调整 %d ／ 仅披露 %d ／ 无需调整 %d |"
      % (s["按处理方式"].get("需调整", 0), s["按处理方式"].get("仅披露", 0),
         s["按处理方式"].get("无需调整（记录内控）", 0)))
    A("| **需调整错报金额合计** | **%s 元** |" % f"{s['需调整金额合计']:,}")
    A("| 重要性水平 | %s 元 ｜ 未更正错报（SAD）限额 %s 元 |"
      % (f"{int(MATERIALITY):,}", f"{int(SAD_LIMIT):,}"))
    A("| 可自动检出的规则数 | %d 条 |" % s["规则可自动检出"])
    A("")
    A("### 能力分层（数据集的难度结构）")
    A("")
    A("| 层级 | 含义 | 项数 | 编号 |")
    A("| --- | --- | --- | --- |")
    for t in TIER_ORDER:
        ids = by_tier()[t]
        A("| %s | %s | %d | %s |" % (TIER_LABEL[t], TIER_CAPABILITY[t][0],
                                     len(ids), "、".join(ids) or "—"))
    A("")
    A("**T3–T5 共 %d 项（%.0f%%）无法靠确定性规则检出**——这是本数据集与前一版实验组"
      "的根本差别：那一版测『程序会不会用规则』，这一版测『会不会做判断』。"
      % (sum(len(by_tier()[t]) for t in ("T3", "T4", "T5")),
         sum(len(by_tier()[t]) for t in ("T3", "T4", "T5")) / s["总数"] * 100))
    A("")
    A("---")
    A("")
    A("## 二、逐项清单")
    A("")
    A("| 编号 | 层级 | 问题 | 金额 | 性质 | 严重 | 处理 | 应实施程序 |")
    A("| --- | --- | --- | --- | --- | --- | --- | --- |")
    for it in ISSUES:
        A("| %s | %s | %s | %s | %s | %s | %s | %s |" % (
            it["id"], it["tier"], it["title"], f"{int(it['amount']):,}",
            NATURE_CN[it["nature"]], it["severity"], it["disposition"], it["procedure"]))
    A("")
    A("> 完整字段（隐蔽手法、为什么难、检出路径、证据文件、需要的能力）见"
      " `预期发现清单.csv`。")
    A("")
    A("---")
    A("")
    A("## 三、无害噪音（用来测『会不会乱报』）")
    A("")
    A("| 编号 | 事项 | 为什么不是漏洞 |")
    A("| --- | --- | --- |")
    for it in NOISE:
        A("| %s | %s | %s |" % (it["id"], it["title"], it["why_noise"]))
    A("")
    A("这 5 项的形状与漏洞高度相似：大额关联交易、会计估计变更、Q4 收入占比高、"
      "大额资金流出、减值准备转回。**凡是『见到某类事项就报』的做法，"
      "都会在这里产生误报。**")
    A("")
    A("---")
    A("")
    A("## 四、按分组")
    A("")
    A("| 分组 | 编号 |")
    A("| --- | --- |")
    for g, ids in by_group().items():
        A("| %s | %s |" % (g, "、".join(ids)))
    A("")
    A("---")
    A("")
    A("## 五、按可自动检出的规则")
    A("")
    A("| 规则 | 覆盖编号 |")
    A("| --- | --- |")
    for r, ids in sorted(by_rule().items()):
        A("| `%s` | %s |" % (r, "、".join(ids)))
    A("")
    A("注：这些「规则」指的是**规则明确、输入输出确定**的程序形态。本数据集里"
      "真正能由脚本直接命中的只有 T1 的 3 项；其余规则仍需先有人提出「要查什么」。")
    A("")

    with open(os.path.join(OUT, "预期发现清单.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(L))


def write_matrix_md() -> None:
    s = summary()
    L: list[str] = []
    A = L.append

    A("# 高难度实验组 · AI 胜任力评估矩阵")
    A("")
    A("这份数据集的用途是**测试 AI 能不能胜任审计作业中的判断部分**。"
      "下面把 24 处漏洞按『需要什么能力』分层，逐层给出结论。")
    A("")
    A("---")
    A("")
    A("## 一、结论速览")
    A("")
    A("| 层级 | 能力 | 项数 | AI 可胜任性 |")
    A("| --- | --- | --- | --- |")
    for t in TIER_ORDER:
        A("| %s | %s | %d | %s |" % (TIER_LABEL[t], TIER_CAPABILITY[t][0],
                                     len(by_tier()[t]), TIER_VERDICT[t]))
    A("")
    A("**分档结论：**")
    A("")
    A("- **确定性程序（不含 LLM）**：能查出 **3 项**（只占 12.5%），且必须有人告诉它查什么。")
    A("- **AI 有望独立完成**：T1–T4 共 **%d 项**（%.0f%%）——前提是它主动发起"
      "跨表核对、比率分析，并且掌握了相应准则。"
      % (sum(len(by_tier()[t]) for t in ("T1", "T2", "T3", "T4")),
         sum(len(by_tier()[t]) for t in ("T1", "T2", "T3", "T4")) / s["总数"] * 100))
    A("- **AI 只能提供线索、结论需人工**：T5 共 **%d 项**（%.0f%%）。"
      % (len(by_tier()["T5"]), len(by_tier()["T5"]) / s["总数"] * 100))
    A("")
    A("---")
    A("")
    A("## 二、逐层展开")
    A("")
    for t in TIER_ORDER:
        ids = by_tier()[t]
        A("### %s（%d 项：%s）" % (TIER_LABEL[t], len(ids), "、".join(ids)))
        A("")
        A("**需要的能力**：%s" % TIER_CAPABILITY[t][1])
        A("")
        A("**AI 可胜任性**：%s" % TIER_VERDICT[t])
        A("")
        A("| 编号 | 它要求 AI 做什么 |")
        A("| --- | --- |")
        for iid in ids:
            it = next(x for x in ISSUES if x["id"] == iid)
            A("| %s | %s |" % (iid, it["capability"]))
        A("")
        A("**这一层最常见的失败方式**：")
        for iid in ids[:3]:
            it = next(x for x in ISSUES if x["id"] == iid)
            A("- %s：%s" % (iid, it["why_hard"]))
        A("")
    A("---")
    A("")
    A("## 三、这份数据实际测的三层能力")
    A("")
    A("| 层次 | 名称 | 测什么 | 失败长什么样 |")
    A("| --- | --- | --- | --- |")
    A("| 第一层 | **程序能力** | 会不会用工具算数、取数、对账 | 连试算平衡都跑不出来 |")
    A("| 第二层 | **分析能力** | 会不会主动做跨表勾稽与比率分析 | 只核合计数，不核分项；不做趋势 |")
    A("| 第三层 | **意识能力** | 会不会**想到**去查某张表、某项准则 | 工具都会用，但根本没去查 |")
    A("")
    A("本数据集的设计靶心是**第三层**。前两层的失败是「能力不足」，"
      "第三层的失败是「不知道自己不知道」——而这恰恰是当前 AI 在专业作业中最主要的短板。")
    A("")
    A("---")
    A("")
    A("## 四、建议的评分方式")
    A("")
    A("用同一份资料跑被测对象，按下列口径打分：")
    A("")
    A("| 指标 | 算法 | 说明 |")
    A("| --- | --- | --- |")
    A("| 总体召回率 | 检出数 ÷ %d | 主要指标 |" % s["总数"])
    A("| 分层召回率 | 各层检出数 ÷ 各层项数 | **看它在哪一层断崖** |")
    A("| 误报率 | 报出但不在清单的项数 ÷ 报出总数 | 用 5 项噪音 + 27 项真实业务事项检验 |")
    A("| 证据充分性 | 每条发现是否附『文件 + 关键数字 + 计算过程』 | 只报结论不给路径的，不计分 |")
    A("| 方向正确性 | 对 H03 这类双向错报，是否**分别列示**超提与少提 | 只报净额视为未检出 |")
    A("")
    A("**建议的判定门槛**：")
    A("")
    A("- 分层召回里 T1 全中、T2/T3 中 ≥60% → 具备**执行型**胜任力；")
    A("- T4 中 ≥50% → 具备**知识型**胜任力；")
    A("- T5 能提出正确假设并说明需何种取证 → 具备**判断型**胜任力；")
    A("- 误报率 >20% → 无论召回多高，都不能算胜任。")
    A("")
    A("---")
    A("")
    A("## 五、必须先讲清楚的前提")
    A("")
    A("1. **本数据集不测「能不能把账做平」**——账本来就是平的，试算平衡一项也查不出来。")
    A("2. **不测「能不能读懂表格」**——所有表格都是规整的 CSV/Markdown。")
    A("3. **不测「知不知道审计流程」**——问「应当实施什么程序」人人都答得出；")
    A("   这份数据测的是**有没有真的去执行**，以及执行得够不够细。")
    A("4. 5 项噪音与 27 项真实业务事项（正常的销售、采购、折旧、税缴、股利、借款）"
      "共同构成误报测试面。**只报噪音的模型，比漏检的模型更危险。**")
    A("")

    with open(os.path.join(OUT, "AI胜任力评估矩阵.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(L))


def write_stealth_md() -> None:
    L: list[str] = []
    A = L.append
    A("# 高难度实验组 · 隐蔽性设计说明")
    A("")
    A("这份数据与前两版最大的差别：**资料里没有任何标记**。")
    A("`_tools/selfcheck.py` 会扫描全部 51 个资料文件，确认不含 ★、"
      "『应提/核对结果/异常/错误』等提示性用语。")
    A("")
    A("下面说明每一处漏洞**具体是怎么藏的**。")
    A("")
    A("| 编号 | 藏在哪儿 | 为什么单看一张表看不出来 |")
    A("| --- | --- | --- |")
    for it in ISSUES:
        A("| %s | %s | %s |" % (it["id"], it["stealth"], it["why_hard"]))
    A("")
    A("---")
    A("")
    A("## 六种隐蔽手法")
    A("")
    A("1. **跨表错配**（H01、H02、H07）：每张表的合计数都对得上总账，"
      "差异只在两张不同来源的表的**分项**之间。")
    A("2. **双向抵消**（H03）：同一项目上既有超提又有少提，净影响 451,250 元，"
      "低于重要性水平 1,500,000 元 —— 只核总额一定放行。")
    A("3. **金额稀释**（H14、H15、H16）：单笔都低于 SAD 限额 75,000 元，"
      "分散在不同月份、不同摘要里。")
    A("4. **形式合规掩盖实质**（H09、H10、H13）：账、单、票齐全，会计处理在旧准则下"
      "毫无问题；要靠**读合同条款**或**套新准则**才能发现。")
    A("5. **披露遗漏**（H17、H18、H19、H20、H21）：账务处理全对，"
      "问题在『没说的话』。H21 更细：附注**已经列了**前五名客户金额，"
      "缺的是判断性表述。")
    A("6. **线索藏在非结构化文本里**（H18、H19、H20、H24）：担保写在决议第四条、"
      "并购写在总经理办公会纪要、败诉写在「上诉后确定」的措辞里、"
      "关联方要穿透两层股权。")
    A("")
    A("## 三处「看起来会报、其实不该报」的设计")
    A("")
    A("| 编号 | 像什么 | 为什么不该报 |")
    A("| --- | --- | --- |")
    for it in NOISE:
        A("| %s | %s | %s |" % (it["id"], it["title"], it["why_noise"]))
    A("")

    with open(os.path.join(OUT, "隐蔽性设计说明.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(L))


def main() -> None:
    os.makedirs(OUT, exist_ok=True)
    write_csv()
    write_findings_md()
    write_matrix_md()
    write_stealth_md()
    for f in sorted(os.listdir(OUT)):
        print("  已生成", f)


if __name__ == "__main__":
    main()
