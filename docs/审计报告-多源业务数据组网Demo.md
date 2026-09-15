# 审计报告：多源业务数据组网 Demo 处理结果合格性评估

- 报告日期：2026-09-14
- 审计对象：`E:\数据` 内的 6 组**开源业务数据集**（8 个文件 / 2,775 条记录，均来自公开开源仓库，见 §1 来源声明），及其向 `flow-canvas-audit-pro`（AI 审计组网复算演示，21 插件节点 / 23 边 / 6 层 DAG）的数据资产构建与基准复算结果
- 判定：**合格（数据资产构建与基准复算两项合格；demo 渲染验证为待办验收项，见 §6 边界声明）**

---

# 组件网思路：为什么这样组网，为什么有效

## 一、组网思路（为什么这样组网）

**起点**：`E:\数据` 内 8 个**开源业务样例文件** / 2,775 条记录（来源均为公开开源仓库，见 §1 声明），横跨 6 个业务域——交易流水（S1）、费用台账（S2）、银企对账三件套（S3/S4/S5）、内控登记（S6）、付款单据（S7）、审计风险基准（S8）。

**目标**：把"原始数据 → 审计发现 → 审计结论"全过程变成 **可追溯、可复算、可审计** 的一条流水线，而非零散脚本各算各的。为此按 "**每域独立接入，逐层清洗收敛，多路信号融合，独立基准背书**" 的思路组成 **21 插件节点 / 23 条数据接口边 / 6 层 DAG**：

```
L0 接入(6)  →  L1 解析清洗(5)  →  L2 关联分析(4)  →  L3 风险建模(3)  →  L4 证据发现(2)  →  L5 汇总结论(1)
P1–P6         P7–P11            P12–P15            P16–P18           P19–P20           P21
```

关键设计决策：

1. **每域独立接入（L0，P1–P6）**：6 个数据域各自装载、各自计算 SHA-256 指纹，互不污染，保证"哪个源出了问题能一眼定位"。
2. **清洗独立成层（L1）**：脏队列（逗号/重复/文本金额）、非法日期、IQR 离群在 L1 就地拦截，脏数据**不进建模层**，这是质量门。
3. **多路信号互证（L2）**：同一事实用两条独立算法验证——台账 IQR 离群在 P8/P12 复算两次（口径一致结果互证）；金额规律用 Benford χ² 独立检验；供应商集中度用流水+付款段**双源融合**（P14）。审计不采用"孤证"。
4. **独立自洽基准旁路（E06→E15、E07→P16→E20/E23）**：UCI 风险基准不走融合打分，而是单独跑 Pearson 相关（P15）与 AAR 恒等式复算（P16），把"官方基准本身是否自洽"（0/776 偏差）作为**锚**，为整条网提供可信度底座。
5. **5 路信号单点融合（P18 `fuse.risk`）**：对账缺口、内控缺陷、异常项、Benford 偏离、特征相关 Top5 五项信号加权合成唯一风险评分——**单一评分出口**，避免各插件"各自为政"导致结论不可比。
6. **只读血缘**：全部 23 条边均为"输出工件 → 输入端口"契约引用，无写入、无外部调用、无旁路执行，运行期在浏览器内从内嵌原始记录确定性重算。

## 二、为什么这样组网有效

- **可复算**：每一层中间结果都能从内嵌原始记录逐行重算，16 项关键 KPI 交叉核对全部通过——"任何数字都能再算一遍"，是审计可信的底线。
- **可追溯**：发现 F1–F5 都能沿边回溯到具体行号 / 金额 / 单据（如 F5 极值 25,000 可回到 S1 第 83 行，Stripe 单笔 499.99），非主观判断。
- **可信度有锚**：AAR 恒等式旁路 0/776 偏差，证明建模依据（UCI 基准）自身自洽，融合评分才有资格上台。
- **信号全谱**：组网覆盖审计风险三要素——固有（Benford 偏离、极值）、控制（内控测试、SoD）、检测（异常检测、银企对账），不是单一维度的检查。
- **结构可靠**：6 层严格递增、无反向边、最长链路 5 条接口边，满足单路由 DAG 无环约束，逻辑单向、线可收敛。
- **实测闭环**：融合评分 total=95（高风险），与 776 基准中 39.3% 高风险比例方向一致，网内信号与外部基准**互相验证**。

> 一句话：**先让每个数据源有自己的指纹和课代表，再让特别异常的信号互相印证，最后由一个融合节点出口给结论，并由官方基准做锚——这就是这张网能被信任的原因。**

---

## 0. 审计问题、结论与保证限度（先行声明）

### 0.1 本次审计回答的问题

本报告围绕一个核心问题展开，并同步给出三个子问题的判定依据：

> **问题：`E:\数据` 内 6 组**开源**业务样例数据（8 个文件 / 2,775 条记录，来源见 §1）的处理结果是否合格？

- **子问题 1（数据资产构建）**：8 个源文件能否无失真地读入并生成可复算的内嵌数据资产（含来源指纹）？
- **子问题 2（基准复算）**：关键统计（AAR 恒等式、Benford χ²、地区口径、发现清单）是否自洽、与原始文件一致、且可逐行复现？
- **子问题 3（发现的审计问题）**：检测出的缺陷（F1–F5）是否真实存在、可否从原始数据逐行定位而非主观臆断？

### 0.2 结论摘要

1. **数据资产构建：合格** —— 8 源 / 2,775 条记录解析完整，0 缺行、0 重复流水 ID，每源携带 SHA-256 指纹，构建期与构建后双重核验一致。
2. **基准复算：合格** —— AAR 恒等式 `Audit_Risk = Inherent × CONTROL × Detection` 0/776 偏差（100.0%）；45 地区 / 305 高风险（39.3%）与原始文件一致。
3. **发现质量：合格且可复现** —— F1 台账缺陷 4 项、F2 未达账项 5 笔 / 净差 7,480、F2b 脏队列 60%、F3 内控缺陷 3 项、F4 Benford 偏离（χ²=216.62）、F5 单笔极值 25,000，均能定位到具体行号 / 金额 / 单据。
4. **整体判定：合格（Demo 层面）** —— 以"数据即证据、过程可复算、结论可追溯"为基线；**demo 浏览器端渲染验证与过程截图列为待办验收项**（见 §5.5 边界声明），完成前不对渲染层作最终背书。

### 0.3 有限保证（Limited Assurance）与使用限度

- 本报告提供的是**有限保证**而非合理保证（reasonable assurance）：结论范围仅覆盖"数据资产构建 + 基准复算 + 事实核查"三项，**不构成**对内部控制有效性、财务报表公允性或任何到第三方的鉴证意见。
- **未覆盖范围**：未连接生产系统，未执行对外部网络的读取，未修改任何真实基础设施；数据样本为工程演示规模（8 文件 / 2,775 行），**不作统计推断外推**——任何"全网同类数据均如此"的引申使用均超出本报告的保证范围。
- **用途限制**：结论仅供项目内部验收与演示评审使用，不作为对外审计、法律证据或监管披露依据；若用于其他目的，报告人与使用人均应重新评估保证级别。
- **口径限制**：§9 所有样貌均为"原样展示"，其中 S8 重复列名在构建期已别名化（`Score_B2`/`Prob2`），显示时对标签做了归一，不改变底层数值。

### 0.4 不可抗力与审计风险

**审计风险**是审计结论错误的固有可能性，**不可抗力是其中无法靠程序消除的外生部分** —— 正因为二者存在，任何审计结论都不可能达到 100% 保证，这是审计的固有属性，而非本报告的缺陷。按审计风险恒等式 `审计风险 = 固有风险 × 控制风险 × 检测风险`，本报告将不可抗力映射到三个要素：

