# Plugin Topology M6 实施计划：链级运行编排——精准关系链调用的受限只读执行运行

> 状态：**已实施并验收**（2026-09-07；契约先行→迁移→服务→API→GUI→全量回归全部完成）。
> 关联：`docs/plugin-topology-orchestration.md`、`docs/plugin-topology-M4.md`、`docs/plugin-topology-M5.md`、`AGENTS.md`。
> M6 目标：把 M5 的"种子链一次隔离执行"升级为**链级执行运行（execution run）编排**——任意已发布锁定（`release_locked=true`）、意图全通过门控（无 denied、无待决 `requires_approval`）的调用链均可发起一次受限只读隔离执行运行（独立 `run_id` + 状态机 `running/success/failed` + 逐运行账本分组 + 桌面运行视图）。仍只限已验证内置插件、`python -I` 隔离子进程、测试库演练；**不引入**队列、租约、定时、Temporal 或 Agent 任务（用户已确认调度边界）。

## 0. 合规边界（前置声明）

Plugin Topology 是用户显式验收的独立模块线（M1–M5 已验收）。M6 继续把"精准关系链调用"的调用面做完整，但严格限定：

- 运行仅由**用户/桌面显式触发**（幂等键 + 单次 + fail-closed），无后台调度、无自动重试、无定时任务。
- 每一节点仍走 M5 ISO 门与双重策略（`topology.chain.execute` + `topology.chain.execute.isolated`），子进程仍为已验证内置插件 + `python -I` + 只读 + 无网络。
- 演练全部落在独立测试库 `audit_network_test` 与受控 staging 目录；不触达生产数据库、真实基础设施、外部网络。
- 账本约束延续：`execution_ledger` 仍 RLS + FORCE + 仅 INSERT+SELECT（不可 UPDATE/DELETE、不可硬删）；运行状态迁移仅经服务层受控路径。

## 1. 范围

| 做 | 不做 |
| --- | --- |
| 链级运行：任意满足预检（发布锁定 + 门控放行）的调用链可发起隔离执行运行，独立 `run_id` 幂等分组 | 后台队列、租约 Worker、定时/重试调度、Temporal、Agent 任务 |
| 运行状态机 `running → success/failed`（终态一次回写、CAS 防篡改），节点账本行带 `run_id` 可追溯 | 把隔离执行冒充 simulated；静默跳过低级；自动重试或降级 |
| 同链并发守卫：已有 `running` 运行 → 409；幂等键重放返回既有运行（201 语义） | 同链无界并发运行；覆盖/硬删历史运行与账本 |
| 运行预检门控：`release_locked=true`、意图无 denied / 无待决审批（复用 M4 `gate_blockers`） | 未锁定发布/含待决审批的链启动任何子进程 |
| 运行投影与只读查询：`list_runs` / `get_run`（运行 + 其账本行聚合血缘） | 查询越界 limit；跨租户可见性 |
| 只读 GUI：「执行运行」tab（运行列表 + 详情账本行）+「发起受限演练运行」按钮（reason 必填、二次确认、运行中禁发） | 桌面暴露任意子进程/调度入口、绕过 Policy 网关 |

## 2. 契约先行（先于服务逻辑）

新增 2 份 Schema、扩展 1 份（`contracts/jsonschema/`，沿用内联样例测试风格），不动 M4/M5 已验收语义：

| Schema | 内容 | 关键约束 |
| --- | --- | --- |
| `chain-run-request.schema.json`（新） | 发起一次链级执行运行：`chain_key`、`mode`、`reason`、`idempotency_key` | `mode` 恒 `const: isolated`（simulated 仍走 M4 `POST .../executions`）；`reason` 必填（演练用途，非空）；幂等键必填；`chain_key` 非空 |
| `execution-run.schema.json`（新） | 运行只读投影：`run_id`、`chain_key`、`mode`、`status`、`node_total/node_succeeded/node_failed`、`reason`、`trace_id`、`started_at`、`finished_at` | `status ∈ [running, success, failed]`；`mode` 恒 isolated；节点计数 `>= 0`；不携带账本正文/payload |
| `execution-ledger.schema.json`（扩展） | 新增可选字段 `run_id`（uuid 字符串） | 账本行可按 `run_id` 分组追溯；既有约束（只写 Refs/checksum、禁 payload 正文）不变 |

