# Plugin Topology M4 实施计划：受治理的精准调用与执行验证账本

> 状态：**已实施并验收**（契约先行已执行；全量回归通过，验收对照见第 7 节）。
> 关联：`docs/plugin-topology-orchestration.md`、`docs/plugin-topology-M1.md`、`docs/plugin-topology-M2.md`、`docs/plugin-topology-M3.md`、`AGENTS.md`。
> M4 目标：在 M3 的 `plan_only` 调用链与逐节点 `InvocationIntent` 之上，打通**精准关系链调用**——人工/规则批准 `requires_approval` 节点 → 整链门控 → 沿链拓扑序执行（默认**影子模拟**，纯 DB 内状态投影；可选**受限隔离执行**复用已验证内置插件的只读子进程）→ 逐节点输出引用与校验和写入不可变执行账本。**不做**真实交易、外部网络、基础设施、任意命令或泛化 AUTO；`requires_approval` 永不自动放行。

## 1. 范围

| 做                                                                                              | 不做                                         |
| ---------------------------------------------------------------------------------------------- | ------------------------------------------ |
| 逐节点批准工作流：`requires_approval` intent → 人工 approve/reject，审批留痕，状态 `approved_projection`/`denied` | 绕过审批的 AUTO 放行、代理审批                         |
| 整链执行门控：任一节点未放行（denied/未审批）→ 整链执行拒绝（fail-closed）                                                | 部分执行、跳过未批准节点                               |
| 影子模拟执行（`mode=simulated`）：沿链序确定性生成影子输出 Ref + SHA256，写入执行账本                                      | 模拟结果冒充真实运行时证据                              |
| 受限隔离执行的接口论证：executor 仅预留 isolated 分支的门控/端口契约描述、不实现子进程调用                                        | 启动任何子进程、调用 `runner.py`、`db_artifacts` 物化输入 |
| 执行账本（`execution_ledger`）与审批账本（`invocation_approvals`）：RLS + FORCE，不可覆盖、不可硬删                    | `agent` 创建真实任务、Temporal/生产调度器              |
| 只读 GUI：调用链执行面板（批准入口、执行状态、账本只读表）                                                                | 桌面端直接触发子进程或绕过网关写账本                         |

说明：M4 只交付**影子模拟执行**（纯 DB 内状态投影，不启动任何子进程）。隔离执行层（复用已验证内置插件只读运行时 + `db_artifacts` 物化）仅在服务层预留契约/门控描述，不实现调用路径，留待用户显式验收后启用。

## 2. 契约先行（先于服务逻辑）

新增 3 份 Schema（`contracts/jsonschema/`，沿用 M0–M3 内联样例测试风格）：

| Schema                                | 内容                                                                                                                                                       | 关键约束                                                     |
| ------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------- |
| `topology-approval.schema.json`       | 审批请求：`chain_key`、`slot_key`、`decision`（approve/reject）、`reason≤500`、幂等键与 trace                                                                           | `decision` 枚举；`reject` 时 reason 必填；必须携带幂等键               |
| `chain-execution-request.schema.json` | 整链执行请求：`chain_key`、`mode`（恒 `simulated`）、幂等键、reason                                                                                                      | `mode` 枚举仅 `simulated`（携带 `isolated` 即拒绝）；必须携带幂等键        |
| `execution-ledger.schema.json`        | 逐节点执行记录：`execution_id`、`chain_key`、`slot_key`、`mode`、`input_refs[]`、`output_refs[]`、`output_contract`、`output_checksum`、`status`、`policy_ref`、`trace_id` | `mode` 枚举；`status` 枚举；输出只允许 Refs + checksum，禁止正文 payload |

契约测试：3 份正例通过；approval 缺幂等键/`decision` 越界/`reject` 空 reason、执行请求 `mode` 越界/缺幂等键、账本携带 payload 正文一律拒绝。

## 3. 表结构（迁移 0040）

`migrations/versions/0040_execution_verification.py`，`down_revision = "0039_invocation_chain"`（revision 21 字符 < 32 上限）。