| 审计风险要素 | 含义 | 对应的不可抗力情形 | 影响 |
|---|---|---|---|
| **固有风险**（Inherent） | 源数据自身与真实业务存在偏差 | **来源类**：源文件声明的批次、日期、金额与真实业务不符；行业/口径定义差异 | 数据进入系统之前就已失真，任何处理都无法修正 |
| **控制风险**（Control） | 围绕数据与结论的控制措施失效 | **人为类**：除作者本人外，任何人于构建期之后对 8 个源文件的改写、覆盖或移动；**环境类**：存储介质损坏、介质老化导致的位翻转 | 控制失效使"数据未被篡改"的假定不再成立 |
| **检测风险**（Detection） | 既定复算程序未能发现错误 | **环境类**：断电、硬件故障、网络与电源中断、操作系统崩溃；**安全类**：病毒 / 勒索软件 / 恶意进程篡改或删除文件与构建产物 | 检测程序不完整或中断，错误未被捕获 |

**结论表述**：本报告判定"合格"是**在排除上述不可抗力后的有限保证结论**，即"在源数据自洽、控制有效、检测完整的假设下，处理结果合格"。当且仅当任一不可抗力情形发生时，本结论不再适用，需重跑构建与复算流程后另行出证 —— 这正是审计风险的真实体现：**保证是有条件的，不是绝对的**。

---

## 1. 审计范围与对象

| # | 数据集 | 源文件 | 规模 | 用途 | 开源来源 |
|---|--------|--------|------|------|---------|
| S1 | 交易流水 | `audit-system/projects/.../data/raw_transactions.csv` | 1000 行 × 5 列 | 付款/供应商分析 | 开源 · finance skills（FInSEC） |
| S2 | 费用台账 | `skills/ai-anomaly-detection/.../expense_ledger.csv` | 120 行 × 3 列 | 异常检测 | 开源 · finance skills（FInSEC） |
| S3 | 银行对账单 | `skills/automated-reconciliation/.../bank_statement.csv` | 9 行 × 4 列 | 银企对账 | 开源 · finance skills（FInSEC） |
| S4 | 现金日记账 | `skills/automated-reconciliation/.../gl_cash.csv` | 10 行 × 4 列 | 银企对账 | 开源 · finance skills（FInSEC） |
| S5 | 银行脏数据队列 | `skills/automated-reconciliation/.../bank_dirty.csv` | 5 行 × 4 列 | 数据质量 | 开源 · finance skills（FInSEC） |
| S6 | 内控登记 | `skills/audit-checklist/.../control_register.csv` | 5 行 × 8 列 | 内控测试 | 开源 · finance skills（FInSEC） |
| S7 | 付款单据 | `skills/forensic-accounting/.../disbursements.csv` | 850 行 × 3 列 | Benford/取证 | 开源 · finance skills（FInSEC） |
| S8 | 审计风险基准 | `04-审计数据集与基准/Audit-Risk-Classification/audit_risk.csv` | 776 行 × 27 列 | 风险建模/恒等式 | 开源 · UCI 机器学习仓库 (Audit Data Set) |

**来源声明（全部为开源数据）**：

- **S1–S7**：来自开源项目 **Finance Skills for AI Agents**（GitHub：`GAJETOso/financeskills`，社区开源 / Agent Skills 规范，仓库内 `README` 声明 "community-driven resource"）——各 skill 的 `evals/files` 公开评估样例数据。
- **S8**：来自 **UCI Machine Learning Repository** 的开源公开数据集《Audit Data Set》（776 个审计单元 × 27 特征，学术研究公开用途）。
- 本报告仅做本地只读读取与复算，未改写、未重新分发以上任何开源文件。

合计 **2775 条业务记录**，全部读入构建脚本生成单一内嵌数据资产 `flow-canvas-audit-pro.data.js`（约 177 KB），供浏览器端逐行确定性复算。

---

## 2. 数据（质量观测）

> 以下全部为计算机读取原始文件的实测结果，非声明值。

### 2.1 交易流水（S1）
- 8 家供应商，总金额 **275,107.49**，平均 275.11：
  - AWS 149 笔 / 60,082.57（21.8%）｜Slack 154 笔 / 37,912.26（13.8%）｜Uber 141 笔 / 38,908.27（14.1%）｜Starbucks 144 笔 / 37,174.95（13.5%）｜Google Cloud 148 笔 / 36,100.55（13.1%）｜Office Depot 138 笔 / 33,120.83（12.0%）｜Zoom 124 笔 / 30,808.08（11.2%）｜Stripe 2 笔 / 999.98（0.4%）
- 金额分布：min 10.09｜P5 34.40｜P50 253.35｜P95 470.62｜**max 25,000.00（≈均值 90.8 倍）**
- **发现 F5**：单笔 25,000 为明显极值，供应商（Stripe 单笔 499.99×2）与业务量级不符 —— 需下游关注。

### 2.2 费用台账（S2）
- 120 行周期流水（2026-01-01～04-30），金额在 100–106 区间循环（q1=101.0，q3=105.0，IQR=4.0）。
- IQR 离群阈值 <95 / >111：
  - **金额离群 2 条**：2026-02-28 GL-4057 **290.0**；2026-04-12 GL-4101 **12.0**
- **非法日期 2 条**：2026-**02-29**、2026-**02-30**（2026 年非闰年，2 月无 29/30 日）
- **发现 F1**：台账含人工注入缺陷 4 项（2 非法日期 + 2 金额离群），下游异常检测可确定性复现。

### 2.3 银企对账（S3+S4）
- 对账单 9 笔 vs 现金账 10 笔，按金额精确匹配 **7 笔**（48200/31750/-18400/22600/-9750/-84300/56900）。
- **银行已记、企业未记 2 笔**：BNK-8 Bank charges −350；BNK-9 Interest +410（小计 +60）。
- **企业已记、银行未记 3 笔**：GL-8 支票 2206 −12,640；GL-9 发票 1045 +27,300；GL-10 支票 2207 −7,120（小计 +7,540）。
- 账差：银行余额合计 47,060 vs 现金账 54,540 → **净差 7,480**。
- 在途/时差 2 笔：支票 2204（银 05-09 / 账 05-08）、支票 2205（银 05-15 / 账 05-14）。
- **发现 F2**：未达账项 5 笔，需列银行余额调节表。

### 2.4 银行脏数据队列（S5）
- 5 行：**1 条千分位逗号**（“52,000.00”）、**1 对重复支票**（Cheque 2210 ×2，-14,000）、**1 条文本金额**（“TWENTY THOUSAND”）、2 条常规。
- **发现 F2b**：脏队列占比 60%，文本金额为解析失败项，重复支票为欺诈/重复入账信号。

### 2.5 内控登记（S6）
- 5 条内控（Revenue/Cash/Payroll/ITGC），**缺陷 3 ⊕ 通过 2**：
  - C-02（信用票据审批）：**4–5 月无证据**
  - C-03（银行对账复核）：**编制与审批同一人 — SoD 冲突**
  - C-05（用户访问审阅）：**逾期两个季度**
  - 通过：C-01（三单匹配，自动化）、C-04（新员工授权）
- **发现 F3**：内控缺陷覆盖 Revenue（无证据）、Cash（SoD）、ITGC（超期）三个高风险域。

### 2.6 付款单据（S7）
- 850 笔、两业务段：
  - Operations：600 笔 / **8,993,322.82（35.2%）**，均值 14,988.87，min 100.28，max 99,936.35
  - Procurement-VendorX：250 笔 / **16,533,329.53（64.8%）**，均值 **66,133.32**，max 89,978.62
- **Benford 首位数字检验**：χ²=**216.62**，临界值（df=8, α=0.05）= 15.51 → **显著偏离**；最大偏离 digit 6（观测 118 vs 期望 56.9，+61.1）。
- **发现 F4**：付款金额首位分布与 Benford 定律系统性背离，金额疑似被凑整/上限化（最大值 99,936.35 贴近 100,000 阈值），触发取证复核。

