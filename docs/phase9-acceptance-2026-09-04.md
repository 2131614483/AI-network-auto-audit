# Phase 9 GUI/API/知识/图谱/审计/量化/AIOps 端到端验收

验收日期：2026-09-04  
验收范围：本机 `audit_network`、PostgreSQL 16、Electron 桌面控制台，以及 `G:\数据` 的受控小样本。  
结论：**不通过完整 Phase 9 验收；当前结论为“部分通过（基础纵向切片已验证）”。**

这是一份如实的验收记录。“测试存在”或“表结构存在”不等于功能已完成，尤其不能把没有实际 embedding、OCR、生产演练或 GUI 工作台的能力写成已交付。

## 环境与边界

| 项目 | 实测结果 |
| --- | --- |
| 数据库 | 本机 PostgreSQL 16.13，`audit_network`，迁移 `0030_graph_visualization_policy` |
| 扩展 | `vector 0.8.0` 已安装 |
| 数据权限 | 仅在 `audit_network` 内验证 `audit_app` 创建、读、写、删；未授予 BYPASSRLS，未操作其他数据库 |
| 真实外部样本 | `G:\数据` 中 2 个 Markdown、1 个 CSV；原始目录未修改、未全量扫描 |
| 桌面入口 | Electron + React + Ant Design；`web/` 仅保留兼容测试，不作为用户入口 |

## 实测通过项

### 知识导入、策略与图谱小样本

- 三个真实文件经知识上传 API 和 Policy Gateway 入库：3 个文档、391 个分块；批次 `77fc3ac7-786e-41db-afd9-8c59485578a4` 已完成。
- `validation-g-data` 图空间已实测写入 2 个节点、1 条关系，并完成有节点/边/跳数预算的邻居读取。
- 图谱桌面工作台已参考 `D:\pythonpro\math` 的可视化实现：经 Policy Gateway 的 `graph.visualize.read` 读取受限拓扑（默认 180 节点、360 关系），ECharts 力导向画布支持缩放、拖拽、关联高亮、类型图例和点击节点后的局部关系检查；截图 `D:\pythonpro\audit_network\.data\phase9-graph-20260904.png` 已人工核验。
- 真实路由的 Policy Gateway 会持久化 ALLOW/REQUIRE_APPROVAL 决策；本轮修复了允许写操作未入策略审计链的问题。
- 初次验收发现 448 个历史语义块、0 条 embedding。整改后已用本机 `qwen3-embedding:0.6b` 实际生成 1024 维向量，建立全文 GIN 与向量 HNSW；当前抽样库共有 10 条向量记录，其中 8 条来自真实本地模型、2 条来自隔离集成测试。
- 新增知识统计、文档、批次、增量向量化以及 keyword/vector/hybrid 检索 API；混合检索会明确报告是否降级。

### 审计、量化、AIOps 最小纵向切片

以下均使用本机 PostgreSQL 的真实读写；测试数据为隔离的临时 CSV/Markdown，不是对 G 盘财务或行情数据的全量处理。

| 领域 | 已验证行为 | 结果 |
| --- | --- | --- |
| 审计 | CSV 总账质量检查、异常候选、原始 Artifact → Evidence → Finding | 通过 |
| 量化 | CSV 点时顺序校验、模拟回测、结果持久化、未来函数拦截 | 通过；仅模拟盘 |
| AIOps | 告警→提案；未放行即阻断；变更单哈希绑定；canary 失败回滚 | 通过；dry-run/canary 验证，未执行生产 Playbook |
| 跨域切片 | 知识→图谱→审计→量化→AIOps | 通过；为服务层直接调用的最小回归，不包含 GUI 编排与完整策略工作流 |

### 自动化检查

| 命令/范围 | 结果 |
| --- | --- |
| `pytest` 全量 Python 检查 | 69 passed；有 1 条 FastAPI TestClient 弃用警告 |
| `ruff check .` | 通过 |
| `mypy packages apps` | 通过（26 个源） |
| `npm run typecheck && npm test` | 通过（4 个兼容界面测试） |
| `npm --prefix desktop run typecheck && npm --prefix desktop run test` | 通过（2 个桌面模型测试） |

## Phase 9 不通过的阻断项

