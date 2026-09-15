# Plugin Topology M8 实施计划：漂移处置·修复提案治理——精准关系链调用的证据闭环收口

> 状态：**已实施并验收**（2026-09-07）。
> 关联：`docs/plugin-topology-M7.md`、`docs/plugin-topology-M6.md`、`docs/plugin-topology-orchestration.md`、`AGENTS.md`。
> M8 目标：M7 让漂移运行产生**只读回滚裁定**（re-verify / re-run-locked-release / escalate-human），M8 把「裁定」收口为**受治理的漂移处置（drift disposition）**——对 drifted 验证可发起**修复提案（remediation proposal）**：记录建议动作、受影响节点、基线摘要，经 Policy 网关 + 幂等键 + 人工/规则批准后，可**复用 M6 既有运行机制**发起一次针对锁定发布的重跑（proposal → run 血缘），或驳回/升级人工。修复提案是纯 DB 证据投影：不新增执行面、不触达主机/网络/基础设施；所有写入仍只落在测试库演练。**不引入**自动修复、定时处置、真实回滚执行。

## 0. 合规边界（前置声明）

- 处置仅由**用户/桌面显式触发**（幂等键 + 单次 + fail-closed），无后台调度、无自动重试、无定时任务；不自动对 drifted 验证发起任何动作。

- 修复提案为**追加型证据**：创建/批准/驳回/关闭各落不可变账本行；状态迁移仅经服务层受控 CAS 路径；历史记录不可覆盖、不可硬删。

- 提案的「重跑」动作**不新增执行面**——完全复用 M6 `start_run` 既有链路（`topology.chain.execute` + `.isolated` 双策略门控 + 预检 fail-closed + 同链 running 并发守卫 + 逐节点 `python -I` 只读隔离子进程），仅额外落 proposal → run 血缘；`escalate-human` / `re-verify` 动作不触发任何执行。

- 演练全部落在独立测试库 `audit_network_test`；不触达生产数据库与真实基础设施；桌面不暴露任何回滚/修复执行按钮。

- 证据约束延续：`remediation_proposals` 与 `remediation_decisions` 均 RLS + FORCE + 仅 INSERT+SELECT（不可 UPDATE/DELETE、不可硬删）；写入经 Policy 网关 + 租户 + Trace + 幂等键。

## 1. 范围

| 做                                                                                                                              | 不做                                               |
| ------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------ |
| 漂移处置：仅对 `status=drifted` 的验证发起修复提案（建议动作/受影响节点/基线摘要），`status` 状态机 `draft → pending_approval → approved/rejected/closed`（终局不可翻转） | 自动/定时处置；对 `verified` 或无验证的运行发起提案（fail-closed）    |
| 提案审批：`approve`/`reject` 各落一条不可变决策账本行（复用 M4 审批账本模式），终局不可翻转；关闭（close）记录升级人工或撤销                                                   | 绕过审批直接重跑；覆盖/硬删既有决策                               |
| 受治理重跑：`approved` 提案可发起一次针对锁定发布的重跑运行（复用 M6 `start_run` 全部门控），落 proposal → run 血缘                                                | 新增执行面；对 rejected/closed 提案发起运行（fail-closed）；自动重跑 |
| 处置门控（fail-closed）：提案仅可引用同租户 drifted 验证；`topology.chain.remediate` 默认 inactive → 403 零写入零子进程                                    | 跨租户观测；引用非 drifted 验证或缺失基线                        |
| 证据投影：`get_remediation_proposal` / `list_remediation_proposals`（strict schema + proposal→verdict→run 血缘）                        | 查询越界 limit；携带 payload 正文                         |
| 只读 GUI：「执行运行」展开验证卡新增「发起修复提案」入口 + 提案处置状态/审批按钮（经 Policy 网关 + 幂等键）                                                                | 桌面暴露回滚/修复执行按钮、绕过 Policy 网关                       |

## 2. 契约先行（先于服务逻辑）

新增 2 份 Schema（`contracts/jsonschema/`，沿用内联样例测试风格），不动 M1–M7 已验收语义：