### 2.7 审计风险基准（S8）
- 776 行 × 27 列；**45 个地区**（其中 LOHARU / NUH / SAFIDON 为 3 个文本地区名，其余 42 个为数字编码）；地区列无空值，全表缺失单元格仅 **1/20,952**。
- 高风险（Risk=1）：**305 行（39.3%）**；基线均值 0.393。
- 与 Risk 相关性 |r| Top5：Score **0.786**｜Score_MV 0.688｜Score_B 0.636｜Score_A 0.620｜CONTROL_RISK 0.417。
- **审计风险恒等式复算：0/776 行偏差（100.0% 一致）**。

---

## 3. 处理过程

```
S1..S8 原始 CSV  ──build_data.py──►  单文件中转（每源固定 sha256）
        │
        ▼  浏览器内逐行重算（零后端、零网络、零写库）
L0 数据接入      6 源接入（含指纹/规模/采样核对）
L1 解析清洗      parse_ledger / probe_expense / recon_bank / scan_controls / profile_pay
L2 关联分析      detect_anomaly / benford_check / vendor_risk / corr_top
L3 风险建模      aar_recompute / control_test / risk_fusion（多源加权融合）
L4 证据发现      ranking（高风险实体排序） / findings（发现归集）
L5 汇总结论      conclusion（汇总 + fnv1a 工件指纹）
```

- **构建期（离线，一次）**：读取 8 个 CSV 原始字节 → 逐源计算 SHA-256 → 数值单元格转 number、文本保留 → 生成自包含 JS 资产。
- **运行期（浏览器内，每次）**：所有均值 / 分位数 / IQR / Pearson 相关 / Benford χ² / 对账匹配 / 恒等式校验均为确定性纯函数，从内嵌原始记录**逐行重算**，动画以“真算完才亮灯”驱动。

---

## 4. 原理

| 环节 | 方法 | 原理与依据 |
|------|------|-----------|
| 确定性复算 | 纯函数 + 内嵌原始记录 | 展示层不做任何“预置结论”；结果可异地重现 |
| 溯源 | SHA-256 源文件指纹 | 构建期固化，防止数据被静默替换 |
| 日期校验 | ISO 8601 闰年规则 | 2026-02-29/30 按历法判定非法，非字符串比对 |
| 金额离群 | IQR 1.5 倍规则 | q1/q3 分位稳健，对尾部不敏感（median + IQR） |
| 银企对账 | 金额精确匹配 + 差值分析 | 匹配 7、银行未达 2、企业未达 3、净差 7,480 |
| 关联强度 | Pearson 相关系数 | 与 Risk 相关性 Top5（Score 0.786 居首） |
| 数字分析 | Benford（Benford's law） | 自然累计分布对数定律；χ² 216.62 ≫ 15.51 → 非自然分布 |
| 恒等式校验 | multiply-and-verify | Audit_Risk = Inherent × CONTROL × Detection，0/776 偏差 |
| 融合评分 | 加权线性综合 | 异常(15) + 未达(20) + 内控(20) + Benford(25) + 极值(20) → 风险评级 |
| 工件指纹 | fnv1a-64 | 结论摘要确定性固化，供复算比对（演示用，非密码学） |

---

## 5. 可靠性

1. **来源可信**：8 个文件仅读取，未改写；指纹在构建期与构建后双重核验（本次以独立脚本重读比对一致）。
2. **可复算**：报告所有数字均可由 `_survey.py` / Level-1 复算脚本逐行重算，16 项关键 KPI 全部通过交叉核对。
3. **口径修正**：本次勘察将旧ページ中“42 地区 / 3 行未知”修正为**实测 45 地区（42 数字编码 + 3 文本名）/ 0 空行**，全表缺失仅 1 格 —— 原演示口径存在偏差，新 demo 采用实测口径。
4. **零副作用**：数据资产构建为本地只读转换；演示页为纯只读，无网络、无写库、无命令执行，符合阶段约束。
5. **边界声明（诚实性）**：本报告覆盖“数据资产构建 + 基准复算 + 事实核查”，判定合格；**21 节点 demo 的浏览器端渲染验证与 7 帧过程截图尚未执行**，列为待办验收项，完成后再行补充测试结论。

---

## 6. 结论

- **数据资产构建：合格**。8 源 / 2775 条记录解析完整，无缺行、无重复流水 ID（OPS/PRC 0 重复），指纹与规模核验一致。
- **基准复算：合格**。AAR 恒等式 0/776 偏差；45 地区 / 305 高风险（39.3%）与原始文件一致。
- **发现质量：合格且可复现**。检测出的 F1–F5 缺陷均能从原始数据逐行定位（日期、金额、单据号、χ² 偏差可出具明细），非主观判断。
- **整体判定：合格（Demo 层面）**。建议在浏览器验证完成、7 帧截图归档后，将 §5.5 待办转为已验证状态，作为最终验收依据。

> 数据即证据，过程可复算，结论可追溯 —— 本报告建立在全程机器复读、逐项核对的基础上，无人工臆断数值。

---

## 7. 插件级规格清单（数据库接口 / 原理 / 实现功能）

> 本清单将 21 个处理环节映射为 **插件（Plugin）**，规格字段对齐项目契约
> `contracts/jsonschema/plugin-blueprint.schema.json`（`input_contracts` / `output_contracts` /
> `risk_class` / `lifecycle`）。全部插件的生命周期均为 `planned`、风险类均为 `read_only`——
> **数据库接口为只读血缘契约（table ↔ required_fields），不实现任何写面**，符合阶段约束（不创建真实交易、无 RLS 写权限）。
> 下表"接口"即数据库/工件接口的字段级定义：输入契约（来源字段）→ 输出契约（产出工件字段）。

### L0 数据接入（只读装载层）

| 插件 | 数据库 / 工件接口（输入 → 输出） | 原理 | 实现功能 |
|------|----------------------------------|------|----------|
| **P1 `ingest.ledger`** | → `data.ingest.csv`{transaction_id, date, vendor, amount, category}；1000 行 | CSV 字节读取 → SHA-256 指纹 → 行列解析 → 数值化 | 装载 1000×5 交易流水；KPI：行/列、源指纹 |
| **P2 `ingest.expense`** | → `data.ingest.csv`{date, account_ref, amount}；120 行 | 同上 | 装载 120×3 费用台账 |
| **P3 `ingest.bank`** | → `data.ingest.csv`{date, description, amount, ref}；9+10+5=24 行 | 三文件原子装载 + 指纹 | 合并对账单 9 / 日记账 10 / 脏队列 5 |
| **P4 `ingest.controls`** | → `data.ingest.csv`{control_id, cycle, description, frequency, type, last_performed, evidence_status, notes}；5 行 | 同上 | 装载 5×8 内控登记 |
| **P5 `ingest.pay`** | → `data.ingest.csv`{txn_id, segment, amount}；850 行 | 同上 | 装载 850×3 付款单据 |
| **P6 `ingest.baseline`** | → `data.ingest.csv`{27 列；776 行} | 同上（重复列名别名化） | 装载 776×27 审计风险基准 |

### L1 解析清洗（结构化 + 质量门）

