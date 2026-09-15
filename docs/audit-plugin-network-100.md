# 审计插件网络关系梳理（100 插件循环组网）

> 本文件是「AI 画布组网」的能力蓝图：按 ComfyUI 风格，每个插件一个独立文件夹，
> 位于 `plugins/builtin/audit-*`，含 `plugin.protocol.json`（端口契约）与 `plugin.manifest.json`（安装声明）。
> 当前全部为 `contract_only`（协议先行，只读分析，不写领域表）；实现与验证按既有 UPP 流程补齐。
> 生成器：`.data/_gen_audit_plugins.py`，规格：`.data/_audit_plugin_specs.py`。

## 0. 数量与分层

| 层 | 数量 | 说明 |
|---|---|---|
| 数据支撑层 foundation | 18 | 全网络共享能力底座，被上层按需调用（星型网状） |
| 核心业务循环层 business | 76 | 沿立项→风险→计划→实施→底稿→定性→报告→整改 8 阶段串联（主循环） |
| 治理优化层 govern | 6 | 承接全流程输出，反向赋能前序阶段（闭环迭代） |
| **合计** | **100** | 目录：`plugins/builtin/audit-*` |

三类链接模式：`⟹` 主干正向流转（阶段间数据传递）；`→` 阶段内联动（同阶段上下游）；
`⇢` 跨层能力调用（上层调支撑层）；`⇠` 反向闭环迭代（治理层赋能前序）；`⊣` 全流程穿透（管控类贯穿所有节点）。

## 1. 全流程穿透层（5 个管控插件，⊣ 全程并行生效）

| 插件 | 能力 | 穿透范围 |
|---|---|---|
| 权限管控 | audit.foundation.permission-control | 所有插件访问控制 |
| 数据脱敏 | audit.foundation.data-mask | 所有数据展示/对外输出 |
| 数据加密存储 | audit.foundation.data-encrypt | 所有归档/底稿存储 |
| 工作流引擎 | audit.foundation.workflow-engine | 所有审批/流转/派单 |
| 审计日志自动记录 | audit.field.audit-log | 所有操作/数据访问 |

## 2. 数据支撑层（18 个公共节点，⇢ 被上层调用）

| # | 文件夹 | 能力 | 输入 ⟹ 输出 | 链接语义 |
|---|---|---|---|---|
| 1 | audit-foundation-multi-source-collect | audit.foundation.multi-source-collect | audit-source-request ⟹ audit-source-set | 对接财务/业务/OA/外部监管多系统数据；被凭证穿透、风险扫描、疑点核查类插件调用（⇢ 上层调用本层）。 |
| 2 | audit-foundation-finance-clean | audit.foundation.finance-clean | raw-finance-set ⟹ clean-finance-set | 对凭证、账簿、报表去重/补全/标准化；被财务异常预警、问题金额计算、底稿勾稽校验调用（⇢）。 |
| 3 | audit-foundation-biz-standardize | audit.foundation.biz-standardize | raw-biz-set ⟹ standard-biz-set | 统一采购、销售、人力业务口径；被内控测绘、流程断点、资产盘点调用（⇢）。 |
| 4 | audit-foundation-master-mapping | audit.foundation.master-mapping | master-source ⟹ master-map | 统一科目/供应商/员工/资产编码；被责任认定、跨系统比对、往来函证调用（⇢）。 |
| 5 | audit-foundation-lineage-track | audit.foundation.lineage-track | lineage-query ⟹ lineage-graph | 记录数据采集到应用全链路流转；被证据真实性校验、问题溯源、底稿复核调用（⇢）。 |
| 6 | audit-foundation-data-mask | audit.foundation.data-mask | mask-request ⟹ masked-set | 敏感数据自动脱敏；⊣ 穿透所有数据展示与对外输出节点。 |
| 7 | audit-foundation-data-encrypt | audit.foundation.data-encrypt | encrypt-request ⟹ encrypted-ref | 底稿/证据等核心数据加密存储；⊣ 穿透所有归档存储节点。 |
| 8 | audit-foundation-metadata-manage | audit.foundation.metadata-manage | metadata-query ⟹ metadata-dict | 统一数据字典、指标定义；被所有指标计算、数据分析类插件调用（⇢）。 |
| 9 | audit-foundation-quality-check | audit.foundation.quality-check | quality-input ⟹ quality-report | 核查完整性/准确性/一致性；被资料预检、报告数据校验、底稿校验调用（⇢）。 |
| 10 | audit-foundation-metric-compute | audit.foundation.metric-compute | metric-input ⟹ metric-output | 内置审计指标库自动计算财务/业务/风险指标；被风险评估、疑点识别、优先级排序调用（⇢）。 |
| 11 | audit-foundation-tag-manage | audit.foundation.tag-manage | tag-query ⟹ tag-tree | 管理审计对象/问题/风险标签；被立项打标、证据分类、问题分级、案例入库调用（⇢）。 |
| 12 | audit-foundation-fulltext-search | audit.foundation.fulltext-search | search-query ⟹ search-hits | 海量文档/凭证关键词检索；被底稿、证据、问题核查类插件调用（⇢）。 |
| 13 | audit-foundation-ocr-extract | audit.foundation.ocr-extract | ocr-image ⟹ ocr-text | 识别纸质单据、发票、合同文字；被取证、底稿、核验类插件调用（⇢）。 |
| 14 | audit-foundation-nlp-process | audit.foundation.nlp-process | nlp-input ⟹ nlp-output | 文本摘要、语义匹配、自动撰写；被报告、访谈、规则匹配类插件调用（⇢）。 |
| 15 | audit-foundation-rule-engine | audit.foundation.rule-engine | rule-input ⟹ rule-evaluation | 可视化配置审计规则、校验逻辑；被风险预警、疑点识别、定性复核类插件调用（⇢）。 |
| 16 | audit-foundation-viz-analysis | audit.foundation.viz-analysis | viz-input ⟹ viz-output | 生成图表、看板、热图；被风险热图、趋势分析、成果看板、质量评分调用（⇢）。 |
| 17 | audit-foundation-workflow-engine | audit.foundation.workflow-engine | workflow-request ⟹ workflow-state | 驱动各类审批、流转流程；⊣ 穿透所有审批、流转、派单节点。 |
| 18 | audit-foundation-permission-control | audit.foundation.permission-control | access-request ⟹ access-decision | 基于角色配置数据/功能访问权限；⊣ 穿透所有插件访问控制。 |