新增 2 张表（每表 RLS ENABLE + FORCE + 租户策略 + `GRANT SELECT, INSERT TO audit_app`，全部 `IF NOT EXISTS`；账本不可 UPDATE/DELETE，证据不可硬删）：

1. `topology.invocation_approvals` —— `id, tenant_id, chain_id(→invocation_chains), intent_id(→invocation_intents), slot_key, decision CHECK('approve'|'reject'), approver, reason, idempotency_key, trace_id, created_at`；`UNIQUE(tenant_id, chain_id, slot_key, decision)`（同一槽位批准/驳回各留一条终局记录，复投按幂等键重放）
2. `topology.execution_ledger` —— `id, tenant_id, chain_id, slot_key, ordinal, mode CHECK('simulated'), input_refs jsonb, output_refs jsonb, output_contract, output_checksum, status CHECK('pending'|'succeeded'|'failed'), policy_ref, idempotency_key, trace_id, started_at, finished_at`；`UNIQUE(tenant_id, chain_id, slot_key, ordinal, mode)`（同链同模式下不可重跑，重放按幂等键返回既有行）

种子（`ON CONFLICT DO NOTHING` 防重跑）：给 M3 种子链补一条 `execution_ledger` 成功投影（`mode=simulated`，输出 Refs + 规范化 SHA256 与服务公式一致）与一条 `invocation_approvals` 示例审批（`research-note-slot` 节点 approve 留痕）；`policy.policy_sets` upsert 追加 `topology.chain.approve`（low/write\_data）、`topology.chain.execute`（medium/write\_data）、`topology.execution.read`（read\_only）与既有 `topology.intent.read` 联动。

## 4. 服务层模块

```
packages/plugin_topology/
├── approvals.py     # ApprovalService：审批账本 + intent 状态机（requires_approval → approved_projection | denied）
└── executor.py      # ChainExecutor：整链门控 → 沿拓扑序模拟/隔离执行 → 执行账本（默认 simulated）
```

- **ApprovalService**：`approve()`/`reject()`（幂等键重放 201 语义；`UNIQUE` 终局约束下改状态原子化：仅允许 `requires_approval` 待决节点，approve → `approved_projection` 并写 approval\_ref，reject → `denied` 并写否决 reason；已终局/未知槽位/决策枚举越界一律拒绝）。`list_approvals(chain_key)` 只读。所有写路径经 Policy 网关 + 租户 + Trace + 幂等键。

- **ChainExecutor**：

  1. `GATE`：载入链全部 intents；存在 `denied` 或遗留 `requires_approval`（未成 `approved_projection`）→ 整链拒绝（fail-closed，留 trace）。
  2. `SIM`：沿 `chain_order` 拓扑序，对每个节点按端口接线契约确定性生成影子输出 —— `output_checksum = sha256(canonical(输出 Ref + 输入 Refs + 槽位 + 版本))`，出入 Refs 仅契约/公共桥接 Ref；预算/大小封顶，超限如实 `truncated`。
  3. `ISO`（仅契约/门控预留，不实现调用路径）：描述 isolated 分支的放行条件（slot 命中已验证内置插件、逐节点只读策略白名单、`side_effects=read_only`）与失败语义（`failed` + trace、不自动降级），本里程碑不启动任何子进程。
  4. 每节点写 `execution_ledger` 行；`list_executions(chain_key)` 只读。

- **design notes**：链 checksum / 锁版本 / planner 版本在每次执行时快照进账本（可复现）；绝不把 M4 实现挂到生产调度器或 `agent` 任务。

## 5. API 与桌面只读 GUI