| Schema                                        | 内容                                                                                                                                                                                                                                                                                                             | 关键约束                                                                                                    |
| --------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------- |
| `remediation-proposal-request.schema.json`（新） | 发起一次漂移处置：`verification_id`（drifted 验证）、`action`（继承 M7 裁定动作枚举 `re-verify/re-run-locked-release/escalate-human`，可覆写但不越界）、`reason`、`idempotency_key`                                                                                                                                                              | `verification_id` 为 uuid（FormatChecker）；`action` 枚举闭合；`reason` 非空必填；幂等键必填；`additionalProperties: false` |
| `remediation-proposal.schema.json`（新）         | 修复提案只读投影：`proposal_id`、`verification_id`、`run_id`、`chain_key`、`status ∈ [draft, pending_approval, approved, rejected, closed]`、`action`、`affected_slots`（非空数组）、`baseline`（reference\_run\_id/chain\_checksum/planner\_version）、`remediation_run_id`（可 null，approved 后重跑运行时回填）、`reason`、`trace_id`、`created_at` | `status` 枚举闭合；`remediation_run_id` 非 null 时仅允许 `status=approved` 的已重跑提案；不携带账本正文/payload                 |

契约测试：2 组正例（提案请求、提案投影均通过）；`verification_id` 非 uuid、`action` 越界、空 reason、缺幂等键、投影 `status` 越界、`affected_slots` 为空/非字符串数组、`remediation_run_id` 在非 approved 状态下出现、携带 payload 正文一律拒绝。

## 3. 表结构（迁移 0045）

`migrations/versions/0045_remediation_proposals.py`，`down_revision = "0044_chain_run_verification"`（revision 25 字符 < 32 上限）。全部幂等；两张表均为**不可变证据面**（仅 INSERT+SELECT，无 UPDATE/DELETE）。

```sql
CREATE TABLE IF NOT EXISTS topology.remediation_proposals (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES iam.tenants(id),
  verification_id uuid NOT NULL REFERENCES topology.run_verifications(id),
  run_id uuid NOT NULL REFERENCES topology.execution_runs(id),
  chain_id uuid NOT NULL REFERENCES topology.invocation_chains(id),
  chain_key text NOT NULL,
  status text NOT NULL DEFAULT 'draft'
    CHECK (status IN ('draft', 'pending_approval', 'approved', 'rejected', 'closed')),
  action text NOT NULL CHECK (action IN ('re-verify', 're-run-locked-release', 'escalate-human')),
  affected_slots jsonb NOT NULL,
  baseline jsonb NOT NULL,
  remediation_run_id uuid REFERENCES topology.execution_runs(id),
  reason text NOT NULL,
  idempotency_key text NOT NULL,
  trace_id text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(tenant_id, idempotency_key),
  CHECK (jsonb_array_length(affected_slots) > 0),
  CHECK ((remediation_run_id IS NULL) OR (status = 'approved'))
);
CREATE INDEX IF NOT EXISTS topology_remediation_proposals_chain_idx
  ON topology.remediation_proposals (tenant_id, chain_id, created_at DESC);
CREATE INDEX IF NOT EXISTS topology_remediation_proposals_run_idx
  ON topology.remediation_proposals (tenant_id, run_id, created_at DESC);

CREATE TABLE IF NOT EXISTS topology.remediation_decisions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES iam.tenants(id),
  proposal_id uuid NOT NULL REFERENCES topology.remediation_proposals(id),
  decision text NOT NULL CHECK (decision IN ('approve', 'reject')),
  approver text NOT NULL,
  reason text NOT NULL DEFAULT '',
  idempotency_key text NOT NULL,
  trace_id text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(tenant_id, proposal_id, decision)
);
CREATE INDEX IF NOT EXISTS topology_remediation_decisions_proposal_idx
  ON topology.remediation_decisions (tenant_id, proposal_id, created_at DESC);
```

- 两张表均 RLS `ENABLE + FORCE` + 租户策略；`GRANT SELECT, INSERT ON topology.remediation_proposals TO audit_app` 与 `GRANT SELECT, INSERT ON topology.remediation_decisions TO audit_app`，**均无 UPDATE/DELETE**（状态迁移经服务层受控路径：`pending_approval`/`approved` 通过 CAS `UPDATE ... WHERE status='draft'` 或 `WHERE status='pending_approval'` 且走 `audit_migrator` 级权限？——否，服务层以应用角色执行时必须保持仅 INSERT+SELECT；故状态以**追加新行 + 最新行裁决**语义表达，proposal 行 `status` 视为「最近生效投影」由服务层 CAS 维护在**同一事务**内，见 §4）。