## 3. 核心业务循环层（76 个，8 阶段主循环）

### 总览主循环

```text
立项 ⟹ 风险 ⟹ 计划 ⟹ 实施 ⟹ 底稿 ⟹ 定性 ⟹ 报告 ⟹ 整改 ──┐
  └──────────────────────────────────────────────────────┘（下一审计周期回流）
治理层 ⇠ 接收全流程输出，沉淀规则/方法/案例后反向赋能前序阶段
```

### 阶段1 审计立项与准备（10）

| # | 文件夹 | 能力 | 输入 ⟹ 输出 | 链接语义 |
|---|---|---|---|---|
| 19 | audit-mandate-demand-collect | audit.mandate.demand-collect | demand-input ⟹ demand-set | 面向管理层/业务部门征集审计需求；上游治理层输入，下游输出至立项申报，调用标签体系（⇢）。 |
| 20 | audit-mandate-strategy-align | audit.mandate.strategy-align | demand-set ⟹ aligned-demand | 需求与公司战略、年度重点对标；上游需求征集，下游输出至立项评审。 |
| 21 | audit-mandate-annual-propose | audit.mandate.annual-propose | aligned-demand ⟹ proposal-set | 立项申请与材料填报；上游需求对齐结果，下游输出至立项评审。 |
| 22 | audit-mandate-proposal-score | audit.mandate.proposal-score | proposal-set ⟹ scored-proposal | 按预设维度对立项申请评分排序；上游立项申报，调用工作流引擎驱动评审（⇢）。 |
| 23 | audit-mandate-project-library | audit.mandate.project-library | scored-proposal ⟹ project-snapshot | 存储备选与立项项目全生命周期；上游评审通过项目，下游输出至计划编制，并同步全流程各阶段状态（网状）。 |
| 24 | audit-mandate-priority-rank | audit.mandate.priority-rank | project-snapshot ⟹ priority-order | 基于风险/重要性/资源匹配度排序；上游项目库，调用统一指标计算权重（⇢）。 |
| 25 | audit-mandate-notice-generate | audit.mandate.notice-generate | project-snapshot ⟹ audit-notice | 自动生成标准化审计通知书并下发；上游正式立项项目，调用 NLP 生成文本（⇢）。 |
| 26 | audit-mandate-material-submit | audit.mandate.material-submit | notice-accept ⟹ submitted-material | 在线收集被审单位前置资料；上游通知书下发，下游输出至资料预检。 |
| 27 | audit-mandate-material-precheck | audit.mandate.material-precheck | submitted-material ⟹ precheck-report | 自动校验报送资料完整合规；上游资料报送，调用数据质量校验（⇢）。 |
| 28 | audit-mandate-team-forming | audit.mandate.team-forming | project-snapshot ⟹ team-scheme | 配置审计组人员/角色/分工；上游正式立项项目，调用权限管控分配权限（⇢）。 |

### 阶段2 风险识别与评估（11）

