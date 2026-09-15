# 内置插件（Phase 0 / 插件组网规划 v1）

本目录按《统一插件协议》组织两层内容：

1. **契约协议包**：`audit-ledger-quality/plugin.protocol.json` 等仅声明身份、能力、输入输出、治理、资源与可观测性；不包含入口点或命令。
2. **已验证内置运行时**（`builtin/` 各子目录）：`plugin.protocol.json` + `plugin.manifest.json` + `plugin.runtime-binding.json`（SHA256 绑定）+ `runtime.py`（隔离子进程实现）。

运行时通过 `packages/plugin_runtime/runner.py` 的固定白名单加载：只接受硬编码的协议/清单/绑定三件套，校验 SHA256 后在一个受限子进程中只读执行；所有调用先经 Policy Gateway 持久裁决。

## 已验证内置插件

| 插件 ID                                 | 版本    | 能力                              | 副作用        | 状态                                |
| ------------------------------------- | ----- | ------------------------------- | ---------- | --------------------------------- |
| `knowledge.document-ingestion`        | 0.1.0 | `knowledge.extract.document`    | read\_only | Phase 1 验收通过                      |
| `audit.ledger-quality`                | 0.1.0 | `audit.ledger.validate`         | read\_only | 插件组网规划 R1 验收通过（2026-09-05）        |
| `audit.journal-anomaly`               | 0.1.0 | `audit.journal.detect`          | read\_only | 插件组网规划 R1 验收通过（2026-09-05）        |
| `knowledge.entity-relation-candidate` | 0.1.0 | `knowledge.extract.relations`   | read\_only | 插件组网规划 R1 验收通过（2026-09-05）        |
| `quant.snapshot-guard`                | 0.1.0 | `quant.dataset.validate`        | read\_only | 插件组网规划 R1 验收通过（2026-09-05）        |
| `quant.factor-compute`                | 0.1.0 | `quant.factor.compute`          | read\_only | 插件组网规划 R1 验收通过（2026-09-05）        |
| `aiops.alert-correlation`             | 0.1.0 | `aiops.alert.correlate`         | read\_only | 插件组网规划 R1 验收通过（2026-09-05）        |
| `aiops.alert-triage`                  | 0.1.0 | `aiops.alert.triage`            | read\_only | 插件组网规划 R0 提升 verified（2026-09-06） |
| `aiops.rca-ranker`                    | 0.1.0 | `aiops.rca.rank`                | read\_only | 插件组网规划 R1 验收通过（2026-09-05）        |
| `knowledge.graph-proposal-builder`    | 0.1.0 | `graph.proposal.build`          | read\_only | 插件组网规划 R2 验收通过（2026-09-05）        |
| `audit.investigation-plan`            | 0.1.0 | `audit.investigation.plan`      | read\_only | 插件组网规划 R2 验收通过（2026-09-05）        |
| `audit.finding-draft`                 | 0.1.0 | `audit.finding.draft`           | read\_only | 插件组网规划 R2 验收通过（2026-09-05）        |
| `audit.evidence-lineage`              | 0.1.0 | `audit.evidence.lineage`        | read\_only | 插件组网规划 R0 提升 verified（2026-09-06） |
| `quant.experiment-evaluator`          | 0.1.0 | `quant.experiment.evaluate`     | read\_only | 插件组网规划 R2 验收通过（2026-09-05）        |
| `quant.simulated-backtest`            | 0.1.0 | `quant.backtest.simulate`       | read\_only | 插件组网规划 R0 提升 verified（2026-09-06） |
| `aiops.playbook-proposer`             | 0.1.0 | `aiops.remediation.propose`     | read\_only | 插件组网规划 R2 验收通过（2026-09-05）        |
| `aiops.recovery-verifier`             | 0.1.0 | `aiops.recovery.verify`         | read\_only | 插件组网规划 R2 验收通过（2026-09-05）        |
| `audit.workpaper-export`              | 0.1.0 | `audit.workpaper.export`        | read\_only | 插件组网规划 R3 验收通过（2026-09-06）        |
| `audit.report-draft`                  | 0.1.0 | `audit.report.draft`            | read\_only | 插件组网规划 R3 验收通过（2026-09-06）        |
| `aiops.ticket-draft`                  | 0.1.0 | `aiops.ticket.draft`            | read\_only | 插件组网规划 R3 验收通过（2026-09-06）        |
| `knowledge.retention-recommendation`  | 0.1.0 | `knowledge.retention.recommend` | read\_only | 插件组网规划 R3 验收通过（2026-09-06）        |
| `aiops.postmortem-draft`              | 0.1.0 | `aiops.postmortem.draft`        | read\_only | 插件组网规划 R3 验收通过（2026-09-06）        |
| `quant.research-note-draft`           | 0.1.0 | `quant.research-note.draft`     | read\_only | 插件组网规划 R3 验收通过（2026-09-06）        |