> **状态机写入策略（关键设计）**：`remediation_proposals.status` 是服务层维护的「最新生效投影」。为保持表级仅 INSERT+SELECT，状态推进不靠裸 UPDATE——而是在同一事务内：写入不可变证据（`remediation_decisions` 决策行）后，对 proposal 行执行 `UPDATE ... SET status='approved', updated_at=now() WHERE id=%s AND status='pending_approval'`。该 UPDATE 由 `audit_app` 持有（表无 UPDATE 授权时改为：proposal 行只写一次 `status='pending_approval'`，终局决策存决策账本，**读取投影时按决策账本推导终局状态**，proposal 表本身不再 UPDATE）。本里程碑采用后者：**proposal 行仅 INSERT 一次（status 恒** **`pending_approval`），终局状态由** **`remediation_decisions`** **账本推导**——完全满足仅 INSERT+SELECT，避免引入表级 UPDATE 授权。投影层 `status ∈ [pending_approval, approved, rejected, closed]` 由「是否已有 approve/reject 决策 + 是否已升级关闭」推导，`remediation_run_id` 由 approved 后重跑链接记录在 proposal 行的 `remediation_run_id`（经一次性 INSERT 的补充行？——否，重跑血缘落**另一张只读投影**或复用 decision 账本的关联，见 §4）。

> **简化终稿（本里程碑采用）**：`remediation_proposals` 表仅 INSERT 一次（status 恒 `pending_approval`），**不做任何 UPDATE**；新增**只读视图语义**由服务层提供：`approve`/`reject` 写入 `remediation_decisions`（append-only），`approved` 提案发起的重跑写入 `remediation_run_links`（`proposal_id, run_id` 两列 append-only，仅 INSERT+SELECT），投影时三表 JOIN 推导终局 `status` 与 `remediation_run_id`。这样全部证据面恒仅 INSERT+SELECT、状态由不可变账本推导，天然满足「不可覆盖/不可硬删」。

## 4. 服务层

```
packages/plugin_topology/
├── runs.py            # 不变（M6 运行生命周期保持原样）
├── verification.py    # 不变（M7 验证语义保持原样）
├── remediation.py     # 新增：RemediationService——提案创建 → 审批/驳回决策 → 受治理重跑血缘 → 只读投影
└── service.py         # 扩展：暴露 create_remediation_proposal / decide_remediation / remediate_run / list_remediation_proposals / get_remediation_proposal（委托 remediation.py）
```

- **提案创建（fail-closed，先于任何写入）**：请求 schema 校验 + 能力门控 `topology.chain.remediate` → 验证存在且租户归属正确 → `verification.status == 'drifted'`（verified/不存在 → `RemediationError`，零写入）→ 提案 `action` 默认继承验证的 `rollback_verdict.action`（可显式覆写但枚举闭合）→ INSERT `remediation_proposals`（status 恒 `pending_approval`，幂等键冲突重放返回既有提案）。

- **审批/驳回决策**：`approve`（能力 `topology.chain.remediate` + 复用审批语义）→ 事务内 INSERT `remediation_decisions(decision='approve')`（`UNIQUE(proposal_id, decision)` 保证终局一次）；`reject` 同理；approve 后提案进入 `approved` 投影状态（终局，不可翻转）；驳回进入 `rejected`。决策人、reason、幂等键、trace 全量留痕。

- **受治理重跑（仅 approved）**：对 `approved` 提案发起 `remediate_run` → 复用 M6 `start_run` 全部链路（双策略门控 `topology.chain.execute` + `.isolated` → 预检 fail-closed → begin\_run 并发守卫 → 逐节点隔离执行 → CAS finalize），新增 `remediation_run_links`（proposal\_id → run\_id，仅 INSERT）血缘；`re-verify` 与 `escalate-human` 提案**不触发**任何执行（`remediate_run` 对非 `re-run-locked-release` 提案 fail-closed 拒绝）。新 run 完成后可再次发起 M7 验证，形成「漂移 → 提案 → 重跑 → 复验」闭环。