契约测试：3 组正例（运行请求、运行投影、带 `run_id` 的账本均通过）；`mode` 越界、运行空 reason、缺幂等键、投影 `status` 越界、节点计数为负、账本 `run_id` 非 uuid 一律拒绝。

## 3. 表结构（迁移 0042）

`migrations/versions/0042_chain_execution_runs.py`，`down_revision = "0041_isolated_execution"`（revision 23 字符 < 32 上限）。全部幂等；`execution_runs` 为**编排控制面**（服务受控 UPDATE 状态迁移），`execution_ledger` 仍为**不可变证据面**。

```sql
CREATE TABLE IF NOT EXISTS topology.execution_runs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES iam.tenants(id),
  chain_id uuid NOT NULL REFERENCES topology.invocation_chains(id),
  chain_key text NOT NULL,
  mode text NOT NULL DEFAULT 'isolated' CHECK (mode = 'isolated'),
  status text NOT NULL CHECK (status IN ('running', 'success', 'failed')),
  reason text NOT NULL,
  idempotency_key text NOT NULL,
  trace_id text NOT NULL,
  node_total int NOT NULL DEFAULT 0,
  node_succeeded int NOT NULL DEFAULT 0,
  node_failed int NOT NULL DEFAULT 0,
  chain_checksum text NOT NULL,
  planner_version text NOT NULL,
  started_at timestamptz NOT NULL DEFAULT now(),
  finished_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(tenant_id, idempotency_key)
);
CREATE INDEX IF NOT EXISTS topology_execution_runs_chain_idx
  ON topology.execution_runs (tenant_id, chain_id, created_at DESC);

ALTER TABLE topology.execution_ledger ADD COLUMN IF NOT EXISTS run_id uuid;
CREATE INDEX IF NOT EXISTS topology_execution_ledger_run_idx
  ON topology.execution_ledger (tenant_id, run_id);

-- 唯一约束从「链·slot·mode」单次语义改为「运行内·slot·mode」：同一链可发起多次独立运行
ALTER TABLE topology.execution_ledger
  DROP CONSTRAINT IF EXISTS execution_ledger_tenant_id_chain_id_plan_node_slot_key_ordinal_mode_key;
ALTER TABLE topology.execution_ledger
  ADD CONSTRAINT execution_ledger_run_slot_unique
  UNIQUE (tenant_id, run_id, plan_node_slot_key, ordinal, mode);
```

- `run_id` 允许 NULL：既有 simulated/isolated 历史行保持合法（Postgres UNIQUE 对 NULL 按 distinct 处理，不冲突），退回 M5 服务层语义。
- 两表 RLS `ENABLE + FORCE` + 租户策略；`execution_runs` 授予 `SELECT, INSERT, UPDATE ON ... TO audit_app`（状态迁移仅服务层 CAS 使用，无 UPDATE 端点），`execution_ledger` 维持 `GRANT SELECT, INSERT`，二者均不授予 DELETE。
- 策略不变：复用 M5 `topology.chain.execute` + `topology.chain.execute.isolated`（默认 inactive，fail-closed）与 `topology.execution.read`；不新增策略规则。

## 4. 服务层

```
packages/plugin_topology/
├── executor.py       # 不变（M4 simulated；ISO 分支委托 runs.py / isolated.py 的现有实现）
├── isolated.py       # 扩展：IsolatedChainExecutor 接受可选 run_id，账本 INSERT 携带 run_id（移除 already_executed 单次语义）
├── runs.py           # 新增：RunService——预检 → 创建运行 → 逐节点执行 → finalize → 查询投影
└── service.py        # 扩展：暴露 start_chain_run / list_runs / get_run（委托 runs.py）
```