| # | 文件夹 | 能力 | 输入 ⟹ 输出 | 链接语义 |
|---|---|---|---|---|
| 29 | audit-risk-policy-risk-scan | audit.risk.policy-risk-scan | policy-input ⟹ policy-risk-set | 匹配最新监管政策识别合规风险；调用规则引擎与外部政策数据，输出至风险矩阵。 |
| 30 | audit-risk-industry-benchmark | audit.risk.industry-benchmark | industry-input ⟹ industry-risk-set | 对标同行业风险事件识别潜在风险；调用外部对标数据，输出至风险矩阵。 |
| 31 | audit-risk-internal-control-map | audit.risk.internal-control-map | submitted-material ⟹ ic-risk-set | 遍历内控流程识别控制缺陷与断点；上游前置资料，调用业务数据标准化，输出至风险矩阵。 |
| 32 | audit-risk-finance-anomaly-alert | audit.risk.finance-anomaly-alert | clean-finance-set ⟹ finance-anomaly-set | 计算财务指标偏离度识别异常波动；调用统一指标计算，输出至风险矩阵。 |
| 33 | audit-risk-process-gap-detect | audit.risk.process-gap-detect | standard-biz-set ⟹ process-gap-set | 扫描采购/销售/资金流程异常节点；调用业务数据标准化，输出至风险矩阵。 |
| 34 | audit-risk-fraud-pattern-match | audit.risk.fraud-pattern-match | risk-input-set ⟹ fraud-risk-set | 基于舞弊三角模型匹配异常特征；调用规则引擎，输出至风险矩阵。 |
| 35 | audit-risk-risk-matrix-build | audit.risk.risk-matrix-build | policy-risk-set,industry-risk-set,ic-risk-set,finance-anomaly-set,process-gap-set,fraud-risk-set ⟹ risk-matrix | 汇总各类风险点生成影响-概率矩阵；上游 6 类风险扫描，下游输出至风险等级赋值。 |
| 36 | audit-risk-risk-level-assign | audit.risk.risk-level-assign | risk-matrix ⟹ risk-level-set | 对风险点自动打分定级（高/中/低）；上游风险矩阵，调用规则引擎。 |
| 37 | audit-risk-high-risk-locate | audit.risk.high-risk-locate | risk-level-set ⟹ high-risk-area | 筛选高风险点对应业务领域/流程节点；上游风险等级结果，下游输出至方案编制。 |
| 38 | audit-risk-risk-heatmap | audit.risk.risk-heatmap | risk-level-set ⟹ risk-heatmap | 可视化呈现风险分布；上游风险等级结果，调用可视化分析。 |
| 39 | audit-risk-risk-response-advice | audit.risk.risk-response-advice | high-risk-area ⟹ risk-advice-set | 针对高风险点匹配应对策略与审计重点；上游高风险领域，调用 NLP；直接输入方案编制阶段（跨阶段直连）。 |

### 阶段3 审计计划与资源调度（9）

| # | 文件夹 | 能力 | 输入 ⟹ 输出 | 链接语义 |
|---|---|---|---|---|
| 40 | audit-plan-annual-plan-build | audit.plan.annual-plan-build | priority-order ⟹ annual-plan | 整合全年项目生成年度审计计划；上游项目优先级排序，输出至审批流程。 |
| 41 | audit-plan-project-scheme-build | audit.plan.project-scheme-build | high-risk-area,risk-advice-set ⟹ project-scheme | 编制审计范围/重点/程序；上游高风险领域与应对建议，调用审计程序模板。 |
| 42 | audit-plan-program-template-match | audit.plan.program-template-match | scheme-request ⟹ program-template | 匹配标准审计程序库；上游方案需求，输出至方案编制。 |
| 43 | audit-plan-sampling-select | audit.plan.sampling-select | sampling-input ⟹ sampling-plan | 根据审计目标与总体特征选择抽样方法；上游审计程序，调用统计计算能力。 |
| 44 | audit-plan-sample-size-compute | audit.plan.sample-size-compute | sampling-plan ⟹ sample-size | 基于置信水平/偏差率计算样本规模；上游抽样方法，输出至现场实施。 |
| 45 | audit-plan-staff-schedule | audit.plan.staff-schedule | team-scheme ⟹ schedule-plan | 根据项目需求/人员技能匹配排班；上游项目分工，调用人力资源数据。 |
| 46 | audit-plan-effort-budget | audit.plan.effort-budget | schedule-plan ⟹ effort-budget | 编制各阶段工时预算与成本测算；上游人员排班，输出至进度管控。 |
| 47 | audit-plan-resource-conflict-detect | audit.plan.resource-conflict-detect | schedule-plan,project-snapshot ⟹ conflict-alert | 识别人员跨项目冲突并预警；上游排班结果，调用项目库数据。 |
| 48 | audit-plan-plan-version-control | audit.plan.plan-version-control | plan-change ⟹ plan-version | 管理计划与方案多版本变更；上游计划变更申请，调用工作流引擎。 |

### 阶段4 现场审计实施（14）