> `knowledge.graph-proposal-builder` 是 R2 第一个提案型插件：隔离子进程只读读取候选集与（可选）活图快照，产出带来源定位/幂等键/重复与冲突标注的 ChangeSet 草稿（`graph-proposal-draft@1`）；草稿本身不写活跃图谱，落库由治理服务在策略裁决后以独立身份完成。
>
> `audit.investigation-plan` / `audit.finding-draft` / `quant.experiment-evaluator` 输出调查计划、发现草稿与实验评估建议；`aiops.playbook-proposer` / `aiops.recovery-verifier` 输出修复提案（canary/回滚建议）与基线/SLO 核验结果。全部 R2 插件只提交影子对象（草稿/候选/提案），确认、发布与执行由不同身份完成。
>
> R0 三个 `contract_only` 协议包（`aiops.alert-triage`、`audit.evidence-lineage`、`quant.simulated-backtest`）已于 2026-09-06 逐项补齐隔离运行时、`plugin.runtime-binding.json`、精确只读白名单策略与单元/集成测试并全部提升为 `verified`；三者输入/输出契约 Schema、正反向样本与独立 Manifest 保持不变，执行仍是受限子进程只读、输出需策略裁决后才可落库。
>
> `audit.workpaper-export` 是 R3① 第一个受审批输出插件：只读读取已确认的 `finding-set` 工件，按 `finding_set_sha256 + finding_ids + max_findings` 确定性生成可追溯底稿草稿（`workpaper-export@1`，status=draft、constraints 声明 no\_overwrite / requires\_human\_approval / immutable\_source）；永不覆盖历史报告，落库由治理服务在人工审批后以独立身份完成。
>
> `audit.report-draft` 是 R3② 第二个受审批输出插件：只读读取同一 `finding-set` 工件，按 `finding_set_sha256 + finding_ids + template_version` 确定性生成未签发的报告草稿（`report-draft@1`，status=draft、body 含 cover/executive\_summary/findings、signature.signed=false、constraints 额外声明 human\_issuance\_required）；模板版本是报告身份的一部分，签发仍由人完成，永不覆盖历史报告。
>
> `aiops.ticket-draft` 是 R3③ 第三个受审批输出插件：只读读取已确认 `incident-proposal@1` 事件，按 `incident_proposal_sha256 + proposal_id + target_system` 确定性生成外部工单草稿（`ticket-draft@1`，status=draft、severity 映射 P1–P4、signature.sent=false、constraints 声明 no\_auto\_send / requires\_target\_whitelist / requires\_human\_approval / immutable\_source）；草稿永不自动发送，实际发送必须满足目标系统白名单与人工审批，插件本身无网络、不写基础设施。
>
> `knowledge.retention-recommendation` 是 R3④ 第四个受审批输出插件：只读读取不可变 `document-content@1` 工件引用与保留元数据（最后访问天数、引用数、密级），按规则确定性地提出 retain/archive/delete\_candidate 建议（`retention-recommendation@1`，status=proposed、signature.reviewed=false、constraints 声明 no\_delete / evidence\_immutable / requires\_human\_approval）；建议绝不删除证据，实际归档或删除由独立治理身份在人工审批后执行，插件本身无写权限、无网络。
>
> `aiops.postmortem-draft` 是 R3⑤ 第五个受审批输出插件：只读读取已确认 `incident-proposal@1` 事件与可选 `recovery-verification@1` 恢复核验账本，按 `incident_sha256 + proposal_id + verification_sha256` 确定性生成复盘草稿（`postmortem-draft@1`，status=draft、timeline 含事件归因/恢复核验、action\_items 按严重度映射优先级、signature.published=false、constraints 声明 no\_auto\_publish / requires\_human\_approval / immutable\_source）；草稿永不自动发布，发布由人完成，插件本身无网络、不写基础设施。
>
> `quant.research-note-draft` 是 R3⑥ 第六个受审批输出插件：只读读取已确认 `experiment-evaluation@1` 实验评估草稿，按 `evaluation_sha256 + strategy_key + strategy_version` 确定性生成量化研究结论草稿（`research-note-draft@1`，status=draft、findings 汇总稳健性/漂移、recommendations 按晋级建议映射、signature.published=false、constraints 声明 no\_auto\_publish / no\_order\_generation / requires\_human\_approval / immutable\_source）；草稿永不自动发布、不含订单生成，发布由人完成，插件本身无网络、不写基础设施。
>
> **Plugin Topology M1（2026-09-07 验收）**：独立 `topology` schema 与 18 张版本化/回收站表承载 集群/蓝图/成员/边/契约 登记与冻结发布；`packages/plugin_topology/` 提供 契约校验 → 登记/发布/回收/恢复（全部写路径经 Policy 网关 + 租户 + Trace + 幂等键）→ 四轴集群收敛 → 契约兼容 → DAG 无环 → 预算截断 的纯 `plan_only` 路由计划，落 `routing_plans/nodes/edges` 且 `mode` 恒 `plan_only`；`main.py` 只读端点 `GET /api/v1/topology/clusters|blueprints|releases|plans` 与 Electron IPC 白名单同步声明。M1 不做 Manifest 安装、Agent 任务与任何执行按钮，蓝图契约禁止 runtime/entrypoint/package/permissions 执行字段；实施计划见 `docs/plugin-topology-M1.md`。

