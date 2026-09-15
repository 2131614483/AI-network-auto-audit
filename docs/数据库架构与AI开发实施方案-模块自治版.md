# 数据库架构与 AI 开发实施方案——模块自治版

> 版本：1.0-modular  
> 日期：2026-09-03  
> 上位设计：《超级审计与量化智能中枢总体设计-模块自治版.md》  
> 定位：指导多个 AI/开发者分别完成独立模块，最后通过公共契约、事件和 Integration Hub 汇总。

## 0. 执行摘要

数据库从“一套大 schema + 一条全局 Alembic 链 + 所有模块强制 RLS/Policy”调整为：

```text
每模块独立数据库或独立 schema
每模块独立 migration/version table
每模块独立应用角色和测试数据
模块间禁止数据库外键和跨库直查
最终通过 API/Event/ArtifactRef 汇总
```

开发态允许单模块使用固定 `local-dev` 身份和模块内白名单，不要求中央 Policy Service、审批表或统一 IAM 在线。集成态再启用统一身份、RLS、审批、授权租约和跨域信息墙。

## 1. 不再采用的开发前置条件

以下规则不再作为单模块 MVP 的前置条件：

- 每张业务表从第一天都有统一 `tenant_id` 并强制 RLS；
- 每个本地写操作都调用中央 Policy Gateway；
- 每个 GUI 动作都有人工审批记录；
- 所有模块表由根目录单一 Alembic head 管理；
- 模块之间通过跨 schema 外键连接；
- 所有请求必须由调用者手工提供 `trace_id`、身份和幂等键；
- 未完成 Agent 调度、插件注册或统一 GUI 就不能调试知识、审计或量化业务。

替代规则：

- `trace_id` 由模块中间件自动生成，集成调用可覆盖；
- 幂等键只对可重放写入和外部副作用强制，普通本地草稿编辑使用资源版本；
- 开发态身份由 `LocalIdentityAdapter` 提供；
- 模块内权限由 `LocalAllowlistPolicy` 判定；
- 多租户模块可从一开始带 `workspace_id`，但是否启用 PostgreSQL RLS 由运行档位决定；
- 所有生产安全约束必须有 Integration Adapter 和测试，不要求阻塞模块业务 MVP。

## 2. 推荐数据库拓扑

### 2.1 本机独立开发

默认使用一个本机 PostgreSQL 16 实例、多个数据库：

| 数据库 | 所属模块 | 默认角色 |
| --- | --- | --- |
| `audit_kb_dev` | Knowledge Library | `kb_owner` / `kb_app` |
| `audit_graph_dev` | Knowledge Graph | `graph_owner` / `graph_app` |
| `audit_audit_dev` | Audit | `audit_owner` / `audit_app_local` |
| `audit_quant_dev` | Quant | `quant_owner` / `quant_app` |
| `audit_risk_dev` | Risk & Policy | `risk_owner` / `risk_app` |
| `audit_agent_dev` | Agent Orchestrator | `agent_owner` / `agent_app` |
| `audit_plugin_dev` | Plugin Runtime | `plugin_owner` / `plugin_app` |
| `audit_aiops_dev` | AIOps | `aiops_owner` / `aiops_app` |
| `audit_hub_dev` | Integration Hub 投影 | `hub_owner` / `hub_app` |

这些数据库可共用 PostgreSQL 进程，但生命周期、migration、备份和测试数据相互独立。某模块重建开发库不会影响其他模块。

资源受限时可退化为一个数据库多个 schema，但必须满足：

- 每模块使用独立 Alembic version table，例如 `kb.alembic_version`；
- 应用角色只拥有本模块 schema；
- 禁止跨模块外键、触发器和视图；
- 集成查询仍走 API，不因同库部署而直查表。

### 2.2 集成环境

集成环境保持模块数据库独立，增加：

- `audit_hub_integration`：模块注册、事件 offset、契约版本、只读投影；
- Risk/Policy 数据库：统一策略、审批、授权租约；
- 可选事件总线；第一版可用 PostgreSQL Outbox + HTTP consumer；
- 统一对象访问代理，但原始对象仍由所属模块管理。

### 2.3 生产环境