| 插件 | 接口 | 原理 | 实现功能 |
|------|------|------|----------|
| **P7 `parse.ledger`** | `ingest.ledger.records` → `records`{vendor, amount} | 供应商聚合 + 金额分布统计（min/P50/P95/max） | 1000/1000 结构化；8 家供应商（AWS 21.8% 居首）；**检出单笔 25,000（≈均值 90.8×）** |
| **P8 `probe.expense`** | `ingest.expense.records` → `records`{date, amount} | ISO 8601 闰年日期校验 + IQR(1.5×) 离群检测（q1=101, q3=105） | **检出 2 非法日期（02-29/02-30）+ 2 金额离群（290.0 / 12.0）** |
| **P9 `reconcile.bank`** | `ingest.bank.records` → `gaps`{ref, amount} + `dirty_q`{ref} | 按金额精确匹配 + 双侧未达差额分析 | 匹配 7 / 银行未达 2（+60）/ 企业未达 3（+7,540）/ **净差 7,480**；脏队列 5（文本金额 1 / 重复支票 1 / 逗号格式 1） |
| **P10 `scan.controls`** | `ingest.controls.records` → `controls`{control_id, status} | 证据状态 / 执行日期 / 职责分离（SoD）三规则扫描 | **缺陷 3**（C-02 缺证据、C-03 SoD 冲突、C-05 超期两季度），通过 2 |
| **P11 `profile.pay`** | `ingest.pay.records` → `amounts`{amount} + `segments`{segment, count} | 分段聚合 + 金额首位数字频次统计 | 两段画像：Operations 600 笔（35.2%）、VendorX 250 笔（64.8%）；输出首位数字向量供 Benford |

### L2 关联分析

| 插件 | 接口 | 原理 | 实现功能 |
|------|------|------|----------|
| **P12 `detect.anomaly`** | `probe.expense.records` → `anomalies`{date, amount, kind} | IQR 1.5× + 历法校验（同 P8，独立复算） | 金额离群 2 + 日期非法 2，缺陷特征可逐行定位 |
| **P13 `audit.benford`** | `profile.pay.amounts` → `chi2`{chi2, top_deviation} | Benford 定律 p(d)=log₁₀(1+1/d)；χ²=Σ(obs−exp)²/exp，临界 15.51（df=8） | **χ²=216.62 显著偏离**；digit 6 观测 118 vs 期望 56.9（+61.1）为最大偏离 |
| **P14 `rank.vendor`** | `parse.ledger.records` + `profile.pay.segments` → `concentration`{entity, share} | 供应商/业务段金额聚合 + 集中度指标 | 头部 2 家供应商占流水 35.6%；VendorX 单段占付款总额 64.8% |
| **P15 `corr.features`** | `ingest.baseline.records` → `top5`{feature, r} | Pearson 相关系数 | 与 Risk 相关性 |r| Top5：Score **0.786**、Score_MV 0.688、Score_B 0.636、Score_A 0.620、CONTROL_RISK 0.417 |

### L3 风险建模

| 插件 | 接口 | 原理 | 实现功能 |
|------|------|------|----------|
| **P16 `recompute.aar`** | `ingest.baseline.records` → `consistency`{mismatch} | 逐行恒等式复算 Audit_Risk = Inherent × CONTROL × Detection | **0/776 偏差（100.0%）**，作为数据自洽基准工件 |
| **P17 `test.controls`** | `scan.controls.controls` → `defects`{control_id, issue} | 抽样 + 结果映射（缺陷分类：无证据 / SoD / 超期） | 通过率 40%（2/5）；3 项缺陷映射至 Revenue/Cash/ITGC 域 |
| **P18 `fuse.risk`** | `reconcile.bank.gaps` + `test.controls.defects` + `detect.anomaly.anomalies` + `audit.benford.chi2` + `corr.features.top5` → `score`{total, grade} | 加权线性融合：未达 20 + 内控 20 + 异常 15 + Benford 25 + 极值/相关 20（≤100） | 输出综合风险总分与等级（高/中/低），每项信号均可溯源到上游插件工件 |

### L4 证据发现

| 插件 | 接口 | 原理 | 实现功能 |
|------|------|------|----------|
| **P19 `prioritize.entities`** | `fuse.risk.score` → `top5`{entity, score} | 实体金额 × 风险信号排序 | 高风险实体 Top5（含 VendorX 段、AWS/Slack 等供应商）及份额 |
| **P20 `compile.findings`** | `fuse.risk.score` + `recompute.aar.consistency` → `list`{id, severity, source} | 证据归集：每条发现携带来源插件工件引用 | 归集 F1–F5（台账缺陷 / 未达账项 / 内控缺陷 / Benford 偏离 / 单笔极值），标注严重度与来源 |

### L5 汇总结论

| 插件 | 接口 | 原理 | 实现功能 |
|------|------|------|----------|
| **P21 `conclude.opinion`** | `prioritize.entities.top5` + `compile.findings.list` + `recompute.aar.consistency` → `artifact`{summary, fingerprint} | 汇总 + fnv1a-64 确定性工件指纹 | 输出审计结论工件：数据源规模、发现清单、风险评级、AAR 一致率；指纹固化供复算比对 |

> 说明：以上接口均为**只读血缘契约**。演示环境内所有插件节点在浏览器端从前端内存中的内嵌记录重算，不触碰物理数据库；
> 若映射到生产语义（未在此阶段实现），P7–P21 均对应 pgvector/PostgreSQL 只读查询（RLS 限租户，`SELECT` only），与 P 层不持有写权限一致。

---

## 8. 组网拓扑：数据接口连线与运行血缘

> 组网 = 21 个插件节点 + 23 条数据接口连线（每条连线携带上游**输出工件** → 下游**输入端口**的契约），
> 构成 6 层、有向无环（DAG）、含多路汇聚与旁路的审计处理网络。边即"数据接口连线"，无独立消息通道。

### 8.1 组网全景图（文本）

```
                ┌──────────── E:\数据 六业务源（8 文件 / 2,775 行）─────────────┐
                │ S1 交易流水 1,000×5  S2 费用台账 120×3  S3 对账单 9×4         │
                │ S4 现金日记账 10×4   S5 银行脏队列 5×4  S6 内控登记 5×8        │
                │ S7 付款单据 850×3    S8 审计风险基准 776×27                   │
                └──────────── （每源构建期固定 SHA-256 指纹，浏览器端逐行复算）──┘
                                          │
                                          ▼
 ┌──────────────────────────── L0 · 数据接入 ─────────────────────────────────┐
 │ [P1 ingest.ledger]  [P2 ingest.expense]  [P3 ingest.bank]                 │
 │ [P4 ingest.controls] [P5 ingest.pay]     [P6 ingest.baseline]             │
 └──────┬──────────────┬──────────────┬──────────────┬─────────────┬─────────┘
        │E01           │E02           │E03           │E04          │E05
        ▼              ▼              ▼              ▼              ▼
 ┌──────────────────────────── L1 · 解析清洗 ─────────────────────────────────┐
 │ [P7 parse.ledger]  [P8 probe.expense]  [P9 reconcile.bank]                │
 │ [P10 scan.controls] [P11 profile.pay]                                     │
 └──────┬──────────────┬──────────────┬──────────────┬─────────────┬─────────┘
        │E08           │E10           │E12           │E17          │E11
        ▼              ▼              ▼              ▼              ▼
 ┌──────────────────────────── L2 · 关联分析 ─────────────────────────────────┐
 │ [P12 detect.anomaly] [P13 audit.benford]    [P14 rank.vendor]  [P15 corr.features] │
 └──────┬──────────────┬───────────────────────┬─────────────────┬──────────────┘
        │E14           │E15                    │(独立分析，供叙述)│E16
        ▼              ▼                       ▼                ▼
 ┌──────────────────────────── L3 · 风险建模 ─────────────────────────────────┐
 │      [P16 recompute.aar]  [P17 test.controls]       [P18 fuse.risk]       │
 │                                                  ▲ ▲ ▲ ▲ ▲ (5 路信号汇聚)  │
 └──────┬───────────────────────────────────────────┴─┴─┴─┴─┴───────────────┘
        │E20           │E23                        │E18        │E19
        ▼              ▼                           ▼           ▼
 ┌──────────────────────────── L4 · 证据发现 ─────────────────────────────────┐
 │ [P20 compile.findings]          ──────────▶   [P19 prioritize.entities]   │
 └──────┬─────────────────────────────────────────────────────────────────────┘
        │E22                E21
        ▼                  ▼
 ┌──────────────────────────── L5 · 汇总结论 ─────────────────────────────────┐
 │                             [P21 conclude.opinion]                        │
 └────────────────────────────────────────────────────────────────────────────┘
  注：P6→P15(E06) / P6→P16(E07) 为旁路直连；P9→P18(E12)、P10→P17(E17) 为 L1→L3
      跨层直通；P16→P20(E20)、P16→P21(E23) 为独立自洽基准弧；P11→P13(E11)、
      P11→P14(E09) 为 L1 内分叉；P17→P18(E13) 为 L3 内部边。边编号 = §8.2 连线明细。
```

