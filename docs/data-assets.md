# 数据资产索引 · 审计素材资料合集（对照基准）

> 建立时间：2026-09-09
> 数据位置：`E:\数据`（README 原文写 `G:\数据`，实际目录已位于 `E:\数据`；`datasets/` 子目录同理）
> 数据形态：19 个开源项目（16 git 仓库 + 1 ClawHub 本地还原 + 2 HF 数据集）+ `datasets/` 8 项下载数据集（3 项可用）
> 定位：**只读对照基准**。本索引把每项资产映射到项目既有能力，后续每个功能在 `docs/status.md` 或契约测试中声明"用什么数据验证"时引用 `DATA-ASSET-xx` 编号。**不复制入库、不加载大文件、不修改 `E:\数据` 内容**（AGENTS.md：不删除或覆盖用户文件；本阶段不扩展业务范围）。

---

## 一、资产总览

| 编号 | 资产 | 形态 | 规模/说明 | 位置（相对 `E:\数据\`） |
| --- | --- | --- | --- | --- |
| DATA-ASSET-01 | Intelligent-Audit-Decision-Support | git | KG-GNN 企业关联交易审计，2,100 节点 + 4,227 边匿名样本 | `01-审计知识图谱项目/Intelligent-Audit-Decision-Support` |
| DATA-ASSET-02 | economic_audit_knowledge_graph | git | 经济责任审计知识图谱（爬虫、关系抽取、领域词汇判定），373MB | `01-审计知识图谱项目/economic_audit_knowledge_graph` |
| DATA-ASSET-03 | AutoAudit 智能审计系统 | git | 知识图谱 + RAG + 强化学习（对话 Agent、风险评估、合规检查） | `01-审计知识图谱项目/AutoAudit-intelligent-audit-system` |
| DATA-ASSET-04 | Hound | git | 关系优先的知识图谱安全审计 Agent（原 `muellerberndt/hound` 301 迁移至 `scabench-org/hound`） | `02-图谱审计Agent/hound` |
| DATA-ASSET-05 | Skynet Audit | git | 基于代码知识图谱的 LLM 安全审计框架 | `02-图谱审计Agent/skynet` |
| DATA-ASSET-06 | security_audit_compliance_agent_v2 | git | IoT 网络安全审计 + 知识图谱 + RAGAS（含数据集和脚本） | `02-图谱审计Agent/security_audit_compliance_agent_v2` |
| DATA-ASSET-07 | nigo-skills | git | 审计师 AI 技能包（财报、关联方核查、报告勾稽，10+ 技能） | `03-AI审计技能包/nigo-skills` |
| DATA-ASSET-08 | financeskills | git | 金融/审计/合规 AI 技能全集（IFRS/GAAP） | `03-AI审计技能包/financeskills` |
| DATA-ASSET-09 | PCCA-Benchmark | HF 数据集 | 工业因果知识图谱审计基准 225 案例（CC-BY-4.0），zip 520.1MB（64,251 条目，样例已解压） | `04-审计数据集与基准/PCCA-Benchmark/` |
| DATA-ASSET-10 | Audit-Risk-Classification | git | 印度审计署企业审计风险数据集（UCI 公开） | `04-审计数据集与基准/Audit-Risk-Classification` |
| DATA-ASSET-11 | VynFi Group Audit | HF 数据集 | 2,000 实体跨国合并审计模拟（Apache-2.0），`enterprise_2000_v5351.tar.zst` 829.4MB（完整语料 8.35GB 未下载） | `04-审计数据集与基准/VynFi-Group-Audit/` |
| DATA-ASSET-12 | org-dep-audit-plugin | git | npm 依赖 CVE 与供应链风险审计（Claude Code 插件） | `05-依赖分析与插件审计/org-dep-audit-plugin` |
| DATA-ASSET-13 | AiCodeAudit | git | 基于大模型的自动代码审计（图结构构建依赖关系），20MB | `05-依赖分析与插件审计/AiCodeAudit` |
| DATA-ASSET-14 | pgokf | git | PostgreSQL 扩展：知识图谱 + 全文/语义/混合检索 + 审计追踪（pgvector） | `06-PgVector知识图谱基础设施/pgokf` |
| DATA-ASSET-15 | firm-memory-audit-pack | ClawHub 本地还原 | pgvector 配置校验 + 知识图谱完整性检查（孤立节点/循环检测），附纯 Python 等价脚本（已实测） | `06-PgVector知识图谱基础设施/firm-memory-audit-pack` |
| DATA-ASSET-16 | rag-staleness-check | git | pgvector/Qdrant/Chroma 索引陈旧/孤立/重复检测（未发布 PyPI，需源码安装） | `06-PgVector知识图谱基础设施/rag-staleness-check` |
| DATA-ASSET-17 | audit-graph-explorer | git | Neo4j + Cypher 关系驱动审计分析 | `07-补充参考/audit-graph-explorer` |
| DATA-ASSET-18 | coop-knowledge-lint | git | 知识图谱健康审计：孤立实体、陈旧来源、矛盾检测 | `07-补充参考/coop-knowledge-lint` |
| DATA-ASSET-19 | KONTRAST | git | 文本、表格与知识图谱间的知识一致性检测，1.29GB | `07-补充参考/KONTRAST` |
| DATA-ASSET-20 | FinAudit CFA/CPA 双语题库 | parquet | 596 例金融/会计多选题（中英），856KB | `datasets/07-finmmeval-cfa-cpa/` |
| DATA-ASSET-21 | Audit Data (UCI id=475) | zip | 777 实例印度企业审计风险分类（audit_risk.csv + trial.csv），28KB | `datasets/08-audit-data-uci/audit_data.zip` |
| DATA-ASSET-22 | Signature Detection | zip | 178 张签名检测图像（YOLO 格式，143 训练/35 验证），11.9MB | `datasets/04-signature_detection/signature.zip` |

> `datasets/` 另 5 项未就绪：SynFinTabs（下载中 7.1GB）、AUDITS（暂缓 35.4GB）、AIForge-Doc-v1（HF gated 需 token）、MyFinMarkdown-sample（HF 私有 401）、Financial Audit Transactions（Kaggle 需凭据）、AuditLLM（仓库已清空不可用）——不做编号，待可用后补。

---

## 二、资产 → 项目能力映射

| 项目能力（Phase/里程碑） | 能力说明 | 引用资产 | 引用方式 |
| --- | --- | --- | --- |
| Phase 5 图谱抽取 | 审计知识图谱实体/关系抽取 | DATA-ASSET-01（KG-GNN 关联交易 2100 节点/4227 边，作抽取结果对照）、DATA-ASSET-02（爬虫/关系抽取/领域词汇，作抽取管线参考）、DATA-ASSET-09（225 工业因果案例，作因果边抽取基准） | 抽取管线在 `docs/status.md` 声明验证数据时引用 |
| Phase 6 合并仲裁 | 跨源实体合并与仲裁 | DATA-ASSET-01/02（多源图谱实体对齐场景）、DATA-ASSET-18（孤立/矛盾检测，作仲裁后健康检查对照） | 合并仲裁测试的对照样本 |
| Phase 7 审计证据链 | 证据锚定、append-only 链、可回溯 | DATA-ASSET-19（文本/表格/KG 一致性检测，作证据一致性与锚定对照）、DATA-ASSET-15（图谱完整性检查） | 证据链验证功能的"一致性检测"参照实现 |
| Phase 8 量化证据链 | 数值/统计证据的可信度量化 | DATA-ASSET-20（596 例财务/会计题，作领域评测）、DATA-ASSET-21（777 实例审计风险分类，作风险量化验证） | 量化证据链的评测数据 |
| Phase 9 AIOps 治理 | 告警/事件/修复提案/ChangeRequest/canary 只读血缘与策略化入口 | DATA-ASSET-14（pgvector+审计追踪扩展，作审计追踪基础设施对照）、DATA-ASSET-16（索引陈旧/孤立检测，作 24×7 数据质量监控参照） | 治理血缘读取的"数据健康"参照 |
| CW0 可观测性 | 租户化 trace/code-map、失败证据短事务、快照锁 | DATA-ASSET-13（代码依赖图结构，作 code-map 下钻到代码层的对照语料）、DATA-ASSET-05（代码知识图谱安全审计，作代码层血缘参照） | trace-locate/code-map 功能的代码层下钻样本 |
| 审计 Agent / 技能编排 | 能力蓝图（ledger-quality、finding.draft 等）与技能包 | DATA-ASSET-03（KG+RAG+RL 对话审计）、DATA-ASSET-04（关系优先 KG 审计 Agent）、DATA-ASSET-07/08（审计技能包，作 capability_contract 对照） | 蓝图能力命名与技能划分的行业参照 |
| 供应链/依赖审计 | npm/CVE/供应链风险 | DATA-ASSET-12（npm 依赖 CVE 审计插件）、DATA-ASSET-13（依赖图构建） | 依赖分析功能的插件/图谱实现参照 |
| 图谱查询/可视化 | 关系驱动审计分析 | DATA-ASSET-17（Neo4j+Cypher 关系分析）、DATA-ASSET-06（IoT 审计+KG+RAGAS 含评估脚本） | 图谱查询与审计评估的对照实现 |
| 图像类审计素材 | 签名检测 | DATA-ASSET-22（178 张签名检测图，YOLO） | 图像证据类功能的素材样本（当前无对应能力，预留） |

---

## 三、引用约定（每个功能声明数据支撑时）

1. 在 `docs/status.md` 对应阶段/里程碑段落，用「数据支撑：`DATA-ASSET-xx`（一句话说明用途）」声明。
2. 契约测试若引用资产，只读不写：用测试内构造的迷你副本（如 PCCA 案例结构的 1 个 JSON 子集），**禁止**直接加载 `E:\数据` 大文件进测试库。
3. 资产只作**对照/基准/评测**，不改变本阶段业务范围（AGENTS.md 边界）；需要把某资产作为**模拟数据种子**入库时，视为下一阶段任务，先经用户拍板再走"先契约测试→最小改动→验证"流程。
4. 访问路径以 `E:\数据` 为准（README 内 `G:\数据` 为生成时位置，已迁移，勿用 G 路径执行解压命令）。

---

## 四、边界与风险

- **只读**：本索引不触发任何资产读取/解压/复制；大文件（PCCA zip 520MB、VynFi tar.zst 829MB、economic_audit 373MB、KONTRAST 1.29GB）按需人工解压后引用样例，不进测试库。
- **许可证**：PCCA=CC-BY-4.0、VynFi=Apache-2.0、firm-memory-audit-pack=MIT；其余仓库以各自 LICENSE 为准。商用/二次分发前需逐项核验。
- **数据卫生**：`E:\数据` 内 `datasets/_dl_datasets.py` 等脚本与下载缓存属用户既有文件，本索引不执行、不修改。