- **运行预检（fail-closed，先于任何子进程）**：请求 schema 校验 + 双策略 gate（`topology.chain.execute` + `.isolated`）→ 链存在 → `chain_json->>'release_locked' == 'true'`（未锁定发布直接拒绝）→ 复用 M4 `gate_blockers`（任一 denied / `requires_approval` 待决 → 整链拒绝）→ 同链无 `status='running'` 运行（否则 409 并发冲突）。
- **创建运行**：短事务 INSERT `execution_runs(status='running')`（幂等键 `UNIQUE(tenant_id, idempotency_key)` 冲突 → 查询并重放既有运行，201 语义）；提交后退出连接再执行，不在子进程期间持锁。
- **逐节点执行**：委托 `IsolatedChainExecutor`（M5 全套：ISO 门 → db_artifacts/上游 artifact 物化 → 每节点独立 `python -I` 子进程 → 输出 artifact SHA256）执行全部节点并依次 INSERT 账本行（携带 `run_id`）；统计 `node_succeeded/node_failed`；任一节点失败 → 账本该节点 `failed` + 终止后续节点（M5 语义不变）。
- **finalize**：按统计 CAS 更新 `execution_runs SET status='success'|'failed', node_total/succeeded/failed, finished_at WHERE id=%s AND status='running'`；影响行数 ≠ 1 → fail-closed（并发/被篡改），不静默通过。
- **查询投影**：`list_runs(chain_key, limit)`（运行摘要，`order by created_at desc`）；`get_run(run_id)`（运行 + 其全部账本行聚合，校验租户归属）；响应经 `execution-run.schema.json` 校验后返回。

## 5. API 与桌面只读 GUI

- API（`apps/api/main.py`，全部经 Policy 网关 + 租户 + Trace + 幂等键）：
  - `POST /api/v1/topology/chains/{chain_key}/runs`（`topology.chain.execute`/medium + `topology.chain.execute.isolated`）→ 运行预检 → 发起运行；进行中并发 409；未锁定发布/待决审批 403/409 fail-closed。
  - `GET /api/v1/topology/chains/{chain_key}/runs`（`topology.execution.read`/read_only）→ 运行摘要列表。
  - `GET /api/v1/topology/runs/{run_id}`（`topology.execution.read`/read_only）→ 运行详情 + 账本行血缘。
  - Electron IPC 白名单新增三条 runs 路径（沿用 chains 精确路径风格，无通配符）。
- GUI（`desktop/src/App.tsx`，复用 M4/M5 拓扑目录卡片）：
  - 新增「执行运行」tab：运行列表（`run_id` 截断、isolated 标签、`running/success/failed` 状态标签、节点成功/失败、reason、时间），选中运行展开其账本行（slot/序号/插件@版本/输入输出校验和/artifact refs）。
  - 新增「发起受限演练运行」按钮：链选择 + reason 必填输入 + 二次确认弹窗（注明仅限测试库、已验证内置插件只读隔离子进程、未经 `execute.isolated` 权限将 403）；存在 `running` 运行该按钮禁用（409 提示）。
  - 横幅更新为「规划，不执行 · 影子模拟 · 受限只读演练（经网关 · 逐运行账本）」；桌面仍不暴露任意子进程/调度入口。

## 6. 测试计划