- API（`apps/api/main.py`，全部经 Policy 网关 + 租户 + Trace + 幂等键）：

  - `POST /api/v1/topology/chains/{chain_key}/approvals`（`topology.chain.approve`/low）→ 审批/驳回意图节点，幂等

  - `POST /api/v1/topology/chains/{chain_key}/executions`（`topology.chain.execute`/medium）→ 整链影子执行（`mode` 恒 simulated），幂等

  - `GET /api/v1/topology/chains/{chain_key}/approvals`（`topology.chain.read` 或 `topology.execution.read`/read\_only）

  - `GET /api/v1/topology/chains/{chain_key}/executions`（`topology.execution.read`/read\_only）

  - Electron IPC 白名单追加两个只读端点；写端点（approvals/executions）按 Phase 7 确认发现模式进白名单（POST 自动携带幂等键），桌面只给「批准/驳回/模拟执行」按钮，不暴露任何子进程执行入口。

- GUI（`desktop/src/App.tsx`，复用 M3 拓扑卡片模式）：新增「调用链执行（只读）」卡片 —— 意图表每行在 `requires_approval` 时显示「批准/驳回」按钮（二次确认）；执行卡片显示最近一次执行账本（slot/模式/输出 Ref/校验和/状态标签）与审批账本（决策/审批人/时间），均只读展示；横幅沿用「规划，不执行 · 仅状态投影」并在写按钮旁注明「经 Policy 网关、只写投影与账本」。

## 6. 测试计划

| 层   | 用例                                                                                                                          |
| --- | --------------------------------------------------------------------------------------------------------------------------- |
| 契约  | 3 份新 Schema 正反例（缺幂等键/decision 越界/reject 空 reason/mode 越界/账本带 payload 拒绝）                                                    |
| 单元  | 审批状态机（requires\_approval→approved\_projection/denied，终局不可翻转）、整链门控拒绝（有 denied/未审批节点）、模拟执行确定性（同链同输入→同输出 checksum）、预算截断、账本不可重跑 |
| 集成  | seed 账本/审批重建校验；approve API 幂等重放；execute API：门控 fail-closed 403、无权限 403、缺幂等键 409、`mode=isolated` 请求契约拒绝；RLS 跨租户不可见           |
| E2E | 批准 requires\_approval 节点 → 执行（simulated）→ 账本落库（输出 Ref+checksum）→ GUI 只读展示 + 批准按钮                                            |
| GUI | Vitest（新组件渲染真实 API mock）+ 桌面 typecheck                                                                                      |

回归：`python -m pytest -q`、`python -m ruff check .`、`python -m mypy packages`、`npm run typecheck`。

## 7. 验收对照（M4 六条）

1. `requires_approval` 节点必须经幂等的人工审批后才能进入执行；`reject` 立即置 `denied`，任何 AUTO 放行被契约/Policy 双重拒绝。
2. 整链执行 fail-closed：任一节点未放行/被拒 → 整链拒绝并留痕，绝无跳过或部分执行。
3. `mode` 恒 `simulated`，为纯 DB 内状态投影（不启动子进程），输出只写 Refs + SHA256；模拟结果如实标注，不冒充真实运行时证据。
4. 隔离执行层仅作契约/门控预留（服务层描述放行条件与失败语义），本里程碑不实现子进程调用、不触达 `runner.py`/`db_artifacts`；执行请求携带 `isolated` 即被契约拒绝。
5. 执行账本与审批账本 RLS + FORCE、不可 UPDATE/DELETE、不可覆盖不可硬删；同链同模式下不可重跑（重放按幂等键返回既有行）。
6. GUI 只读展示执行/审批账本，写操作仅经 Policy 网关的批准/模拟执行按钮，桌面不暴露任何子进程执行入口。

完成后：全量回归、更新 `docs/status.md` 与 `plugins/README.md`；隔离执行层按用户验收留到后续里程碑。

## 8. 与后续里程碑的衔接

- 本阶段**不创建** `agent` 任务、不引入 Temporal/生产调度（留给 M5+）。

- 隔离执行层（已验证内置插件只读运行时 + `db_artifacts` 物化输入）在 M4 仅作契约/门控预留，待用户显式验收后接入 `runner.py.invoke`，并用现有种子链的 `ledger-quality-slot → research-note-slot` 桥接路径做受限只读演练。