生产可按容量把多个逻辑数据库部署到同一 PG 集群，也可拆分实例。部署位置不改变模块所有权。禁止为了 JOIN 方便破坏边界。

## 3. 独立迁移体系

### 3.1 目录

```text
modules/knowledge_library/migrations/
modules/knowledge_graph/migrations/
modules/audit/migrations/
modules/quant/migrations/
modules/risk/migrations/
modules/agent/migrations/
modules/plugin/migrations/
modules/aiops/migrations/
modules/integration_hub/migrations/
```

每个目录有自己的 `alembic.ini`、`env.py`、revision 命名空间和 version table。禁止一个模块的 migration 创建或修改另一个模块的表。

### 3.2 版本命名

建议：

```text
kb_0001_base
kb_0002_ingest_jobs
graph_0001_base
audit_0001_base
quant_0001_base
```

revision 保持短于 Alembic version 列长度。模块 release 记录自己依赖的公共契约版本，而不是依赖其他模块 migration revision。

### 3.3 升级与回滚

- 从空库升级到 head 是每模块必测项；
- schema rollback 仅对可逆 DDL 执行；
- 证据、发布和执行历史采用前向补偿 migration，不用 destructive downgrade；
- 应用启动不得自动 migration；由模块启动脚本显式执行；
- 集成测试分别创建/销毁目标明确的临时数据库。

## 4. 公共数据库约定：保持最小

公共约定只统一跨模块必需内容：

- 主键使用 UUID；
- 时间使用 `timestamptz`/UTC；
- 外部引用使用 `<module, kind, id, version>`，不使用跨库 FK；
- 原始文件、证据和发布记录软删除或版本化；
- 写入产生 `created_at/updated_at`；
- 可重放命令使用 `idempotency_key`；
- 需要并发编辑的资源使用 `row_version`；
- 事件使用 schema version；
- JSONB 只存扩展属性，状态、版本、SHA、时间和所有权保持结构化列。

不统一：

- 所有模块同一状态枚举；
- 所有表同一租户字段；
- 所有模块同一 embedding 维度；
- 所有模块同一发布模型；
- 所有模块同一 ORM。

## 5. Knowledge Library 数据库

独立目标：文件上传后成为可检索、可追溯、可修改、可回收的知识资产；PDF/图片经本地 MinerU，音视频经本地转写器。

### 5.1 核心表

| 表 | 用途 |
| --- | --- |
| `folders` | 虚拟文件夹树，`parent_id/name/path_key/row_version` |
| `items` | 文件/文档条目，名称、媒体类型、当前版本、处理状态 |
| `item_versions` | SHA、大小、存储引用、来源、版本、提取器版本 |
| `ingest_jobs` | 上传/扫描/重处理任务及检查点 |
| `extract_jobs` | MinerU、OCR、音视频转写的本地任务接口 |
| `documents` | 规范化文档、标题、语言、页数、状态 |
| `chunks` | 文本块、页码/时间码/结构路径、全文检索列 |
| `embeddings` | 模型、维度、向量、embedding release |
| `tags` / `item_tags` | 文件与文档标签 |
| `trash_entries` | 回收站、原位置、删除人、到期、恢复状态 |
| `outbox` | `item.ready/document.ready/item.recycled` 等事件 |

### 5.2 文件系统

```text
data/knowledge/
  blobs/<sha256-prefix>/<sha256>
  working/<job-id>/
  derived/<item-version-id>/
  quarantine/
```

数据库保存内容寻址引用，不把用户原始绝对路径当作访问权限。上传先写 working，计算 SHA 后原子移动到 blob；同 SHA 复用 blob，不重复保存。

### 5.3 本地提取器接口

```text
LocalExtractor.probe() -> health/capabilities
LocalExtractor.submit(ArtifactRef, options) -> ExtractJobRef
LocalExtractor.poll(job_id) -> progress/result/error
LocalExtractor.cancel(job_id)
```

- `mineru-local`：PDF、扫描件、图片；输出 Markdown、布局块、表格、图片引用、页码坐标；
- `media-local`：音频/视频；输出转写文本、说话人、时间码、关键帧引用；
- 未安装时 `extract_jobs.status=waiting_runtime`，文件仍安全入库；
- 解析器只能读取 job 指定的 blob，不能接收任意 shell；
- 解析结果先进入版本草稿，成功验证后切换当前版本。