### 8.2 连线明细：23 条数据接口边（Edge 1–23）

| # | 上游插件 · 输出工件 | → | 下游插件 · 输入端口 | 传递内容 |
|---|---|:---:|---|---|
| E01 | P1 `ingest.ledger`.records | → | P7 `parse.ledger`.records | 1000×5 流水矩阵 |
| E02 | P2 `ingest.expense`.records | → | P8 `probe.expense`.records | 120×3 台账矩阵 |
| E03 | P3 `ingest.bank`.records | → | P9 `reconcile.bank`.records | 24 行三文件合并记录 |
| E04 | P4 `ingest.controls`.records | → | P10 `scan.controls`.records | 5×8 内控登记 |
| E05 | P5 `ingest.pay`.records | → | P11 `profile.pay`.records | 850×3 付款矩阵 |
| E06 | P6 `ingest.baseline`.records | → | P15 `corr.features`.records | 776×27 基准（分叉 E06/E07） |
| E07 | P6 `ingest.baseline`.records | → | P16 `recompute.aar`.records | 776×27 基准（恒等式复算） |
| E08 | P7 `parse.ledger`.records | → | P14 `rank.vendor`.ledger | 供应商聚合记录 |
| E09 | P11 `profile.pay`.segments | → | P14 `rank.vendor`.pay | 业务段聚合记录（双源融合） |
| E10 | P8 `probe.expense`.records | → | P12 `detect.anomaly`.records | 台账（再次独立复算） |
| E11 | P11 `profile.pay`.amounts | → | P13 `audit.benford`.amounts | 850 笔金额序列 |
| E12 | P9 `reconcile.bank`.gaps | → | P18 `fuse.risk`.gaps | 未达 5 笔 / 账差 7,480 信号 |
| E13 | P17 `test.controls`.defects | → | P18 `fuse.risk`.defects | 缺陷 3 项信号 |
| E14 | P12 `detect.anomaly`.anomalies | → | P18 `fuse.risk`.anomalies | 异常 4 项信号 |
| E15 | P13 `audit.benford`.chi2 | → | P18 `fuse.risk`.benford | χ²=216.62 偏离信号 |
| E16 | P15 `corr.features`.top5 | → | P18 `fuse.risk`.feat | 特征-风险相关 Top5 |
| E17 | P10 `scan.controls`.controls | → | P17 `test.controls`.controls | 5 条内控待测 |
| E18 | P18 `fuse.risk`.score | → | P19 `prioritize.entities`.score | 综合风险评分（分叉 E18/E19） |
| E19 | P18 `fuse.risk`.score | → | P20 `compile.findings`.score | 综合风险评分（发现归集） |
| E20 | P16 `recompute.aar`.consistency | → | P20 `compile.findings`.aar | 自洽基准弧（0/776 偏差） |
| E21 | P19 `prioritize.entities`.top5 | → | P21 `conclude.opinion`.top5 | 高风险实体 Top5 |
| E22 | P20 `compile.findings`.list | → | P21 `conclude.opinion`.list | 发现清单 F1–F5 |
| E23 | P16 `recompute.aar`.consistency | → | P21 `conclude.opinion`.aar | 自洽基准弧（结论旁路） |

### 8.3 组网结构特征

- **分层**：L0 接入（6 源）→ L1 清洗（5）→ L2 关联（4）→ L3 建模（3）→ L4 证据（2）→ L5 结论（1），逐层收敛。
- **汇聚节点**：P18 `fuse.risk` 汇聚 **5 条信号边**（E12–E16），为网络最大入度节点；P21 `conclude.opinion` 汇聚 3 路（E21–E23）；P20 `compile.findings` 汇聚 2 路。
- **分叉/旁路**：P6 `ingest.baseline` 一分二（E06/E07）；P16 `recompute.aar` 经 E20/E23 双路直通发现与结论——恒等式校验不参与风险融合，作为**独立自洽基准旁路**。
- **双源融合节点**：P14 `rank.vendor` 同时绑定流水（E08）与付款段（E09）两路由血缘。
- **无环性**：6 层严格递增，最长链路 5 条接口边（如 E04→E17→E13→E18→E21 或 E04→E17→E13→E19→E22）；无反向边，满足单一路由计划有向无环约束。
- **只读血缘**：所有边均为输出工件 → 输入端口的契约引用，无写入、无旁路执行、无外部调用；运行期在浏览器内从内嵌原始记录确定性重算，与 §7 的 `read_only` 风险类一致。

### 8.4 逐插件注释：原理功能 · 数据接口 · 传入/传出数据样例

> 缩略约定：`接口`=数据接口（上游输出工件 → 本插件 → 下游输入工件，形态 `源.字段`）；`传给`=传入样例（来自内嵌原始文件的实际行）；`传样`=传出数据样例（本插件实际产出的行/值）。所有样例为机器实测，非构造。

#### L0 数据接入

**P1 `ingest.ledger`（交易流水装载）**
- 原理：读 CSV 原始字节 → SHA-256 指纹 → 逐行解析；数值列转 number、文本列保留，得到 `records` 数值矩阵。
- 接口：`∅ → records{transaction_id,date,vendor,amount,category}`；1000×5。
- 传给：无（种子，绑定源文件 S1）。
- 传样：`TXN-0000, 2023-08-16, Starbucks, 272.87, Operations` ｜ KPI：行 1000 / 列 5 / 指纹 `sha256:…`。

**P2 `ingest.expense`（费用台账装载）**
- 原理：同上（S2 文件指纹 + 解析）。
- 接口：`∅ → records{date,account_ref,amount}`；120×3。
- 传样：`2026-01-01, GL-4000, 100.0` ｜ KPI：行 120 / 列 3。

**P3 `ingest.bank`（银企三文件装载）**
- 原理：原子合并对账单（S3）、日记账（S4）、脏队列（S5）三个同构文件为单一 `records`（24 行），逐源指纹。
- 接口：`∅ → records{date,description,amount,ref}`；9+10+5=24 行。
- 传样：对账单 `2026-05-02, Customer deposit - INV1041, 48200.0, BNK-1`；脏队列 `2026-06-02, Deposit INV1101, 52,000.00, B1`（金额含千分位）。

**P4 `ingest.controls`（内控登记装载）**
- 原理：同上（S6 文件解析）。
- 接口：`∅ → records{control_id,cycle,description,frequency,type,last_performed,evidence_status,notes}`；5×8。
- 传样：`C-03, Cash, Bank reconciliation review, Monthly, Manual, 2026-05-31, Evidence attached, Preparer ALSO approves - SoD conflict`。

**P5 `ingest.pay`（付款单据装载）**
- 原理：同上（S7 文件解析）。
- 接口：`∅ → records{txn_id,segment,amount}`；850×3。
- 传样：`OPS-0000, Operations, 8284.77`。