| Phase 9 要求 | 当前事实 | 结论 |
| --- | --- | --- |
| Mission、Workflow、Agent、Plugin、Policy、Knowledge、Audit、Quant、AIOps 等完整工作台 | 已扩展为 10 个桌面视图；图谱已具备受预算真实可视化与局部关系检查，任务/审计/量化/AIOps 仍主要是实时运行投影，尚非完整业务操作台 | 部分完成 |
| GUI 显示每项关键动作的来源、规则、执行、结果 | 最近策略裁决表已显示 capability、规则说明、风险分、Trace 和结果；尚无统一任务图和动作回放 | 部分完成 |
| 知识工厂：PDF/图片 OCR、embedding/HNSW、检索、图谱抽取、导入进度 | embedding/HNSW、全文/向量/混合检索、文档与批次进度已完成；MinerU/OCR、页码引用和图谱自动抽取仍缺失 | 部分完成 |
| 多图谱大规模路由和性能指标 | 仅小图预算查询；没有 10 万节点/100 万边 Golden Query 和 P95 报告 | 未完成 |
| 审计正式报告与独立 QA 工作台 | 有证据链最小测试；没有 Excel/Hound/正式报告工作台 | 未完成 |
| 量化生产一致性/漂移与公开数据隔离演练 | 仅 CSV 模拟回测 | 未完成 |
| AIOps 24×7 | 没有 Temporal/NATS、真实 Playbook、OTel/watchdog、故障注入和 14 天运行证据 | 未完成 |
| 灾备、RPO/RTO、五条完整 E2E 演示 | 未有可复核的恢复演练和五条 GUI/API 完整演示包 | 未完成 |

因此，本轮解决了向量检索、知识管理可视化和跨域运行可观测性三组阻断；剩余项目仍使当前版本不能标记为“Phase 9 完成”或“可长期无人值守生产运行”。

## OCR+CPA 问答综合系统：可借鉴与禁止直接迁移

参考项目：`D:\pythonpro\ollama项目\审计OCR+CPA问答综合系统`。本次只做只读代码审查，未运行、未修改该项目或其数据库。

| 可借鉴能力 | 迁入方式 | 不能直接复制的部分 |
| --- | --- | --- |
| 知识库的列表、筛选、详情、浏览体验 | 建设 Knowledge 工作台：文件夹视图、批次进度、文档版本、块/页码证据、回收站和恢复 | 参考项目的直连删除端点；本项目必须变更为 ChangeSet + 回收站 + 策略裁决 |
| vector、BM25、hybrid、graph 四种检索入口 | 设计为受发布版本、检索预算和来源说明约束的 Retrieval Adapter Registry；embedding 缺失时明确显示全文降级 | 不能把 `double precision[]` 当作 pgvector/HNSW，也不能隐藏检索降级 |
| OCR 批处理进度与失败状态 | 作为本地 MinerU/OCR worker 的任务进度、断点恢复、失败重试与可审计事件 | 不允许 GUI 或 worker 绕开 Policy Gateway 直接写入活动知识 |
| 问答后提炼、去重、矛盾识别 | 输出仅能进入影子 ChangeSet；Reviewer 与提案 Agent 分离，人工/规则放行后发布 | 参考项目“dream”服务会直接 UPDATE/DELETE 知识，不能用于本项目的证据与发布历史 |
| 本地 Ollama embedding/模型调用方式 | 封装为可替换的本地模型适配器，记录模型、输入 SHA、耗时、错误和降级状态 | 不能硬编码管理员账户、端口和密码，也不能跨数据库直接访问 |

优先落地顺序：先完成 Knowledge 工作台与批次可观测性；再完成本地解析/embedding 适配器和“文本降级”检索；其后实现受预算约束的多图路由；最后再接入受 ChangeSet 治理的自我进化。

## 下一次完整验收的准入条件

1. 每个工作台可从桌面端完成关键读写，并可展示来源、策略、审批、执行日志和结果；
2. 使用真实本地 PDF/图片样本跑通 MinerU/OCR、embedding、HNSW/全文降级、引用回链和发布；
3. 提供知识、代码审计、财务审计、量化、AIOps 五条独立的 GUI/API E2E 演示数据与可重复脚本；
4. 完成图规模 Golden Query、权限隔离、故障注入、恢复演练，并记录 RPO/RTO；
5. 完成 24×7 运行期的可观测性与无静默丢任务证据。