- **只读投影**：`get_remediation_proposal` / `list_remediation_proposals`——三表 JOIN（proposal + 决策账本 + 重跑血缘）推导 `status`（无决策 → `pending_approval`；有 approve → `approved`；有 reject → `rejected`；`escalate-human` 提案人工关闭 → `closed`）与 `remediation_run_id`；strict schema 校验后返回。

## 5. API 与桌面只读 GUI

- API（`apps/api/main.py`，全部经 Policy 网关 + 租户 + Trace + 幂等键）：

  - `POST /api/v1/topology/verifications/{verification_id}/remediation`（`topology.chain.remediate`/medium）→ 创建修复提案；验证非 drifted → 409/403 fail-closed。

  - `POST /api/v1/topology/remediation/{proposal_id}/decisions`（`topology.chain.remediate`/medium）→ 批准/驳回（`decision ∈ [approve, reject]` + reason）；终局不可翻转 → 409。

  - `POST /api/v1/topology/remediation/{proposal_id}/runs`（`topology.chain.remediate` + `topology.chain.execute` + `.isolated` 三重门控）→ 仅 `re-run-locked-release` 且 approved 提案可发起重跑运行；返回 run（复用 M6 运行投影 + proposal 血缘）。

  - `GET /api/v1/topology/remediation`（`topology.execution.read`/read\_only）与 `GET /api/v1/topology/remediation/{proposal_id}`（read\_only）→ 提案列表/详情（含状态推导、决策账本、重跑血缘）。

  - Electron IPC 白名单新增四条 remediation 路径（沿用精确路径风格，无通配符）。

- GUI（`desktop/src/App.tsx`，复用 M7「执行运行」tab 展开卡）：

  - drifted 验证卡新增「发起修复提案」按钮（reason 必填 + 二次确认弹窗注明纯证据投影、重跑仍走 M6 全部门控、未获 `topology.chain.remediate` 将 403）。

  - 提案处置区：`pending_approval` 提案显示「批准/驳回」按钮（经 Policy 网关 + 幂等键 + 二次确认）；`approved` 且动作 `re-run-locked-release` 显示「发起重跑运行」按钮（复用 M6 发起逻辑）；展示「验证 → 提案 → 决策 → 重跑运行」血缘链（proposal 卡内嵌 verification\_id/run\_id/决策账本/remediation\_run\_id 截断）。

  - 模型层新增 `proposalStatusLabel` / `proposalActionLabel` / `proposalCanRemediate` 纯函数；桌面仍不暴露回滚/修复执行或调度入口。

## 6. 测试计划

| 层   | 用例                                                                                                                                                                                                                                                                            |
| --- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 契约  | 2 份新 Schema 正反例：`verification_id` 非 uuid、`action` 越界、空 reason、缺幂等键、`status` 越界、`affected_slots` 空/非数组、`remediation_run_id` 非 approved 出现、payload 正文拒绝                                                                                                                         |
| 单元  | 提案创建预检（验证不存在/非 drifted/跨租户 → 拒绝零写入）；状态机（approve → approved 终局、reject → rejected 终局、重复决策 409）；幂等键重放返回既有提案/决策；`remediate_run` 仅 `approved` + `re-run-locked-release` 放行（其余 fail-closed 零运行）；fake runtime 断言提案审批与驳回**不启动任何子进程**                                                  |
| 集成  | 种子链：构造 drifted 验证（输入漂移重跑）→ 发起 `re-run-locked-release` 提案 → 批准 → 发起重跑运行（真实 `python -I` 两节点隔离执行、复用 M6 门控）→ 新 run 再验证收敛 `verified`；`escalate-human` 提案驳回 → 不产生任何运行；无 `topology.chain.remediate` 权限 → 403 零写入零子进程；幂等键重放同一 proposal\_id；RLS 跨租户不可见；两证据表无 UPDATE/DELETE 权限（psql 验证） |
| GUI | Vitest（提案状态/动作标签、按钮禁用态、血缘投影 mock）+ 桌面 typecheck                                                                                                                                                                                                                               |

回归：`python -m pytest -q`、`python -m ruff check .`、`python -m mypy packages`、`npm run typecheck`、`npm test`、`npm run build`。