> **Plugin Topology M2（2026-09-07 验收）**：跨域桥接服务闭环（迁移 0038 + `service.py` bridge 登记/`bridge` 类型边一致性校验，仅 4 种公共 Ref + 前缀一致性；`GET /api/v1/topology/bridges` 只读端点并纳入 IPC 白名单）；桌面插件工作台「拓扑目录（真实只读 API）」并行对照真实 集群/蓝图/发布/计划/桥接 状态，显式「规划，不执行」横幅。全部读写仍经 Policy 网关、带租户/Trace/幂等键；实施计划见 `docs/plugin-topology-M2.md`。

> **Plugin Topology M3（2026-09-07 验收）**：精准关系链调用投影——`ChainResolver` 从已持久化 plan_only 路由计划以确定性无环拓扑序物化 `InvocationChain`（端口只接线契约与公共桥接 Ref、`payload_disallowed=true`、SHA256 可复现）；`IntentGenerator` 产出逐节点 `InvocationIntent`（isolation=isolated_subprocess、side_effects=read_only、Policy 裁决 allowed/requires_approval/denied）；`adapters.py` 提供 Plugin Runtime/Policy/Agent 三个只读适配器投影（`execution_invoked=False`）。迁移 0039 落 `invocation_chains/chain_nodes/intents` 三表（RLS + FORCE，含种子链），`materialize_chain/list_chains/list_intents` 服务方法与 `POST /api/v1/topology/chains/materialize`、`GET /api/v1/topology/chains[/{chain_key}/intents]` 端点（全部经 Policy 网关 + 幂等键），桌面工作台新增「调用链/调用意向」只读标签页。链恒 plan_only，绝不触达运行时；实施计划见 `docs/plugin-topology-M3.md`。