| # | 文件夹 | 能力 | 输入 ⟹ 输出 | 链接语义 |
|---|---|---|---|---|
| 49 | audit-field-site-checkin-track | audit.field.site-checkin-track | checkin-input ⟹ checkin-log | 记录审计人员现场出勤与轨迹；上游人员排班，输出至审计日志。 |
| 50 | audit-field-workpaper-build | audit.field.workpaper-build | program-template,sample-size ⟹ workpaper-draft | 按审计程序编制底稿；上游程序模板与样本，调用全文检索、OCR。 |
| 51 | audit-field-voucher-drilldown | audit.field.voucher-drilldown | clean-finance-set ⟹ voucher-chain | 从报表直达明细账、凭证原始单据；上游财务数据，调用多源数据采集。 |
| 52 | audit-field-confirm-letter | audit.field.confirm-letter | confirm-input ⟹ confirm-result | 生成函证/跟踪回函/统计结果；上游往来款审计程序，调用工作流引擎。 |
| 53 | audit-field-inventory-count | audit.field.inventory-count | inventory-input ⟹ inventory-result | 制定监盘计划/记录结果/识别盘盈盘亏；上游存货审计程序，调用数据比对。 |
| 54 | audit-field-asset-check | audit.field.asset-check | asset-input ⟹ asset-result | 匹配资产台账与实物；上游资产审计程序，调用 OCR 识别。 |
| 55 | audit-field-interview-record | audit.field.interview-record | interview-input ⟹ interview-note | 记录访谈提纲/内容/要点；上游审计程序，调用 NLP 整理纪要。 |
| 56 | audit-field-meeting-minutes | audit.field.meeting-minutes | meeting-audio ⟹ meeting-minutes | 会商录音转写并提炼要点；调用语音转写与 NLP，输出至底稿。 |
| 57 | audit-field-cross-dept-inquiry | audit.field.cross-dept-inquiry | inquiry-request ⟹ inquiry-result | 发起跨部门数据调取与核实；调用工作流引擎，输出至证据归集。 |
| 58 | audit-field-suspicion-flag | audit.field.suspicion-flag | field-finding-input ⟹ suspicion-set | 对现场疑点即时标记分类；上游现场执行过程，下游输出至疑点汇总。 |
| 59 | audit-field-evidence-photo | audit.field.evidence-photo | photo-input ⟹ photo-evidence | 上传取证照片并关联底稿；调用 OCR 识别，输出至证据管理。 |
| 60 | audit-field-audit-log | audit.field.audit-log | log-event ⟹ audit-log-set | 全程自动记录所有审计操作与数据访问；⊣ 穿透全现场阶段，调用权限管控。 |
| 61 | audit-field-progress-report | audit.field.progress-report | effort-budget ⟹ progress-daily | 每日填报项目进度与完成工时；上游工时预算，输出至项目管控。 |
| 62 | audit-field-extension-approve | audit.field.extension-approve | extension-request ⟹ extension-decision | 处理延期/程序调整审批；调用工作流引擎，同步更新计划版本。 |

### 阶段5 审计证据与底稿管理（10）

| # | 文件夹 | 能力 | 输入 ⟹ 输出 | 链接语义 |
|---|---|---|---|---|
| 63 | audit-evidence-evidence-archive | audit.evidence.evidence-archive | evidence-input ⟹ evidence-index | 按类型/程序分类归集证据；上游现场取证结果，调用标签体系。 |
| 64 | audit-evidence-evidence-verify | audit.evidence.evidence-verify | evidence-index ⟹ evidence-verdict | 交叉验证证据一致真实；上游证据归档，调用数据血缘追踪。 |
| 65 | audit-evidence-workpaper-reconcile | audit.evidence.workpaper-reconcile | workpaper-draft ⟹ reconcile-report | 自动校验底稿间数据勾稽、逻辑一致；上游底稿编制，调用规则引擎。 |
| 66 | audit-evidence-workpaper-review3 | audit.evidence.workpaper-review3 | reconcile-report ⟹ review3-verdict | 驱动编制人-项目经理-部门负责人三级复核；调用工作流引擎，贯穿底稿全周期。 |
| 67 | audit-evidence-workpaper-version-diff | audit.evidence.workpaper-version-diff | workpaper-change ⟹ version-diff | 对比不同版本底稿修改痕迹；上游底稿变更，调用版本管理能力。 |
| 68 | audit-evidence-evidence-index-link | audit.evidence.evidence-index-link | workpaper-draft,evidence-index ⟹ evidence-link-graph | 建立底稿与证据索引关联，双向穿透查询；上游底稿与证据。 |
| 69 | audit-evidence-workpaper-template-update | audit.evidence.workpaper-template-update | standard-update ⟹ workpaper-template | 根据最新准则更新底稿模板；上游准则更新，输出至模板库。 |
| 70 | audit-evidence-e-signature | audit.evidence.e-signature | review3-verdict ⟹ signed-workpaper | 对复核通过底稿/证据加盖电子签章；调用身份认证，输出至正式档案。 |
| 71 | audit-evidence-workpaper-borrow-approve | audit.evidence.workpaper-borrow-approve | borrow-request ⟹ borrow-decision | 管理底稿借阅/复制审批；调用工作流、权限管控。 |
| 72 | audit-evidence-workpaper-encrypt-store | audit.evidence.workpaper-encrypt-store | signed-workpaper ⟹ archived-workpaper | 正式底稿加密归档；调用数据加密，实现长期存档。 |