| 层 | 用例 |
| --- | --- |
| 契约 | 2 份新 Schema 正反例 + `execution-ledger` 扩展：`mode` 越界、运行空 reason、缺幂等键、投影 `status` 越界、节点计数为负、`run_id` 非 uuid、账本带 payload 拒绝 |
| 单元 | 运行预检（未锁定发布/denied/待决审批 → 拒绝零子进程）；fake runtime 下 `start_chain_run` 状态机（全成功 → success；任一节点失败/异常 → failed 且统计正确）；finalize CAS 影响行数 ≠ 1 → fail-closed；幂等重放返回既有运行；同链并发 `running` → 409 |
| 集成 | 种子链 `ledger-quality → research-note` 发起运行：db_artifacts 物化 → 两个真实只读子进程 → 账本两行带同一 `run_id`、`output_checksum`=真实文件 SHA256 → run `success`；同链用不同幂等键第二次运行成功（验证 M6 多运行语义取代 M5 `already_executed`）；幂等键重放返回同一运行；运行中并发 409；无 `.isolated` 权限 → 403 且零子进程；RLS 跨租户不可见；`execution_runs` 无 DELETE 权限 |
| GUI | Vitest（运行 tab 渲染 + 受限演练按钮/禁用态 mock）+ 桌面 typecheck |

回归：`python -m pytest -q`、`python -m ruff check .`、`python -m mypy packages`、`npm run typecheck`。

## 7. 验收对照（M6 六条）

1. **运行语义**：任意发布锁定且门控放行的调用链可发起受限只读隔离执行运行（独立 `run_id`），账本行带 `run_id` 可追溯；M5 单次 `already_executed` 语义被多运行语义替代。
2. **状态机**：`running → 全节点成功 → success`；任一节点失败/异常 → `failed`（账本如实记录）；不自动重试、不降级、不把隔离执行冒充 simulated。
3. **并发与幂等**：同链已有 `running` 运行 → 409；幂等键重放返回既有运行（201）；finalize 以 CAS 写终态、影响行数异常即 fail-closed。
4. **预检门控**：未锁定发布、含 denied/待决审批的链发起运行 → 拒绝且零子进程；双策略未放行 → 403 零子进程。
5. **证据约束延续**：`execution_ledger` 仍不可 UPDATE/DELETE、RLS + FORCE、仅 INSERT+SELECT；运行状态迁移仅服务层受控路径；跨租户不可见。
6. **GUI 受限入口**：桌面经 Policy 网关提供运行入口（reason 必填 + 二次确认 + 运行中禁发），无任意子进程/调度入口；`.isolated` 默认 inactive → 未放行即 403。

完成后：全量回归、更新 `docs/status.md`、`plugins/README.md` 与本文档状态。

## 9. 已实施并验收（2026-09-07）

### 交付清单

- **契约先行**：新增 `contracts/jsonschema/chain-run-request.schema.json`（`mode` 恒 `const: isolated`、`reason`/`idempotency_key` 必填）与 `execution-run.schema.json`（`status ∈ [running, success, failed]`、`mode` 恒 isolated、节点计数 ≥0、不携带账本正文）；`execution-ledger.schema.json` 扩展可选 `run_id`（uuid 格式经 FormatChecker）。
- **迁移 0042/0043**：`topology.execution_runs` 运行编排控制面表（CHECK mode/status、`UNIQUE(tenant_id, idempotency_key)`、RLS + FORCE、`GRANT SELECT/INSERT/UPDATE` 无 DELETE）；`execution_ledger` 新增 `run_id uuid` 列 + `(tenant_id, run_id)` 索引，唯一约束由「链·slot·mode」改为「run·slot·mode」（存量 NULL run_id 合法；同链可多次独立运行）；0043 partial unique index `(tenant_id, chain_id) WHERE status='running'` 为并发硬背压。显式应用 test/dev 库。
- **服务层**：`packages/plugin_topology/runs.py`（`load_chain_context`/`preflight` fail-closed/`begin_run` 事务内并发检查/`finalize_run` CAS `WHERE status='running'`/`run_projection`/`list_run_rows`/`run_entries`）；`isolated.py` 的 `IsolatedChainExecutor` 支持 `run_id` 账本分组并移除 M5 `already_executed` 单次语义；`service.py` 新增 `start_run`（双策略门控 → 预检 → begin_run → 逐节点隔离执行 → CAS finalize → 幂等记录；重放幂等键返回既有运行）与 `list_runs`/`get_run`。
- **API**：`POST /api/v1/topology/chains/{chain_key}/runs`（`topology.chain.execute` + `.isolated` 双裁决；`RunConflictError`→409、`PermissionError`→403、fail-closed 零子进程）与 `GET .../runs`、`GET /api/v1/topology/runs/{run_id}`（`topology.execution.read` 只读投影 + 账本血缘）；Electron IPC 白名单三条 runs 路径（无通配符）。
- **桌面只读 GUI**：插件工作台「执行运行」标签页（运行列表：run_id 截断/isolated/状态标签/节点成功·失败计数/reason/时间；展开加载该 run_id 的逐节点账本血缘）+「发起受限演练运行」按钮（链选择 + reason 必填 + 二次确认弹窗 + 有 running 运行/空 reason/未选链禁用并提示 409）；横幅更新「受限只读演练（经网关 · 逐运行账本）」；模型层新增 `runStatusLabel`/`anyRunRunning` 纯函数。桌面仍不暴露任意子进程与调度入口。