> **Plugin Topology M4（2026-09-07 验收）**：受治理的精准调用与执行验证账本——`ApprovalService` 实现 `requires_approval` 意图的人工/驳回状态机（approve → approved_projection、reject → denied、终局不可翻转，审批先写账本再改意图）；`ChainExecutor` 实现整链门控（任一 denied/未批准节点 → fail-closed 拒绝）→ 沿链拓扑序影子模拟（`mode` 恒 `simulated`，纯 DB 内状态投影、不启动子进程）→ 逐节点确定性 SHA256 输出 Ref/校验和写入不可变执行账本；隔离执行分支仅作契约/门控预留。迁移 0040 落 `invocation_approvals/execution_ledger` 两表（RLS + FORCE、仅 INSERT+SELECT、不可 UPDATE/DELETE），策略追加 `topology.chain.approve/execute` 与 `topology.execution.read`；API 新增 `POST .../approvals|executions` 与只读 GET（全部经 Policy 网关 + 幂等键），桌面工作台新增「审批账本/执行账本」只读标签页与经网关的「批准/驳回/影子模拟」按钮。链恒 simulated，绝不触达运行时；实施计划见 `docs/plugin-topology-M4.md`。

> **Plugin Topology M5（2026-09-07 验收）**：隔离执行层——受限只读子进程调用——把 M4 预留的 ISO 分支落地为真实执行：`IsolatedChainExecutor`（`packages/plugin_topology/isolated.py`）先跑 ISO 门（fail-closed：capability 命中 `_BUILTIN_LAYOUT` 白名单、`side_effects=read_only`、`isolated_subprocess`、Policy `topology.chain.execute` + `topology.chain.execute.isolated` 双放行，任一不满足零子进程）→ 输入物化（`db_artifacts` 或上游 artifact，SHA256 锁定）→ 逐节点 `python -I` 真实子进程（仅已验证内置只读插件、每节点独立进程、无网络）→ 输出物化为受控 staging artifact（SHA256 = 账本 output_checksum）；`build_research_note_payload_from_ledger` 桥接物化器将 `ledger-quality → research-note` 演练路径的输入适配与引用锁值保持一致。迁移 0041 扩 `execution_ledger` 的 `mode` 枚举（simulated|isolated）+ 5 个 DEFAULT 新列（plugin/version/runtime 校验和/输入 SHA256/artifact refs），策略追加 `topology.chain.execute.isolated` 且默认 `inactive`；API `POST .../executions` 支持 `mode='isolated'`（缺 `.isolated` 权限 → 403 零子进程）；桌面执行账本区分模拟/受限标签并新增「受限演练」按钮（二次确认）。执行仍只限测试库演练，不触达生产基础设施；实施计划见 `docs/plugin-topology-M5.md`。

> **Plugin Topology M6（2026-09-07 验收）**：链级执行运行编排——把 M5 的"一次隔离执行"升级为任意发布锁定且门控放行的调用链均可发起受限只读隔离执行运行：独立 `run_id` + 状态机 `running → success|failed`（CAS 终局一次回写、并发/篡改影响行数异常即 fail-closed）+ 逐 `run_id` 账本分组血缘；同链已有 `running` 运行 → 409（SQL partial unique index 硬背压），幂等键重放返回既有运行；预检 fail-closed（`release_locked=true`、无 denied/待决审批、双策略 `topology.chain.execute` + `.isolated` 放行，否则零子进程）。迁移 0042 新增编排控制面 `topology.execution_runs`（GRANT SELECT/INSERT/UPDATE 无 DELETE、RLS + FORCE）+ `execution_ledger.run_id` 列，唯一约束改「run·slot·mode」（同链可多次独立运行，M5 单次 `already_executed` 语义退役）；0043 加 running 并发唯一索引。API 新增 `POST .../chains/{chain_key}/runs`（409/403 语义）与 `GET .../runs`、`GET /runs/{run_id}` 只读投影，Electron IPC 白名单含三条 runs 路径；桌面「执行运行」标签页+「发起受限演练运行」按钮（链选择/reason 必填/二次确认/运行中禁发+409 提示）。无队列/租约/定时/调度，仍只限测试库演练；实施计划见 `docs/plugin-topology-M6.md`。