### 阶段6 问题核查与定性（8）

| # | 文件夹 | 能力 | 输入 ⟹ 输出 | 链接语义 |
|---|---|---|---|---|
| 73 | audit-finding-suspicion-merge | audit.finding.suspicion-merge | suspicion-set,photo-evidence ⟹ merged-suspicion | 汇总全流程疑点去重分类；上游现场疑点标记与取证，下游输出至问题核实。 |
| 74 | audit-finding-issue-type-judge | audit.finding.issue-type-judge | merged-suspicion ⟹ issue-type-set | 初步判定问题类型（合规/财务/内控/舞弊）；上游疑点汇总，调用规则引擎。 |
| 75 | audit-finding-violation-clause-match | audit.finding.violation-clause-match | issue-type-set ⟹ violation-clause | 自动匹配监管法规/公司制度条款；上游问题性质，调用法规库数据。 |
| 76 | audit-finding-issue-amount-compute | audit.finding.issue-amount-compute | issue-verify-input,clean-finance-set ⟹ issue-amount | 自动核算问题金额/税款/损失；上游问题核实与财务数据。 |
| 77 | audit-finding-responsible-party-find | audit.finding.responsible-party-find | issue-trace-input,master-map ⟹ responsible-party | 追溯问题对应责任部门/岗位/人员；上游问题流程溯源，调用主数据。 |
| 78 | audit-finding-issue-grade | audit.finding.issue-grade | issue-amount,responsible-party ⟹ graded-issue | 按严重程度/影响范围分级；上游金额与责任认定，调用分级标准。 |
| 79 | audit-finding-auditee-feedback | audit.finding.auditee-feedback | graded-issue ⟹ feedback-set | 发起问题确认收集被审单位反馈；调用工作流引擎，输出至异议核实。 |
| 80 | audit-finding-issue-final-review | audit.finding.issue-final-review | feedback-set ⟹ final-issue-set | 最终复核问题定性分级；上游意见反馈，输出至报告；直接追溯现场审计日志与访谈记录（跨阶段）。 |

### 阶段7 审计报告与成果输出（7）

| # | 文件夹 | 能力 | 输入 ⟹ 输出 | 链接语义 |
|---|---|---|---|---|
| 81 | audit-report-report-frame-build | audit.report.report-frame-build | project-snapshot ⟹ report-frame | 根据项目类型匹配报告框架；上游项目信息，调用报告模板库。 |
| 82 | audit-report-issue-desc-write | audit.report.issue-desc-write | final-issue-set ⟹ issue-desc | 基于问题信息生成标准化描述；上游问题定性结果，调用 NLP。 |
| 83 | audit-report-advice-match | audit.report.advice-match | final-issue-set ⟹ advice-set | 针对问题类型匹配审计建议；上游问题清单，调用最佳实践库。 |
| 84 | audit-report-report-data-check | audit.report.report-data-check | report-draft-input ⟹ report-check-report | 校验报告中数据/口径一致；上游报告初稿，调用数据质量校验。 |
| 85 | audit-report-report-multi-review | audit.report.report-multi-review | report-check-report ⟹ report-approved | 驱动报告多级审批与修改；调用工作流引擎，同步管理版本。 |
| 86 | audit-report-result-distill | audit.report.result-distill | report-approved ⟹ distilled-result | 提炼共性发现与管理启示；上游正式报告，输出至成果库。 |
| 87 | audit-report-notice-mask-publish | audit.report.notice-mask-publish | report-approved ⟹ published-notice | 报告脱敏后对内发布；调用数据脱敏，输出至公示节点。 |

### 阶段8 整改跟踪与闭环管理（7）

| # | 文件夹 | 能力 | 输入 ⟹ 输出 | 链接语义 |
|---|---|---|---|---|
| 88 | audit-remedy-remedy-dispatch | audit.remedy.remedy-dispatch | final-issue-set ⟹ remedy-task | 问题拆解为整改任务派发责任主体；上游正式报告问题清单，调用工作流引擎。 |
| 89 | audit-remedy-remedy-plan-review | audit.remedy.remedy-plan-review | remedy-task ⟹ remedy-plan-approved | 审核责任单位整改方案；上游整改派单，调用审批流程。 |
| 90 | audit-remedy-remedy-progress-track | audit.remedy.remedy-progress-track | remedy-plan-approved ⟹ remedy-progress | 实时跟踪整改执行进度；上游整改方案，自动预警逾期。 |
| 91 | audit-remedy-remedy-effect-verify | audit.remedy.remedy-effect-verify | remedy-evidence,evidence-index ⟹ remedy-verdict | 核实整改证据验证有效性；上游整改提交，调用证据校验；直接调取底稿/证据历史资料（跨阶段）。 |
| 92 | audit-remedy-remedy-overdue-alert | audit.remedy.remedy-overdue-alert | remedy-progress ⟹ overdue-alert | 超期未完成整改自动预警；上游进度跟踪，调用消息通知。 |
| 93 | audit-remedy-remedy-close | audit.remedy.remedy-close | remedy-verdict ⟹ remedy-ledger | 验证通过任务销号；上游成效验证，输出至整改台账。 |
| 94 | audit-remedy-remedy-publish | audit.remedy.remedy-publish | remedy-ledger ⟹ published-remedy | 对内公示整改完成情况；调用数据脱敏，接受全员监督。 |