## 7. 验收对照（M8 六条）

1. **处置语义**：仅 `status=drifted` 的验证可发起修复提案（纯 DB 证据投影、零新增执行面）；提案/决策/重跑血缘各落不可变账本行，可多次追加、不可覆盖/硬删。
2. **状态机正确性**：`pending_approval → approved/rejected` 终局不可翻转（决策账本 `UNIQUE(proposal_id, decision)` 保证一次）；`escalate-human` 关闭为 `closed`；`remediation_run_id` 仅 approved 且重跑后非空。
3. **受治理重跑**：仅 `approved` + `re-run-locked-release` 提案可复用 M6 `start_run` 发起运行（三重策略门控 + 预检 + 并发守卫全部沿用），proposal → run 血缘可追溯；重跑后再次 M7 验证可收敛 `verified`。
4. **预检门控**：验证非 drifted / 跨租户 / 已终局决策 → fail-closed 零写入零子进程；`topology.chain.remediate` 默认 inactive → 403 零写入零子进程。
5. **证据约束**：`remediation_proposals` / `remediation_decisions` / `remediation_run_links` 三表 RLS + FORCE + 仅 INSERT+SELECT；终局状态由不可变账本推导，无表级 UPDATE/DELETE；跨租户不可见。
6. **GUI 受限入口**：桌面经 Policy 网关提供「发起修复提案 / 批准 / 驳回 / 发起重跑」入口（reason 必填 + 二次确认 + 终局禁用），血缘链只读展示，无任何回滚/修复执行按钮。

完成后：全量回归、更新 `docs/status.md`、`plugins/README.md` 与本文档状态。

## 8. 与后续里程碑的衔接

- 本阶段**不创建** Agent 任务、不引入 Temporal/生产调度器/队列租约、不实现真实回滚执行（重跑复用 M6 既有隔离执行；真实修复/回滚执行面仍留待另行批准的阶段）。

- 处置面仅限已验证内置插件 + 已发布锁定链的 drifted 验证；第三方插件来源信任/签名、生产运行时完整调用面、规则驱动的自动处置留待后续，需单独流程。

- 默认 fail-closed：`topology.chain.remediate` 未显式激活时一切提案/决策/重跑请求 403 且零写入零子进程；M1–M7 已验收语义保持兼容。

## 9. 交付清单（已实施）

- **契约**（`contracts/jsonschema/`）：`remediation-proposal-request.schema.json`（新）+ `topology-remediation-proposal.schema.json`（新，独立命名以避免与 AIOps `aiops.playbook-proposer`/`aiops.recovery-verifier` 既有共享契约 `remediation-proposal.schema.json` 冲突；原 AIOps Schema 语义完整保留）；`contracts.py` 新增 `RemediationProposalRequest.parse` / `validate_proposal_request` / `validate_proposal`；契约正反例测试覆盖 uuid/action/reason/幂等键/status/affected\_slots/remediation\_run\_id/payload 全约束。

- **迁移 0045**（`migrations/versions/0045_remediation_proposals.py`，down\_revision = `0044_chain_run_verification`）：`topology.remediation_proposals`（仅 INSERT 一次，status 恒 `pending_approval`，`CHECK (remediation_run_id IS NULL OR status='approved')`，`UNIQUE(tenant_id, idempotency_key)`）、`topology.remediation_decisions`（`UNIQUE(tenant_id, proposal_id, decision)` 终局一次）、`topology.remediation_run_links`（proposal→run append-only 血缘）；三表均 RLS + FORCE + 仅 `GRANT SELECT, INSERT`（无 UPDATE/DELETE）；`policy.policy_sets` 追加 `topology.chain.remediate`（medium/write\_data），种子默认 `inactive`（fail-closed）；已显式应用 test/dev 库，幂等可重放。

- **服务层**（`packages/plugin_topology/remediation.py` 新增 + `service.py` 五个方法）：`create_proposal`（fail-closed 预检 → drifted 锚定 → action 继承/覆写 → 幂等重放）、`decide_proposal`（approve/reject 决策账本 + close 仅 escalate-human）、`remediate_run`（仅 approved + re-run-locked-release，复用 M6 `start_run` 全链路 + run 链接血缘）、`proposal_projection` / `list_proposal_rows`（三表 JOIN 推导终局状态与 remediation\_run\_id）。