> **Plugin Topology M7（2026-09-07 验收）**：运行验证·可复现性核对与只读回滚裁定——把 M6 的 run 升级为可被证据化核对的对象：`topology.run_verifications` 逐节点对照目标 run 与参照 run（显式指定或自动选同链最近一次 `success` run）的账本 `output_checksum`，纯 DB 比对、不派生子进程、不执行任何回滚；得出 `verified`（全部节点一致）或 `drifted`（存在偏差/缺参照）并附**节点计数守恒**（matched + mismatched + ref_missing = total，JSON Schema 条件约束 + 库 CHECK 双重背书）；`drifted` 时生成只读 `rollback_verdict` 建议投影（仅偏差→`re-run-locked-release`、仅缺参照→`re-verify`、混合→`escalate-human`，含受影响节点与基线参照/锁链/规划器元数据）。迁移 0044 落 `run_verifications` 表（RLS + FORCE、仅 SELECT+INSERT、无 UPDATE/DELETE）+ 策略 `topology.chain.verify` 默认 `inactive`（fail-closed）；API 新增 `POST /api/v1/topology/runs/{run_id}/verifications` 与两条只读 GET，全部经 Policy 网关 + 幂等键（重放返回既有证据）；桌面「执行运行」表新增「验证状态」列与「发起运行验证」按钮，展开行展示验证历史卡（守恒校验 + 幂等重放标签 + 只读回滚裁定卡，显式注明桌面无任何回滚/修复执行入口）。实施计划见 `docs/plugin-topology-M7.md`。

> **Plugin Topology M8（2026-09-07 验收）**：漂移处置·修复提案治理——把 M7 的只读回滚裁定收口为受治理的漂移处置闭环：`packages/plugin_topology/remediation.py` 提供 提案创建 → 审批/驳回决策 → 受治理重跑血缘 → 只读投影。`create_proposal` fail-closed（仅 `status='drifted'` 的验证可提案、能力门控 `topology.chain.remediate`、action 默认继承 `rollback_verdict.action`、幂等键重放返回既有提案）；`decide_proposal` 的 `approve`/`reject` 各落一条不可变决策账本行（`UNIQUE(tenant_id, proposal_id, decision)` 终局一次、重复 409），proposal 行仅 INSERT 一次（status 恒 `pending_approval`），终局状态由决策账本推导（approve→approved、reject→rejected、escalate-human 人工关闭→closed）；`remediate_run` 仅放行 `approved` 且 `re-run-locked-release` 提案，**完全复用 M6 `start_run` 全部门控**（双策略 + 预检 fail-closed + running 并发守卫 + 逐节点 `python -I` 只读隔离子进程 + CAS finalize）并发起一次针对锁定发布的重跑，另落 `remediation_run_links` proposal→run 血缘；重跑后可再次 M7 验证收敛 `verified`，形成「漂移 → 提案 → 重跑 → 复验」证据闭环。迁移 0045 落 `remediation_proposals/remediation_decisions/remediation_run_links` 三张不可变证据表（RLS + FORCE、仅 INSERT+SELECT、无 UPDATE/DELETE）+ 策略 `topology.chain.remediate` 默认 `inactive`（fail-closed：未放行即 403 零写入零子进程）；API 新增 2 写 3 读共五条 remediation 端点（全部经 Policy 网关 + 幂等键），Electron IPC 白名单同步声明；桌面「执行运行」展开验证卡新增「发起修复提案 / 批准 / 驳回 / 关闭 / 发起重跑」入口（reason 必填 + 二次确认 + 终局禁用）与「修复提案」只读标签页，模型层新增 `proposalStatusLabel` 等纯函数。不引入自动修复、定时处置与真实回滚执行；实施计划见 `docs/plugin-topology-M8.md`。