### 阶段内箭头流转（主链）

```text
阶段1 立项: 需求征集→战略对齐→立项申报→立项评审→项目库→优先级排序→通知书→资料报送→资料预检；项目库→项目组建分工
阶段2 风险: 政策扫描/行业对标/内控测绘/财务预警/断点识别/舞弊匹配→风险矩阵→等级赋值→高风险定位→应对建议（→热图）
阶段3 计划: 年度计划→项目方案→程序模板；抽样→样本量→排班→资源冲突；工时预算→版本管控
阶段4 实施: 底稿编制←凭证穿透/访谈/纪要/函证/监盘/盘点/协查；取证→疑点标记→进度填报→延期审批
阶段5 底稿: 证据归档→真实性校验→索引关联←底稿；勾稽校验→三级复核→电子签章→加密存储；版本对比/借阅审批
阶段6 定性: 疑点汇总→性质判定→违规匹配→金额计算→责任认定→分级分类→意见反馈→定性复核
阶段7 报告: 框架生成→问题描述→建议匹配→数据校验→多级审核→成果提炼→脱敏发布
阶段8 整改: 派单→方案审核→进度跟踪→逾期预警；成效验证→销号→结果公示
```

## 4. 治理优化层（6 个，⇠ 反向赋能）

| # | 文件夹 | 能力 | 输入 ⟹ 输出 | 链接语义 |
|---|---|---|---|---|
| 95 | audit-govern-project-quality-score | audit.govern.project-quality-score | project-flow-input ⟹ quality-score | 按质量标准量化评分全项目流程/成果；上游全流程数据，输出至质量考核。 |
| 96 | audit-govern-effect-evaluate | audit.govern.effect-evaluate | remedy-ledger ⟹ effect-report | 从整改/管理提升/价值创造维度评估审计价值；上游整改结果，输出至管理层。 |
| 97 | audit-govern-case-library-update | audit.govern.case-library-update | distilled-result ⟹ case-entry | 典型案例入库沉淀；上游审计成果，调用标签体系分类。 |
| 98 | audit-govern-issue-trend-analysis | audit.govern.issue-trend-analysis | history-issue-set ⟹ trend-report | 分析多期问题趋势/高发领域；上游历史问题库，调用可视化分析。 |
| 99 | audit-govern-rule-iteration | audit.govern.rule-iteration | trend-report ⟹ updated-rule-pack | 基于审计发现更新审计规则/预警模型；上游问题趋势，⇠ 反向赋能风险扫描、问题定性、底稿校验。 |
| 100 | audit-govern-method-distill | audit.govern.method-distill | project-review-input ⟹ method-library | 沉淀优秀审计方法/技巧更新方法库；上游项目复盘，⇠ 反向赋能计划与实施阶段。 |

### 反向闭环迭代

```text
规则库迭代 ⇠ 更新规则/预警模型 → 风险扫描、问题定性、底稿校验
问题趋势分析 ⇠ 调整年度审计重点 → 立项准备、计划编制
典型案例库 ⇠ 对标参考 → 风险评估、报告建议
审计方法沉淀 ⇠ 优化审计程序 → 计划、现场实施
质量评分/成效评估 ⇠ 优化考核与配置 → 立项、资源调度
```

## 5. 跨阶段直连（网状链接）

| 源插件 | 目标阶段 | 语义 |
|---|---|---|
整改成效验证 | 证据/底稿管理 | 直接调取历史底稿证据核验 |
问题定性复核 | 现场实施 | 追溯审计日志与访谈记录 |
风险应对建议 | 计划编制 | 直接输入审计重点 |
项目库管理 | 全流程各阶段 | 同步项目状态与进度 |

## 6. 与既有已验证审计插件的关系

| 既有插件（已验证，含 runtime） | 本网络对应能力 | 关系 |
|---|---|---|
| audit-ledger-quality | audit.foundation.finance-clean / quality-check | 复用其隔离运行时模式（ledger CSV → candidates） |
| audit-journal-anomaly | audit.risk.finance-anomaly-alert | 同规则引擎语义，可迁移 |
| audit-finding-draft | audit.finding.issue-type-judge 等 | 输入契约 anomaly-candidates → finding-draft 可复用 |
| audit-investigation-plan | audit.plan.project-scheme-build | 计划草稿契约可复用 |
| audit-report-draft | audit.report.* 系列 | report-draft 契约可复用 |
| audit-workpaper-export | audit.report.result-distill | workpaper 契约可复用 |
| audit-evidence-lineage | audit.foundation.lineage-track / audit.evidence.* | evidence-lineage 契约可复用 |