### 5.4 图谱连接

Knowledge 不写 Graph 私有表。文档发布后发送：

```text
knowledge.document.ready
  document_ref
  chunk_refs
  provenance
  suggested_graph_space
```

Graph 模块自行抽取实体关系并返回 `graph.release.ready`。Knowledge 只保存 GraphReleaseRef。

## 6. Knowledge Graph 数据库

| 表 | 用途 |
| --- | --- |
| `spaces` | L0–L4 图空间和层级 |
| `nodes` / `node_versions` | 稳定 ID 与版本内容分离 |
| `edges` / `edge_versions` | 关系及有效期、置信度、来源 |
| `aliases` / `redirects` | 实体对齐、merge 后旧 ID 重定向 |
| `bridges` | 有限跨图连接和配额 |
| `change_sets` / `operations` | 编辑、merge、split、删除提案 |
| `releases` | staging/active/superseded |
| `conflicts` | 冲突事实、候选解决方案 |
| `trash_entries` | 节点/边回收站 |
| `query_metrics` | Golden Query、预算、延迟、Recall |
| `outbox` | 图发布和节点变更事件 |

Graph 在开发态可使用单 workspace，无需 Risk 服务即可编辑草稿；发布、跨图桥和硬删除在集成态接入 PolicyPort。

## 7. Audit 数据库

| 表 | 用途 |
| --- | --- |
| `engagements` | 审计项目与范围 |
| `data_imports` | 总账/凭证/合同/代码数据导入 |
| `evidence` / `evidence_slices` | 原始证据引用与定位 |
| `procedures` / `procedure_runs` | 审计程序和执行 |
| `candidates` | 规则/模型异常候选 |
| `claims` | 可验证主张 |
| `findings` | 确认发现、影响、建议 |
| `workpapers` / `reports` | 底稿和报告版本 |
| `qa_reviews` | 独立 QA 结论 |

Audit 只保存 Knowledge/Graph 的公共引用，不外键到其表。开发态使用本模块样例数据；集成态信息墙禁止将审计机密事件路由给 Quant。

## 8. Quant 数据库

| 表 | 用途 |
| --- | --- |
| `datasets` / `snapshots` | 点时数据快照和 SHA |
| `factors` / `factor_versions` | 因子定义与代码哈希 |
| `experiments` | 参数、数据、代码、环境血缘 |
| `backtests` / `trades` / `metrics` | 回测结果 |
| `signals` | 研究/模拟信号，明确用途 |
| `portfolios` / `risk_evaluations` | 组合与风险评估 |
| `drift_reports` | 数据/模型/表现漂移 |

开发态不创建券商凭证或真实订单表。真实执行属于单独的 Production Trading Adapter，默认不存在。

## 9. Risk、Agent、Plugin 与 AIOps 数据库

### 9.1 Risk

`policy_sets/rules/decisions/approvals/leases/freezes/tool_calls`。本模块独立开发 Policy Simulator；其他模块通过 PolicyPort 使用本地适配器，不直接依赖这些表。

### 9.2 Agent

`missions/workflow_definitions/workflow_versions/runs/tasks/task_dependencies/agent_runs/checkpoints/leases/outbox`。任务 payload 只包含公共引用，不复制领域大对象。

### 9.3 Plugin

`plugins/versions/capabilities/manifests/installations/ui_contributions/runtime_health`。插件数据库不保存领域业务结果；结果回到所属模块 ArtifactStore。

### 9.4 AIOps

`alerts/incidents/symptoms/rca_candidates/playbooks/proposals/change_requests/executions/verifications/rollbacks`。开发态只允许模拟执行器；接入生产执行器必须切到 Integration/Production Policy。

## 10. 开发态权限设计

### 10.1 数据库角色

每模块最少两个角色：

- `<module>_owner`：只用于显式 migration；
- `<module>_app`：只访问本模块运行表；不得创建 schema、角色或扩展。

可选 `<module>_test` 拥有临时测试库，不连接用户正式开发库。

### 10.2 RLS 切换