> **Plugin Topology M9（2026-09-07 验收）**：证据链完整性与只读审计导出——把 M1–M8 的九类证据账本（发布、路由计划、链、调用意向、审批、执行账本、运行、验证/裁定、修复提案/决策/重跑血缘）锚进**每租户一条追加式 SHA256 前驱哈希链**，补足 M0 四图分治中的「E 图（证据与数据血缘图）」。`packages/plugin_topology/evidence.py`：`canonical_row` 确定性序列化（列名排序 + UUID/datetime/Decimal/jsonb 类型归一化 → 同一行哈希稳定、列序无关）；`anchor_evidence` 纯 DB 投影（scope 锁定枚举 `full/topology/chain/execution/verification/remediation` → 按表收集租户行 → 行哈希排序保证跨重放确定性 → 读取链尾 `prev_hash`（种子 `sha256(chain_key)`）→ `group_id = sha256(tenant:key)[:32]` 批量幂等，单事务逐行 `ON CONFLICT ... DO NOTHING` 追加并链接前驱，零子进程；无 `id` 列的 `routing_plan_edges` 用确定性行哈希标签定位）；`verify_evidence_chain` 两趟只读校验（重算源表行哈希 + 按锚定算法回放前驱链），任一环被改写 → `verified=False` 并**精确定位首个失配**（seq + 来源表 + 主键）；`export_evidence` 只读导出锚点元数据 + 整体 `sha256` + 实时证明摘要 `proof_ref{tail_hash, total_anchors}`，仅 API 响应体、不生成任何文件。迁移 0046（+0047 移除 `seq` IDENTITY 改应用侧自管链内序号）落 `topology.evidence_chain_anchors`（RLS + FORCE、仅 INSERT+SELECT、无 UPDATE/DELETE、`UNIQUE(tenant_id, chain_key, seq)` 链内序号唯一 + `UNIQUE(tenant_id, group_id, source_table, source_pk)` 批量幂等）+ 三个 fail-closed 策略 `topology.evidence.anchor/verify/export` 默认 `inactive`（未放行即 403 零写入零子进程）；API 新增 1 写 3 读共四条 evidence 端点（全部经 Policy 网关 + 幂等键），Electron IPC 白名单同步声明；桌面「拓扑目录」新增「证据链」只读标签页（链状态 / scope 选择 + 锚定/校验/只读导出 / 完整一致性证明卡含首个失配定位 / 导出摘要卡 / 锚点条目表），模型层新增 `evidenceScopeLabel` 等纯函数。全程纯 DB、只修改自身锚点表、零新增执行面，不触达主机/网络/基础设施；实施计划见 `docs/plugin-topology-M9.md`。

## 开发顺序

按 `docs/审计组网插件规划-v1.md` 的 R1→R4 路线逐个实现：每个插件先补契约 Schema 与正反向样本 → 契约测试 → 隔离子进程运行时 → 单测/黄金样本 → manifest + runtime-binding → 泛化 runner → 注册与精确策略 → API/脚本适配 → E2E + 全量回归 → 更新状态文档。

## 只读边界

插件在子进程中只能读取 `AUDIT_PLUGIN_READ_ROOTS` 声明目录内的本地文件，校验大小与 SHA256；无写入、无网络、无任意命令。`scripts/register-phase1-plugin.ps1` 仅登记 manifest 并（在显式开关下）发布精确能力白名单。