- **API**（`apps/api/main.py`）：`POST /verifications/{verification_id}/remediation`、`POST /remediation/{proposal_id}/decisions`、`POST /remediation/{proposal_id}/runs`（三重门控）、`GET /remediation`、`GET /remediation/{proposal_id}`；Electron IPC 白名单五条路径（`desktop/electron/main.ts`）。

- **桌面只读 GUI**（`desktop/src/App.tsx` + `styles/globals.css` + 模型层 `pluginTopology.ts`）：验证卡「发起修复提案」入口（reason 必填 + 二次确认）、提案处置卡「批准/驳回/关闭/发起重跑」（终局禁用 + 幂等重放标签）、「修复提案」只读标签页、`proposalStatusLabel` / `proposalActionLabel` / `proposalCanDecide` / `proposalCanRerun` / `remediationRunLabel` 纯函数及 Vitest 单测。

## 10. 验收对照（已逐条满足）

| # | 验收项                                                                                                                    | 实现与验证                                                                                                                      |
| - | ---------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------- |
| 1 | 处置语义：仅 drifted 验证可发起提案，纯 DB 证据投影、零新增执行面；提案/决策/重跑血缘各落不可变账本行，可追加不可覆盖/硬删                                                  | `remediation_proposals/decisions/run_links` 三表仅 INSERT+SELECT；单元测试断言非 drifted 锚定零 INSERT、集成测试验证 `python -I` 子进程仍只经 M6 既有链路 |
| 2 | 状态机正确性：`pending_approval → approved/rejected` 终局不可翻转；escalate-human 关闭为 `closed`；`remediation_run_id` 仅 approved 重跑后非空 | 决策账本 `UNIQUE(proposal_id, decision)` 保证终局一次；投影由账本推导；Schema `allOf` 条件约束非 approved 恒 null；契约正反例 + 集成重复决策 409                |
| 3 | 受治理重跑：仅 approved + re-run-locked-release 复用 M6 `start_run`（三重门控 + 预检 + 并发守卫全沿用），proposal→run 血缘可追溯；重跑后可复验收敛 `verified` | 集成测试「漂移 → 提案 → 批准 → 真实双节点隔离重跑 → 再验证收敛 `verified`」闭环通过                                                                      |
| 4 | 预检门控：非 drifted/跨租户/已终局 → fail-closed 零写入零子进程；`topology.chain.remediate` 默认 inactive → 403 零写入零子进程                      | 单元（零写入断言）+ 集成（403/409 + RLS 跨租户不可见）覆盖                                                                                      |
| 5 | 证据约束：三表 RLS + FORCE + 仅 INSERT+SELECT，终局状态由账本推导，无表级 UPDATE/DELETE；跨租户不可见                                               | 迁移 GRANT 显式无 UPDATE/DELETE；psql 权限验证 + RLS 集成用例通过                                                                          |
| 6 | GUI 受限入口：经 Policy 网关提供「发起修复提案/批准/驳回/发起重跑」入口（reason 必填 + 二次确认 + 终局禁用），血缘链只读展示，无任何回滚/修复执行按钮                              | App.tsx 实现 + 模型层纯函数 Vitest 单测 + 桌面 typecheck/build 通过                                                                      |

## 11. 回归结果（2026-09-07）

- 全量 Python `pytest -q`：**685 passed, 2 skipped**（两个默认关闭的真实 MinerU 集成）。

- Ruff：`pyproject.toml` 显式 `[tool.ruff.lint] select = ["E4","E7","E9","F","I"]` 收敛规则集（ruff 0.16 起默认启用 SIM/UP/FLY/DTZ 等历史里程碑未采用的全库新告警），并自动修复 123 处 I001 import 排序后**全仓通过**。

- Mypy（packages 46 源）、桌面 TypeScript typecheck、Vitest（14/14）、生产构建（electron-vite）均通过。

- 修复项：M8 投影 Schema 与 AIOps 共享契约同名冲突——`topology-remediation-proposal.schema.json` 独立命名，原 `remediation-proposal.schema.json`（AIOps playbook-proposer / recovery-verifier 契约）语义完整保留，相关契约/运行时测试全部通过。