### 验收对照（六条全过）

1. **运行语义**：种子链 `ledger-quality → research-note` 发起运行 → 独立 `run_id`、两节点真实只读子进程、账本两行带同一 `run_id`、`output_checksum`=真实文件 SHA256；同链用新幂等键可再次运行（M5 `already_executed` 单次语义已退役）。
2. **状态机**：集成与单元测试验证 `running → success`（全节点成功）与 `running → failed`（任一节点失败/异常，账本如实记录、终止后续节点），无自动重试/降级，隔离执行不冒充 simulated。
3. **并发与幂等**：同链已有 `running` 运行 → `RunConflictError` → HTTP 409（API 层验证）；幂等键重放返回既有运行（`idempotent: true`）；`finalize_run` CAS 影响行数 ≠ 1 → fail-closed。
4. **预检门控**：单元测试覆盖未锁定发布/denied/待决审批/无节点 → 拒绝且零子进程；双策略缺 `.isolated` → 403 零子进程；API fail-closed 无活跃策略 → 403/409。
5. **证据约束延续**：`execution_runs` 无 DELETE 权限、`execution_ledger` 仅 INSERT+SELECT + RLS + FORCE；RLS 跨租户不可见（运行列表与逐运行账本均为 0）；状态迁移仅服务层 CAS 路径。
6. **GUI 受限入口**：运行入口经 Policy 网关（reason 必填 + 二次确认 + 运行中禁发 + 409 提示）；`.isolated` 策略默认 inactive → 未放行即 403 零子进程；无任意子进程/调度入口。

### 回归结果

- 全量 Python **623 passed, 2 skipped**（两个默认关闭的真实 MinerU 集成）；Ruff 按项目默认规则集（F/E4/E7/E9）全仓通过（本机 ruff 0.16.6 较基线 0.15.20 新增默认 I001 import 排序变体，不影响实际改动）、Mypy（packages 44 源）通过、桌面 TypeScript typecheck 与 Vitest（8/8）及生产构建均通过。
- 修复：M5 隔离集成测试的 M6 兼容块（缩进与残留标记）、`runs.py::begin_run` 返回类型（Any → UUID）。
- 主库/测试库迁移头：`0043_chain_run_exclusive_running`；新增契约测试 3 组正反例、运行单元测试 12 例、运行 + M5 隔离集成测试 16 例（含真实子进程演练）。

## 8. 与后续里程碑的衔接

- 本阶段**不创建** Agent 任务、不引入 Temporal/生产调度器/队列租约（向后留给编排里程碑，需另行批准）。
- 运行仍只覆盖**已验证内置插件 + 已发布锁定的调用链**；第三方插件来源信任与签名、生产运行时的完整调用面留待后续，需单独流程。
- 默认 fail-closed：`topology.chain.execute.isolated` 未显式激活时所有运行请求 403 且零子进程；simulated 路径与 M4/M5 验收保持兼容。