## 7. 数据契约（schema_ref）现状与待建

| schema_ref | 状态 | 用途 |
|---|---|---|
| ledger-artifact-ref / audit-quality-candidates / anomaly-candidates / finding-draft / investigation-plan-draft / report-draft / workpaper-export / evidence-lineage / artifact-ref / dataset-validation / metric-series / workflow / document-content / graph-candidate-set / graph-proposal-draft / retention-recommendation | 已有（contracts/jsonschema） | 可立即作为端口契约 |
| audit-source-set / clean-finance-set / standard-biz-set / master-map / demand-set / proposal-set / risk-matrix / high-risk-area / remedy-task / case-entry 等业务语义契约 | 规划中（随对应插件实现） | 端口契约已按命名约定先行声明 |

## 8. 运行约束

- 全部 `contract_only` 且 `read_only`：只做影子分析，不写领域表、不触达主机/网络/基础设施。
- 所有调用必经 Policy Gateway（`gateway_required: true`），带租户、trace_id、幂等键。
- 同一数据边两端 schema_ref 一致才可编译链接（与画布组网编译器语义一致）。
- 实现与验证按「先契约→再测试→后 runtime→注册绑定→精确策略」既有流程推进。

## 9. 生成清单（100 目录）

| # | 文件夹 | 插件 id |
|---|---|---|
| 1 | audit-foundation-multi-source-collect | audit.foundation.multi-source-collect |
| 2 | audit-foundation-finance-clean | audit.foundation.finance-clean |
| 3 | audit-foundation-biz-standardize | audit.foundation.biz-standardize |
| 4 | audit-foundation-master-mapping | audit.foundation.master-mapping |
| 5 | audit-foundation-lineage-track | audit.foundation.lineage-track |
| 6 | audit-foundation-data-mask | audit.foundation.data-mask |
| 7 | audit-foundation-data-encrypt | audit.foundation.data-encrypt |
| 8 | audit-foundation-metadata-manage | audit.foundation.metadata-manage |
| 9 | audit-foundation-quality-check | audit.foundation.quality-check |
| 10 | audit-foundation-metric-compute | audit.foundation.metric-compute |
| 11 | audit-foundation-tag-manage | audit.foundation.tag-manage |
| 12 | audit-foundation-fulltext-search | audit.foundation.fulltext-search |
| 13 | audit-foundation-ocr-extract | audit.foundation.ocr-extract |
| 14 | audit-foundation-nlp-process | audit.foundation.nlp-process |
| 15 | audit-foundation-rule-engine | audit.foundation.rule-engine |
| 16 | audit-foundation-viz-analysis | audit.foundation.viz-analysis |
| 17 | audit-foundation-workflow-engine | audit.foundation.workflow-engine |
| 18 | audit-foundation-permission-control | audit.foundation.permission-control |
| 19 | audit-mandate-demand-collect | audit.mandate.demand-collect |
| 20 | audit-mandate-strategy-align | audit.mandate.strategy-align |
| 21 | audit-mandate-annual-propose | audit.mandate.annual-propose |
| 22 | audit-mandate-proposal-score | audit.mandate.proposal-score |
| 23 | audit-mandate-project-library | audit.mandate.project-library |
| 24 | audit-mandate-priority-rank | audit.mandate.priority-rank |
| 25 | audit-mandate-notice-generate | audit.mandate.notice-generate |
| 26 | audit-mandate-material-submit | audit.mandate.material-submit |
| 27 | audit-mandate-material-precheck | audit.mandate.material-precheck |
| 28 | audit-mandate-team-forming | audit.mandate.team-forming |
| 29 | audit-risk-policy-risk-scan | audit.risk.policy-risk-scan |
| 30 | audit-risk-industry-benchmark | audit.risk.industry-benchmark |
| 31 | audit-risk-internal-control-map | audit.risk.internal-control-map |
| 32 | audit-risk-finance-anomaly-alert | audit.risk.finance-anomaly-alert |
| 33 | audit-risk-process-gap-detect | audit.risk.process-gap-detect |
| 34 | audit-risk-fraud-pattern-match | audit.risk.fraud-pattern-match |
| 35 | audit-risk-risk-matrix-build | audit.risk.risk-matrix-build |
| 36 | audit-risk-risk-level-assign | audit.risk.risk-level-assign |
| 37 | audit-risk-high-risk-locate | audit.risk.high-risk-locate |
| 38 | audit-risk-risk-heatmap | audit.risk.risk-heatmap |
| 39 | audit-risk-risk-response-advice | audit.risk.risk-response-advice |
| 40 | audit-plan-annual-plan-build | audit.plan.annual-plan-build |
| 41 | audit-plan-project-scheme-build | audit.plan.project-scheme-build |
| 42 | audit-plan-program-template-match | audit.plan.program-template-match |
| 43 | audit-plan-sampling-select | audit.plan.sampling-select |
| 44 | audit-plan-sample-size-compute | audit.plan.sample-size-compute |
| 45 | audit-plan-staff-schedule | audit.plan.staff-schedule |
| 46 | audit-plan-effort-budget | audit.plan.effort-budget |
| 47 | audit-plan-resource-conflict-detect | audit.plan.resource-conflict-detect |
| 48 | audit-plan-plan-version-control | audit.plan.plan-version-control |
| 49 | audit-field-site-checkin-track | audit.field.site-checkin-track |
| 50 | audit-field-workpaper-build | audit.field.workpaper-build |
| 51 | audit-field-voucher-drilldown | audit.field.voucher-drilldown |
| 52 | audit-field-confirm-letter | audit.field.confirm-letter |
| 53 | audit-field-inventory-count | audit.field.inventory-count |
| 54 | audit-field-asset-check | audit.field.asset-check |
| 55 | audit-field-interview-record | audit.field.interview-record |
| 56 | audit-field-meeting-minutes | audit.field.meeting-minutes |
| 57 | audit-field-cross-dept-inquiry | audit.field.cross-dept-inquiry |
| 58 | audit-field-suspicion-flag | audit.field.suspicion-flag |
| 59 | audit-field-evidence-photo | audit.field.evidence-photo |
| 60 | audit-field-audit-log | audit.field.audit-log |
| 61 | audit-field-progress-report | audit.field.progress-report |
| 62 | audit-field-extension-approve | audit.field.extension-approve |
| 63 | audit-evidence-evidence-archive | audit.evidence.evidence-archive |
| 64 | audit-evidence-evidence-verify | audit.evidence.evidence-verify |
| 65 | audit-evidence-workpaper-reconcile | audit.evidence.workpaper-reconcile |
| 66 | audit-evidence-workpaper-review3 | audit.evidence.workpaper-review3 |
| 67 | audit-evidence-workpaper-version-diff | audit.evidence.workpaper-version-diff |
| 68 | audit-evidence-evidence-index-link | audit.evidence.evidence-index-link |
| 69 | audit-evidence-workpaper-template-update | audit.evidence.workpaper-template-update |
| 70 | audit-evidence-e-signature | audit.evidence.e-signature |
| 71 | audit-evidence-workpaper-borrow-approve | audit.evidence.workpaper-borrow-approve |
| 72 | audit-evidence-workpaper-encrypt-store | audit.evidence.workpaper-encrypt-store |
| 73 | audit-finding-suspicion-merge | audit.finding.suspicion-merge |
| 74 | audit-finding-issue-type-judge | audit.finding.issue-type-judge |
| 75 | audit-finding-violation-clause-match | audit.finding.violation-clause-match |
| 76 | audit-finding-issue-amount-compute | audit.finding.issue-amount-compute |
| 77 | audit-finding-responsible-party-find | audit.finding.responsible-party-find |
| 78 | audit-finding-issue-grade | audit.finding.issue-grade |
| 79 | audit-finding-auditee-feedback | audit.finding.auditee-feedback |
| 80 | audit-finding-issue-final-review | audit.finding.issue-final-review |
| 81 | audit-report-report-frame-build | audit.report.report-frame-build |
| 82 | audit-report-issue-desc-write | audit.report.issue-desc-write |
| 83 | audit-report-advice-match | audit.report.advice-match |
| 84 | audit-report-report-data-check | audit.report.report-data-check |
| 85 | audit-report-report-multi-review | audit.report.report-multi-review |
| 86 | audit-report-result-distill | audit.report.result-distill |
| 87 | audit-report-notice-mask-publish | audit.report.notice-mask-publish |
| 88 | audit-remedy-remedy-dispatch | audit.remedy.remedy-dispatch |
| 89 | audit-remedy-remedy-plan-review | audit.remedy.remedy-plan-review |
| 90 | audit-remedy-remedy-progress-track | audit.remedy.remedy-progress-track |
| 91 | audit-remedy-remedy-effect-verify | audit.remedy.remedy-effect-verify |
| 92 | audit-remedy-remedy-overdue-alert | audit.remedy.remedy-overdue-alert |
| 93 | audit-remedy-remedy-close | audit.remedy.remedy-close |
| 94 | audit-remedy-remedy-publish | audit.remedy.remedy-publish |
| 95 | audit-govern-project-quality-score | audit.govern.project-quality-score |
| 96 | audit-govern-effect-evaluate | audit.govern.effect-evaluate |
| 97 | audit-govern-case-library-update | audit.govern.case-library-update |
| 98 | audit-govern-issue-trend-analysis | audit.govern.issue-trend-analysis |
| 99 | audit-govern-rule-iteration | audit.govern.rule-iteration |
| 100 | audit-govern-method-distill | audit.govern.method-distill |