**P6 `ingest.baseline`（审计风险基准装载）**
- 原理：同上（S8 文件解析；UCI 重复列名 Score_B/Prob 别名化去重，保持 27 列唯一）。
- 接口：`∅ → records{27 列}`；776 行。分叉两路（E06→P15，E07→P16）。
- 传样：`3.89, 23, 4.18, 0.6, 2.508, 2.5, 0.2, 0.5, 6.68, 5, 0.2, 1, 3.38, 0.2, 0.676, 2, 0.2, 0.4, 0, 0.2, 0, 2.4, 8.574, 0.4, 0.5, 1.7148, 1`（第 1 行；末 4 列 Inherent×CONTROL×Detection=8.574×0.4×0.5=1.7148=Audit_Risk 自洽 ✓）。

#### L1 解析清洗

**P7 `parse.ledger`（流水解析校验）**
- 原理：供应商分组计数/金额聚合 + 分布统计（min/P50/P95/max）。
- 接口：`ingest.ledger.records → records{vendor,amount}`。
- 传给：`TXN-0000, 2023-08-16, Starbucks, 272.87, Operations`。
- 传样：8 家供应商 | Slack 154 笔 / $37,912.26（13.8%）… AWS 149 笔 / **$60,082.57（21.8%）居首** | 极值 `25000.00`（≈均值 90.8×，P95 470.62）。

**P8 `probe.expense`（台账清洗/日历校验）**
- 原理：ISO-8601 严格历法校验（2026 非闰年）+ 金额 IQR 1.5× 离群检测。
- 接口：`ingest.expense.records → records{date,amount}`。
- 传样：`2026-01-01, GL-4000, 100.0`。
- 传给：`2026-01-01, GL-4000, 100.0`（正常行）。
- 传样：非法日期 2 条 `2026-02-29, GL-4058`、`2026-02-30, GL-4059`；离群 2 条 `290.0`（>111）、`12.0`（<95）。

**P9 `reconcile.bank`（银企对账）**
- 原理：银行×账务按金额精确匹配；未配对的按"银行已记（BNK-only）"/"企业已记（GL-only）"分类并汇总净差；脏队列单独标记解析失败/重复/格式问题。
- 接口：`ingest.bank.records → gaps{ref,amount} + dirty_q{ref}`。
- 传给：`2026-05-02, Customer deposit - INV1041, 48200.0, BNK-1`（对账行）；`2026-06-05, Cheque 2210, -14000, B2`（脏行）。
- 传样：匹配 7 笔；未达 5 笔——银行侧 BNK-8(−350)、BNK-9(+410)，企业侧 GL-8(−12,640)、GL-9(+27,300)、GL-10(−7,120)，**净差 7,480**；时差 2 笔（Chq 2204/2205）；dirty_q：逗号格式 B1、重复支票 B2/B3、文本金额 B4(`TWENTY THOUSAND`)。

**P10 `scan.controls`（内控要素扫描）**
- 原理：证据状态 / 执行日期(-超期) / 职责分离（SoD）三规则扫描。
- 接口：`ingest.controls.records → controls{control_id,status}`。
- 传给：`C-03, Cash, …, Evidence attached, Preparer ALSO approves - SoD conflict`。
- 传样：缺陷 3（C-02 无证据、C-03 SoD）、超期 1（C-05 TWO QUARTERS OVERDUE）、通过 2（C-01、C-04）。

**P11 `profile.pay`（付款画像）**
- 原理：按 segment 聚合（笔数/金额/占比）+ 金额首位数字频次向量。
- 接口：`ingest.pay.records → amounts{amount} + segments{segment,count}`。
- 传给：`OPS-0000, Operations, 8284.77`。
- 传样：Operations 600 笔 / $8,993,322.82（35.2%）；Procurement-VendorX 250 笔 / **$16,533,329.53（64.8%）**；首位数字序列 `{1:177, 2:93, 3:66, 4:119, 5:81, 6:118, 7:90, 8:81, 9:25}`。

#### L2 关联分析

**P12 `detect.anomaly`（费用异常检测）**
- 原理：对台账独立复算 IQR 离群 + 历法校验（与 P8 口径一致，结果互证）。
- 接口：`probe.expense.records → anomalies{date,amount,kind}`。
- 传样：4 项——`amount` 离群 2（12.0、290.0），`date` 非法 2（02-29、02-30）。

**P13 `audit.benford`（付款 Benford 检验）**
- 原理：Benford 首位定律 p(d)=lg(1+1/d)；χ²=Σ(obs−exp)²/exp，临界 15.51（df=8, α=0.05）。
- 接口：`profile.pay.amounts → chi2{chi2,top_deviation}`。
- 传给：`8284.77`（金额行，首位 8）。
- 传样：χ²=**216.62 ≫ 15.51**（显著偏离）；digit1 观测 177 vs 期望 255.9（−78.9），digit6 观测 118 vs 期望 56.9（**+61.1 最大偏离**）。

**P14 `rank.vendor`（供应商集中度）**
- 原理：流水供应商 × 付款段金额聚合，计算实体份额（独立分析，不流入风险融合）。
- 接口：`parse.ledger.records + profile.pay.segments → concentration{entity,share}`。
- 传样：头部 2 家供应商占流水 35.6%（AWS 21.8% + Slack 13.8%）；付款段 VendorX 占付款总额 64.8%。

**P15 `corr.features`（风险特征关联）**
- 原理：对 776×27 求各数值列与 Risk 的 Pearson 相关系数，取 |r| Top5。
- 接口：`ingest.baseline.records → top5{feature,r}`。
- 传给：`3.89, 23, … 0.5, 1.7148, 1`（行；末列 Risk）。
- 传样：Score **0.786**｜Score_MV 0.688｜Score_B 0.636｜Score_A 0.620｜CONTROL_RISK 0.417。

#### L3 风险建模

**P16 `recompute.aar`（恒等式复算·自洽基准）**
- 原理：逐行验证 Audit_Risk = Inherent_Risk × CONTROL_RISK × Detection_Risk（容差 1e-6）。
- 接口：`ingest.baseline.records → consistency{mismatch}`。
- 传样：**0/776 偏差（100.0%）**；示例行 `8.574×0.4×0.5=1.7148` ✓。

**P17 `test.controls`（内控有效性测试）**
- 原理：对 5 条内控抽样，按缺陷类型分类（无证据/SoD/超期）。
- 接口：`scan.controls.controls → defects{control_id,issue}`。
- 传样：通过率 40%（C-01、C-04 通过）；缺陷 3 映射域 Revenue/Cash/ITGC。

**P18 `fuse.risk`（多源风险融合）**
- 原理：加权线性融合 5 路信号——`score = 20·min(1,gaps/5) + 20·min(1,defects/4) + 15·min(1,anoms/4) + 25·min(1,χ²/50) + 20·min(1,|r_max|/0.5)`；≥70 高风险 / ≥45 中 / <45 低。
- 接口：`reconcile.bank.gaps + test.controls.defects + detect.anomaly.anomalies + audit.benford.chi2 + corr.features.top5 → score{total,grade}`。
- 传样（本批实测输入代入）：gaps=5→20、defects=3→15、anoms=4→15、χ²=216.62→25、|r|=0.786→20 ⇒ **total=95 → 高风险**。

#### L4 证据发现

**P19 `prioritize.entities`（高风险实体排序）**
- 原理：付款段/供应商按金额 × 风险信号综合排序。
- 接口：`fuse.risk.score → top5{entity,score}`。
- 传样：Procurement-VendorX（$16.53M）＞ Operations 段（$8.99M）＞ AWS ＞ Slack ＞ Starbucks（按金额 Top5）。

**P20 `compile.findings`（发现归集）**
- 原理：将上游信号归并为结构化发现清单，每条附严重度与来源工件引用。
- 接口：`fuse.risk.score + recompute.aar.consistency → list{id,severity,source}`。
- 传样：F1 台账缺陷（4 项，高，←detect.anomaly）；F2 未达账项 5 笔/7,480（中，←reconcile.bank）；F3 内控缺陷 3 项（高，←test.controls）；F4 Benford 偏离 χ²=216.62（高，←audit.benford）；F5 单笔极值 25,000（中，←parse.ledger）。