领域表建议保留 `workspace_id`，但通过两套迁移/策略启用方式处理：

- MODULE_DEV：固定 workspace，中间件注入；RLS 可不启用；
- MODULE_TEST：启用 RLS 并执行双 workspace 越权测试；
- INTEGRATION/PRODUCTION：强制 RLS，应用角色无 `BYPASSRLS`。

业务 repository 始终按 workspace 查询，避免进入集成态后大改 SQL。

### 10.3 模块白名单

每模块有独立配置：

```yaml
mode: MODULE_DEV
allow:
  - knowledge.upload
  - knowledge.folder.manage
  - knowledge.extract.mineru.local
  - knowledge.extract.media.local
write_roots:
  - D:/pythonpro/audit_network/.data/knowledge
network: deny
hard_deny:
  - filesystem.recursive_delete_outside_module
  - credential.read_undeclared
  - broker.order.live
  - aiops.production_change
```

开发者不需要在中央数据库手工插入 PolicySet 才能测试模块。切到 Integration 时，配置改用 RemotePolicyGateway。

## 11. 事件与同步

每个模块拥有自己的 outbox。开发态可用进程内 consumer 或 PostgreSQL 轮询；集成态换成事件总线适配器。

规则：

- 业务数据与 outbox 同事务；
- event_id 全局唯一；
- consumer 维护 inbox/processed_event，至少一次投递、幂等处理；
- 事件 payload 不包含大文件和机密正文；
- schema 版本不兼容时进入 dead letter，不默默丢弃；
- Hub 投影可从事件重放重建。

## 12. Integration Hub 数据库

只保存：

- `modules`：descriptor、endpoint、health、契约版本；
- `contract_compatibility`：生产者/消费者兼容结果；
- `event_offsets` / `dead_letters`：路由状态；
- `resource_index`：跨模块公共引用索引；
- `workflow_links`：跨模块 Saga 步骤；
- `ui_contributions`：统一 Shell 注册缓存；
- `projections_*`：可重建的状态卡片和搜索索引。

禁止保存：原始审计证据正文、完整市场行情、知识 blob、图谱权威节点、模块私有审批细节。

## 13. API 与契约优先顺序

每个模块先实现：

```text
GET  /health
GET  /descriptor
GET  /capabilities
GET  /openapi.json
```

写 API 根据动作选择：

- 可安全重放：强制 `Idempotency-Key`；
- 资源编辑：强制 `If-Match`/`row_version`；
- 本地草稿：允许模块生成 idempotency key；
- 外部副作用：集成态强制 PolicyDecision/lease。

统一错误 Envelope：

```json
{
  "code": "KNOWLEDGE_EXTRACTOR_UNAVAILABLE",
  "message": "local MinerU is not configured",
  "trace_id": "uuid",
  "retryable": true,
  "details": {"extractor": "mineru-local"}
}
```

## 14. 仓库与多 AI 并行开发

推荐单仓多模块，但每个 AI 只拥有一个模块目录：

```text
audit_network/
  contracts/                 # 公共契约，变更需兼容评审
  modules/
    knowledge_library/
    knowledge_graph/
    audit/
    quant/
    risk/
    agent/
    plugin/
    aiops/
    integration_hub/
  shell/                     # 统一 GUI
  integration-tests/
  docs/
```

并行规则：

1. 模块 AI 不修改根 API 大文件；路由在模块内注册。
2. 模块 AI 不修改其他模块 migration。
3. 公共契约变更先增加新版本和兼容测试，不直接破坏旧字段。
4. 每模块使用独立测试数据库名和临时目录。
5. 根集成任务只组装已通过模块验收的版本。
6. 模块失败不会阻塞其他模块开发，只阻塞依赖它的集成场景。

## 15. 开发与验收阶段

### M0：模块骨架与契约

- 创建独立目录、配置、health、descriptor；
- 定义 API/事件/ArtifactRef；
- 建立独立数据库、migration、角色和空库测试；
- 建立 LocalIdentity、LocalPolicy、LocalEventBus 适配器。

验收：模块不依赖 Hub 可启动；契约正反例、空库 migration 和开发白名单测试通过。

### M1：模块业务 MVP