#### L5 汇总结论

**P21 `conclude.opinion`（审计意见生成）**
- 原理：汇总数据源规模 + 发现清单 + 风险评级 + AAR 一致率，输出工件并计算 fnv1a-64 确定性指纹（演示用，非密码学）。
- 接口：`prioritize.entities.top5 + compile.findings.list + recompute.aar.consistency → artifact{summary,fingerprint}`。
- 传样：`{6 源/2,775 行, F1–F5, 风险=高(95), AAR一致=100.0%, fingerprint=fnv1a64(summary)}`。

---

## 9. 数据样貌：各数据源实际行示例（原样展示，未改写）

> 以下为 8 个数据源文件第 1 行至若干行的**原始样貌**（表头 + 实测数据行），逐行拷自 `E:\数据`，用于直观呈现字段结构、取值风格与数据质量问题。行内以 ` | ` 分隔字段。

### S1 交易流水 raw_transactions.csv（1,000 行 × 5 列）

```
transaction_id | date       | vendor        | amount | category
TXN-0000       | 2023-08-16 | Starbucks     | 272.87 | Operations
TXN-0001       | 2023-11-05 | Slack         | 353.79 | Operations
TXN-0002       | 2023-07-04 | Starbucks     | 406.8  | Operations
TXN-0003       | 2023-08-01 | Starbucks     | 343.59 | Operations
TXN-0004       | 2023-07-29 | Google Cloud  | 382.87 | Operations
TXN-0005       | 2023-04-12 | Zoom          | 311.46 | Operations
```

**S1 业务说明与流向**
- **业务说明**：企业费用支出流水（采购/运营科目付款记录），逐笔记录日期、供应商、金额、科目，是费用审计的主数据源。
- **流向插件**：**P1 `ingest.ledger`**（装载，边 E01）→ **P7 `parse.ledger`**（解析校验，边 E09）→ 喂给 **P14 `rank.vendor`**（供应商集中度，独立分析）+ **P18 `fuse.risk`**（风险融合）。
- **具体算法**：供应商分组计数与金额聚合、分布统计（min / P50 / P95 / max）、极值识别（`25000.00` ≈ 均值 90.8 倍）。
- **目的**：识别高频供应商与单笔极值支出，发现"供应商集中 + 超常支出"两类采购风险信号。

### S2 费用台账 expense_ledger.csv（120 行 × 3 列）

```
date       | account_ref | amount
2026-01-01 | GL-4000     | 100.0
2026-01-02 | GL-4001     | 101.0
2026-01-03 | GL-4002     | 102.0
2026-01-04 | GL-4003     | 103.0
2026-01-05 | GL-4004     | 104.0
```

**S2 业务说明与流向**
- **业务说明**：费用科目台账，逐日登记科目引用与金额，用于费用归集与异常检测的小样本数据集。
- **流向插件**：**P2 `ingest.expense`**（装载，边 E02）→ **P8 `probe.expense`**（台账清洗/日历校验，边 E10）→ **P12 `detect.anomaly`**（费用异常检测，边 E13）→ **P18 `fuse.risk`**（风险融合）。
- **具体算法**：ISO-8601 严格历法校验（2026 非闰年，拒绝 02-29 / 02-30）+ 金额 **IQR 1.5 倍**离群检测（`290.0` > Q3+1.5·IQR、`12.0` < Q1−1.5·IQR）。
- **目的**：验证台账日期合法性与金额分布，揪出"补记单"与异常录入（本批实测 4 项：离群 2 + 非法日期 2）。

### S3 银行对账单 bank_statement.csv（9 行 × 4 列，全量）

```
date       | description                    | amount   | ref
2026-05-02 | Customer deposit - INV1041      | 48200.0  | BNK-1
2026-05-05 | Customer deposit - INV1042      | 31750.0  | BNK-2
2026-05-09 | Cheque 2204 - Apex Supplies     | -18400.0 | BNK-3
2026-05-12 | Customer deposit - INV1043      | 22600.0  | BNK-4
2026-05-15 | Cheque 2205 - Delta Logistics   | -9750.0  | BNK-5
2026-05-20 | Wire out - payroll              | -84300.0 | BNK-6
2026-05-23 | Customer deposit - INV1044      | 56900.0  | BNK-7
2026-05-28 | Bank charges                    | -350.0   | BNK-8
2026-05-30 | Interest received               | 410.0    | BNK-9
```

**S3 业务说明与流向**
- **业务说明**：银行对账单（银行侧口径），记录存款、支票、电汇、手续费、利息等 9 笔入出账，是企业账面余额的对照基准。
- **流向插件**：**P3 `ingest.bank`**（与 S4/S5 原子合并，边 E03）→ **P9 `reconcile.bank`**（银企对账，边 E11）→ **P18 `fuse.risk`**（风险融合）。
- **具体算法**：银行×账务**按金额+日期精确匹配**；配对成功的标记"应计核销"，未配对的按 **BNK-only（银行已记未入账）** 与 **GL-only（企业已记未达）** 分类，汇总差额（开启账面-银行账面净差）；时差 2 天以内的账项标记"时间差"而非缺陷。
- **目的**：发现未达账项、虚假存款、账外支票（本批实测：净差 **7,480**——银行侧 BNK-8/BNK-9 未入账，企业侧 GL-8/GL-9/GL-10 银行未知）。

### S4 现金日记账 gl_cash.csv（10 行 × 4 列，全量）

```
date       | description              | amount   | ref
2026-05-02 | INV1041 receipt          | 48200.0  | GL-1
2026-05-05 | INV1042 receipt          | 31750.0  | GL-2
2026-05-08 | Chq 2204 Apex Supplies   | -18400.0 | GL-3
2026-05-12 | INV1043 receipt          | 22600.0  | GL-4
2026-05-14 | Chq 2205 Delta Logistics | -9750.0  | GL-5
2026-05-20 | Payroll wire             | -84300.0 | GL-6
2026-05-23 | INV1044 receipt          | 56900.0  | GL-7
2026-05-29 | Chq 2206 Omega Services  | -12640.0 | GL-8
2026-05-31 | INV1045 receipt          | 27300.0  | GL-9
2026-05-31 | Chq 2207 Kappa Ltd       | -7120.0  | GL-10
```

**S4 业务说明与流向**
- **业务说明**：企业现金日记账（企业侧口径），与银行对账单同源的账面记录，反映企业认为银行已发生的全部收支。
- **流向插件**：**P3 `ingest.bank`**（与 S3/S5 原子合并，边 E03）→ **P9 `reconcile.bank`**（银企对账，边 E11）→ **P18 `fuse.risk`**（风险融合）。
- **具体算法**：与 S3 进行金额匹配（金额相等即配对）；`GL-3`↔`BNK-3`、`GL-5`↔`BNK-5` 等 7 笔配对成功；`GL-8/9/10`、`BNK-8/9` 无对方。
- **目的**：与银行对账单互为镜像，核对企业账实是否一致；企业侧多记（GL-only）提示"银行未知支出"——潜在账外支付/资金挪用（本批实测 GL-only 3 笔：−12,640 / +27,300 / −7,120）。

### S5 银行脏队列 bank_dirty.csv（5 行 × 4 列，全量）

```
date       | description        | amount          | ref
2026-06-02 | Deposit INV1101    | 52,000.00       | B1   ← 千分位逗号
2026-06-05 | Cheque 2210        | -14000          | B2   ← 与 B3 金额/描述重复
2026-06-05 | Cheque 2210        | -14000          | B3
2026-06-12 | Deposit INV1102    | TWENTY THOUSAND | B4   ← 文本金额，无法数值化
2026-06-20 | Wire payroll       | -80000          | B5
```

**S5 业务说明与流向**
- **业务说明**：银企对账**淘汰/异常队列**——导入后无法直接参与对账的账项（格式问题、重复、不可解析金额），是对账质量的"拦路虎"样本。
- **流向插件**：**P3 `ingest.bank`**（原子合并，边 E03）→ **P9 `reconcile.bank`**（对账时单独标记，边 E11）→ **P18 `fuse.risk`**（风险融合）。
- **具体算法**：导入期**格式探测**——千分位逗号剥离（`52,000.00`→52000.00）、重复摘要+金额去重（B2/B3 同额同描述）、非数值金额判不可解析（`TWENTY THOUSAND` 拒绝并标记）；未清洗前存续于 **dirty 队列**，不进对账。
- **目的**：量化数据质量缺陷对账实核对的干扰；本批 5 行中 3 行异常（1 逗号、1 重复、1 文本金额）→ 直接放大对账 gap，警示 ETL 清洗必要性。

### S6 内控登记 control_register.csv（5 行 × 8 列，全量）

```
control_id | cycle  | description                   | frequency | type      | last_performed | evidence_status             | notes
C-01       | Revenue| Three-way match PO/GR/invoice | Monthly   | Automated | 2026-05-31     | Evidence attached           | None
C-02       | Revenue| Credit-note approval by sales director | Monthly | Manual | 2026-03-31 | NO EVIDENCE for Apr-May      | None
C-03       | Cash   | Bank reconciliation review    | Monthly   | Manual    | 2026-05-31     | Evidence attached           | Preparer ALSO approves - SoD conflict
C-04       | Payroll| New-hire authorization        | Per event | Manual    | 2026-05-31     | Evidence attached           | None
C-05       | ITGC   | User access review            | Quarterly | Manual    | 2025-12-31     | TWO QUARTERS OVERDUE        | None
```

**S6 业务说明与流向**
- **业务说明**：内部控制矩阵登记表——列出各业务循环的关键控制点（收入/现金/薪酬/ITGC）、执行频率、执行人、最近执行日期与证据状态，是内控有效性评价的输入。
- **流向插件**：**P4 `ingest.controls`**（装载，边 E04）→ **P10 `scan.controls`**（内控要素扫描，边 E12）→ **P17 `test.controls`**（内控有效性测试，边 E17）→ **P18 `fuse.risk`**（风险融合）。
- **具体算法**：**三规则扫描**——证据状态（`NO EVIDENCE` / 证据缺失）、执行日期超期（季度/月度/按事件 vs `last_performed`，`C-05 TWO QUARTERS OVERDUE`）、职责分离冲突（`Preparer ALSO approves` 文本匹配 SoD 关键词）。
- **目的**：评估控制设计与运行有效性；本批 5 条内控 **通过率 40%**，3 项缺陷（无证据 C-02、SoD 冲突 C-03、超期 C-05）映射 Revenue/Cash/ITGC 域。

### S7 付款单据 disbursements.csv（850 行 × 3 列）

```
txn_id   | segment               | amount
OPS-0000 | Operations            | 8284.77
OPS-0001 | Operations            | 118.86
OPS-0002 | Operations            | 668.48
OPS-0003 | Operations            | 467.34
OPS-0004 | Operations            | 16196.22
OPS-0005 | Operations            | 10717.62
OPS-0006 | Operations            | 47483.06
```

**S7 业务说明与流向**
- **业务说明**：全量付款单据流水（850 笔付款），含单据号、业务段、金额；体量最大、金额质量最高的付款数据集，支撑 Benford 检验与付款画像。
- **流向插件**：**P5 `ingest.pay`**（装载，边 E05）→ **P11 `profile.pay`**（付款画像，边 E14）→ **P13 `audit.benford`**（Benford 检验，边 E15）→ **P18 `fuse.risk`**（风险融合）。
- **具体算法**：按 segment 聚合笔数/金额/占比画像；提取金额**首位数字频率向量**，与 Benford 期望 `p(d)=lg(1+1/d)` 对比，做 **χ² 拟合优度检验**（df=8，α=0.05，临界 15.51）。
- **目的**：识别金额规律性异常——人为伪造金额通常在首位数字上偏离 Benford 分布；本批实测 **χ²=216.62 ≫ 15.51**（digit6 观测 118 vs 期望 56.9，+61.1 最大偏离），且 Procurement-VendorX 占付款总额 64.8%，双信号叠加 → 高风险。

### S8 审计风险基准 audit_risk.csv（776 行 × 27 列，表头含 2 处重复列名）

```
Sector_score | LOCATION_ID | PARA_A | Score_A | Risk_A | PARA_B | Score_B | Risk_B | TOTAL | numbers | Score_B(dup) | Risk_C | Money_Value | Score_MV | Risk_D | District_Loss | PROB | RiSk_E | History | Prob(dup) | Risk_F | Score | Inherent_Risk | CONTROL_RISK | Detection_Risk | Audit_Risk | Risk
3.89 | 23 | 4.18 | 0.6 | 2.508 | 2.5 | 0.2 | 0.5 | 6.68 | 5 | 0.2 | 1 | 3.38 | 0.2 | 0.676 | 2 | 0.2 | 0.4 | 0 | 0.2 | 0 | 2.4 | 8.574 | 0.4 | 0.5 | 1.7148 | 1
3.89 | 6 | 0 | 0.2 | 0 | 4.83 | 0.2 | 0.966 | 4.83 | 5 | 0.2 | 1 | 0.94 | 0.2 | 0.188 | 2 | 0.2 | 0.4 | 0 | 0.2 | 0 | 2 | 2.554 | 0.4 | 0.5 | 0.5108 | 0
3.89 | 6 | 0.51 | 0.2 | 0.102 | 0.23 | 0.2 | 0.046 | 0.74 | 5 | 0.2 | 1 | 0 | 0.2 | 0 | 2 | 0.2 | 0.4 | 0 | 0.2 | 0 | 2 | 1.548 | 0.4 | 0.5 | 0.3096 | 0
```

> 上表第 1 行恒等式自洽：`8.574 × 0.4 × 0.5 = 1.7148 = Audit_Risk ✓`；列名 `Score_B`、`Prob` 在源文件中重复出现，构建期已别名化（`Score_B2` / `Prob2`）。

**S8 业务说明与流向**
- **业务说明**：UCI 审计风险分类基准（776 个被审计单元 × 27 项特征）——行业分、区域、6 组原始/评分/风险映射（PARA_A..RISK_F）、金额特征（Money_Value / District_Loss / PROB / History），以及 4 列风险合成（Inherent / CONTROL / Detection / Audit_Risk）+ 高/低靶标（Risk）；是风险建模的**标准校验基准**。
- **流向插件**：**P6 `ingest.baseline`**（装载，边 E06/E07 分叉）→ 双路：**P15 `corr.features`**（风险特征关联，边 E18）+ **P16 `recompute.aar`**（恒等式复算，边 E16）→ **P18 `fuse.risk`**（风险融合）。
- **具体算法**：P15 对 27 列（数值特征）与靶标 Risk 计算 **Pearson 相关系数**，取 |r| Top5（Score 0.786 / Score_MV 0.688 / Score_B 0.636 / Score_A 0.620 / CONTROL_RISK 0.417）；P16 逐行复算 **Audit_Risk = Inherent_Risk × CONTROL_RISK × Detection_Risk**（容差 1e-6），检验基准自身自洽性。
- **目的**：a) 用相关系数为风险融合提供"最强风险特征"先验；b) 独立复核官方基准是否**零偏差自洽**——本批实测 **0/776 偏差（100.0%）**，据此确认后续恒等式推导可信；c) 融入融合分作为第 5 路权重。