- 只实现该模块第一条真实纵向链路；
- 使用真实本地 PostgreSQL、文件或数据；
- 完成最小可操作 GUI；
- 失败状态、重试和回收站可见。

验收：用户可独立完成核心任务，不遇到中央权限缺失。

### M2：可靠性

- 幂等、检查点、Worker 恢复、资源版本、软删除/恢复；
- 大数据批处理和预算；
- 错误注入与真实重启测试。

验收：杀死 Worker 后可恢复；重复请求无重复副作用；数据可回滚或恢复。

### M3：集成适配

- RemotePolicy、RemoteEventBus、统一 Identity、Artifact access adapter；
- descriptor 注册；
- 与至少一个模块完成契约 E2E；
- 开发档位和集成档位使用同一领域核心。

验收：切换配置即可组网，不修改业务代码。

## 16. 从当前单体工程迁移

不要求一次重写。采用绞杀式拆分：

1. 冻结根目录新增业务表；现有表继续运行。
2. 先抽取 Knowledge Library，因为文件上传、解析和 GUI 最需要独立调试。
3. 为现有 API 加兼容 Facade，内部调用新 Knowledge API。
4. 数据通过一次性导出/导入脚本迁移，记录旧 ID → 新 ID 映射。
5. 验证双读一致后，停止旧表写入；旧数据保留只读。
6. 再按 Graph、Audit、Quant、Agent、Plugin、AIOps 顺序抽取。
7. 最后将根控制平面缩减为 Integration Hub 和 GUI Shell。

每次只迁移一个模块；不得同时拆库、改契约、换消息总线和重写 GUI。

## 17. 测试矩阵

| 层级 | 模块必须通过 | 集成阶段再通过 |
| --- | --- | --- |
| Unit | 领域规则、状态机、路径安全 | — |
| Contract | API/事件/descriptor 正反例 | 跨版本兼容 |
| Migration | 独立空库 upgrade、重复执行 | 多模块同时启动 |
| Integration | 本模块真实 PG/文件/Worker | Remote Policy/Event/Identity |
| Security | 模块目录边界、硬黑名单 | RLS、信息墙、跨模块授权 |
| E2E | 本模块 GUI 核心路径 | 跨模块 Saga |
| Failure | 本模块 Worker/解析器/DB 失败 | 总线中断、模块离线、重放 |

单模块不能用“中央服务未完成”解释测试失败；集成测试不能用 mock 模块冒充真实 E2E。

## 18. 状态文档格式

每模块 `docs/status.md` 必须记录：

1. 当前 M0–M3 状态；
2. 独立启动命令和端口；
3. 数据库名、migration head；
4. 当前 LocalPolicy 白名单和硬黑名单；
5. 已完成真实链路；
6. unit/contract/integration/e2e 实际结果；
7. 未配置本地运行时，如 MinerU/FFmpeg/embedding；
8. Integration Adapter 是否实现、是否实测；
9. 已知数据迁移和兼容风险。

## 19. 首批执行任务：Knowledge Library

```text
只执行 Knowledge Library 的 M0 和 M1，不修改 Graph/Audit/Quant/AIOps 私有实现。

建立 modules/knowledge_library：独立数据库 audit_kb_dev、独立 migration、health、
descriptor、LocalIdentity、LocalAllowlistPolicy、LocalEventBus 和本地 ArtifactStore。

完成文件/文件夹上传、虚拟文件夹树、MD/TXT/CSV/DOCX/XLSX 解析；PDF/图片通过
MinerU LocalExtractor 接口，音视频通过 Media LocalExtractor 接口。未安装运行时时
状态为 waiting_runtime，文件仍可查看和重试。完成列表、重命名、移动、版本、回收站、
恢复、处理进度和最小 GUI。

开发态默认允许 knowledge.upload/folder.manage/item.edit/item.recycle/extract.local，
保留路径逃逸、工作区外删除、外部泄露和任意命令硬拒绝。不得要求中央 Policy 数据库
存在。先写契约和测试，再实现逻辑；用真实 PostgreSQL 和本地文件完成 E2E。
```

Knowledge M1 验收通过后，可以独立开始 Graph M0/M1；不需要等待统一中枢完成。
