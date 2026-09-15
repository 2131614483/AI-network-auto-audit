# Plugin Topology M7 实施计划：运行验证·可复现性核对与回滚裁定——精准关系链调用的证据核查闭环

> 状态：**已实施并验收**（2026-09-07；契约先行→迁移→服务→API→GUI→全量回归全部完成）。
> 关联：`docs/plugin-topology-M6.md`、`docs/plugin-topology-orchestration.md`、`AGENTS.md`。
> M7 目标：M6 让链级执行运行（`run_id` + 状态机 + 逐运行账本）可用，M7 补齐**结果核查**——对 `status=success` 的隔离运行发起**运行验证（run verification）**：逐节点对照参考运行（显式 `reference_run_id` 或缺省取同链最近一次成功运行）的 `output_checksum` 做可复现性核对，全匹配 → `verified`，任一不一致/缺参照 → `drifted` 并附带**只读回滚裁定投影（rollback verdict）**（建议动作枚举，纯投影、绝不执行、不触达主机与发布）。验证为纯 DB 内比对、零子进程，验证记录进入**不可变验证账本**（仅 INSERT+SELECT）。仍限定测试库演练；**不引入**调度/自动验证/真实回滚。

## 0. 合规边界（前置声明）

- 验证仅由**用户/桌面显式触发**（幂等键 + 单次 + fail-closed），无后台调度、无自动重试、无定时任务。

- 验证过程**不启动任何子进程**（纯 DB 内 `output_checksum` 比对），不新增执行面；验证前后运行/账本语义与 M5/M6 完全一致。

- 回滚裁定是**只读投影**：`rollback_verdict` 仅含受影响节点列表、基线摘要、建议动作枚举；不含任何执行动作、不修改发布、不触发子进程、不触达主机/网络/基础设施。

- 演练全部落在独立测试库 `audit_network_test`；不触达生产数据库与真实基础设施。

- 证据约束延续：`run_verifications` 同样 RLS + FORCE + 仅 INSERT+SELECT（不可 UPDATE/DELETE、不可硬删）；写入经 Policy 网关 + 租户 + Trace + 幂等键。

## 1. 范围

| 做                                                                                                                                | 不做                                                                 |
| -------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------ |
| 运行验证：对 `status=success` 的 isolated 运行发起核查，逐节点对照账本 `output_checksum`（显式参考运行或缺省同链最近成功运行）                                           | 后台/定时/自动验证；把 simulated 账本纳入验证（验证面仅 `execution_runs` 的 isolated 运行） |
| 验证判定：全匹配 → `verified`；任一 mismatch / 参考缺 slot / 无参考 → `drifted`；节点计数守恒（matched + mismatched + ref\_missing = node\_total）         | 覆盖/硬删既有验证；静默跳过缺失对照或冒充 verified                                     |
| 只读回滚裁定：drift 时生成 `rollback_verdict`（affected\_slots + baseline + action 枚举 `re-verify/re-run-locked-release/escalate-human`，仅建议） | 执行任何回滚/修复/发布变更；把裁定当作已执行结论造假                                        |
| 并发与幂等：同一幂等键重放返回既有验证；同 (run, reference) 重复验证追加独立 verification\_id（不可变）                                                            | 无界并发验证写锁；覆盖历史验证行                                                   |
| 验证门控（fail-closed）：run 非 success / 参照不存在 / 参照跨租户 / 自身参照 → 拒绝；`topology.chain.verify` 默认 inactive → 403 零子进程                       | 对非终态运行放行验证；跨租户观测                                                   |
| 查询投影：`list_verifications` / `get_run_verification`（strict schema + `rollback_verdict` 只读投影）                                      | 查询越界 limit；携带账本正文/payload                                          |
| 只读 GUI：「执行运行」tab 增验证状态列与「发起运行验证」入口（参考运行可选 + reason 必填 + 二次确认），drift 验证展示只读回滚裁定卡                                                  | 桌面暴露回滚/修复执行按钮、绕过 Policy 网关                                         |

## 2. 契约先行（先于服务逻辑）

新增 2 份 Schema（`contracts/jsonschema/`，沿用内联样例测试风格），不动 M4/M5/M6 已验收语义：

| Schema                                    | 内容                                                                                                                                                                                                                                                                | 关键约束                                                                                                                                                                                           |
| ----------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `run-verification-request.schema.json`（新） | 发起一次运行验证：`run_id`、`reference_run_id`（可 null）、`reason`、`idempotency_key`                                                                                                                                                                                           | `run_id`/`reference_run_id`（非 null 时）为 uuid 格式（FormatChecker）；`reference_run_id != run_id`；`reason` 非空必填；幂等键必填；`additionalProperties: false`                                                   |
| `run-verification.schema.json`（新）         | 验证只读投影：`verification_id`、`run_id`、`chain_key`、`reference_run_id`（可 null）、`status ∈ [verified, drifted]`、`node_total/node_matched/node_mismatched/node_ref_missing`（≥0 且守恒）、`reason`、`rollback_verdict`（可 null；drifted 必填、verified 恒 null）、`trace_id`、`created_at` | `rollback_verdict` 子约束：`action ∈ [re-verify, re-run-locked-release, escalate-human]`、`affected_slots` 字符串数组、`baseline` 对象（reference\_run\_id/chain\_checksum/planner\_version）；不携带账本正文/payload |

契约测试：2 组正例（验证请求、验证投影均通过）；`run_id` 非 uuid、`reference_run_id` 非 uuid、`reference_run_id == run_id`、验证空 reason、缺幂等键、投影 `status` 越界、节点计数为负、计数不守恒、`verified` 仍带 `rollback_verdict`、`action` 越界、`affected_slots` 非字符串数组、携带 payload 正文一律拒绝。

## 3. 表结构（迁移 0044）

`migrations/versions/0044_chain_run_verification.py`，`down_revision = "0043_chain_run_exclusive_running"`（revision 28 字符 < 32 上限）。全部幂等；`run_verifications` 为**不可变证据面**（仅 INSERT+SELECT，无 UPDATE/DELETE）。

```sql
CREATE TABLE IF NOT EXISTS topology.run_verifications (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES iam.tenants(id),
  run_id uuid NOT NULL REFERENCES topology.execution_runs(id),
  chain_id uuid NOT NULL REFERENCES topology.invocation_chains(id),
  chain_key text NOT NULL,
  reference_run_id uuid REFERENCES topology.execution_runs(id),
  status text NOT NULL CHECK (status IN ('verified', 'drifted')),
  node_total int NOT NULL DEFAULT 0,
  node_matched int NOT NULL DEFAULT 0,
  node_mismatched int NOT NULL DEFAULT 0,
  node_ref_missing int NOT NULL DEFAULT 0,
  reason text NOT NULL,
  rollback_verdict jsonb,
  idempotency_key text NOT NULL,
  trace_id text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(tenant_id, idempotency_key),
  CHECK (node_matched + node_mismatched + node_ref_missing = node_total)
);
CREATE INDEX IF NOT EXISTS topology_run_verifications_run_idx
  ON topology.run_verifications (tenant_id, run_id, created_at DESC);
CREATE INDEX IF NOT EXISTS topology_run_verifications_chain_idx
  ON topology.run_verifications (tenant_id, chain_id, created_at DESC);
```

- RLS `ENABLE + FORCE` + 租户策略；`GRANT SELECT, INSERT ON topology.run_verifications TO audit_app`，**无 UPDATE/DELETE**。

- 策略种子：追加能力 `topology.chain.verify`（默认 inactive，fail-closed；沿用 0041 的 `jsonb_build_object('capabilities', ...)` 匹配规则种子模式）；验证**读取**复用既有 `topology.execution.read`，不新增只读能力。

- 迁移显式应用 test/dev 库，幂等可重放。

## 4. 服务层

```
packages/plugin_topology/
├── runs.py           # 不变（M6 运行生命周期保持原样）
├── verification.py   # 新增：RunVerifier——预检 → 逐节点比对 → drifted 回滚裁定投影 → 不可变写入 → 查询投影
└── service.py        # 扩展：暴露 verify_chain_run / list_chain_verifications / get_run_verification（委托 verification.py）
```

- **验证预检（fail-closed，先于任何比对）**：请求 schema 校验 + 能力门控 `topology.chain.verify` → 运行存在且租户归属正确 → `status == 'success'`（非 success → `RunVerificationError`，零写入）→ `mode == 'isolated'`（execution\_runs 恒定，防御性复核）。

- **参考解析**：显式 `reference_run_id` → 必须存在、同租户、非自身（冲突 → `VerificationConflictError`）、`status='success'`；缺省 → 同链最近一次 `success` 运行（排除自身）；无可用参考 → 全部节点计 `node_ref_missing` → `drifted` + `rollback_verdict.action='re-verify'`。

- **逐节点比对**：对运行账本行（按 slot 取 `output_checksum`）与参考运行同 slot 比对；一致条件 = 两行 `output_checksum` 相等且两者 `status='succeeded'`；参考缺该 slot → `node_ref_missing`；不一致 → `node_mismatched` + 记入 `affected_slots`。比对纯 DB 查询，**零子进程**。

- **裁定**：`node_mismatched + node_ref_missing == 0` → `verified`（`rollback_verdict = NULL`）；否则 `drifted` + 生成只读回滚裁定 `{action, affected_slots, baseline: {reference_run_id|null, chain_checksum, planner_version}}`，`action ∈ [re-verify, re-run-locked-release, escalate-human]`（mismatch 且参照完整 → `re-run-locked-release`；仅 ref\_missing → `re-verify`；mismatch 且含 ref\_missing → `escalate-human`）。裁定仅建议，业务代码不含执行动作。

- **不可变写入**：INSERT `run_verifications`；`UNIQUE(tenant_id, idempotency_key)` 冲突 → 按幂等记录/既有验证行重放返回（201 语义）。

- **查询投影**：`list_verification_rows(run_id | chain_key, limit)`（newest first）；`verification_projection(verification_id)`（strict schema 校验后返回，`rollback_verdict` 走子 schema 校验）。

## 5. API 与桌面只读 GUI

- API（`apps/api/main.py`，全部经 Policy 网关 + 租户 + Trace + 幂等键）：

  - `POST /api/v1/topology/runs/{run_id}/verifications`（`topology.chain.verify`/medium）→ 验证预检 → 比对 → 不可变落账；run 非 success 409、参照缺失/越权 403/404 fail-closed。

  - `GET /api/v1/topology/runs/{run_id}/verifications`（`topology.execution.read`/read\_only）→ 该运行验证列表。

  - `GET /api/v1/topology/verifications/{verification_id}`（`topology.execution.read`/read\_only）→ 单条验证详情（含 `rollback_verdict` 只读投影）。

  - Electron IPC 白名单新增三条 verifications 路径（沿用精确路径风格，无通配符）。

- GUI（`desktop/src/App.tsx`，复用 M6「执行运行」tab）：

  - 运行新增「验证状态」列：按最近一次验证显示 `已验证`（verified）/`漂移`（drifted）/`未验证` 标签；非 success 运行显示「不可验证」灰态。

  - 每行新增「发起运行验证」按钮：仅 `status=success` 且非进行中运行可点；弹出小表单（参考运行下拉 = 同链成功运行列表，默认最近一次；reason 必填）+ 二次确认弹窗（注明纯 DB 比对、零子进程、仅限测试库、未获 `topology.chain.verify` 将 403）。

  - 运行展开详情加载该 run 验证列表：每条显示 verified/drifted 标签 + 逐节点 `matched/mismatched/ref_missing` 与计数守恒提示；drift 验证展示**只读回滚裁定卡**（action/affected\_slots/baseline，纯文本提示、无任何执行按钮）。

  - 模型层新增 `verificationStatusLabel` / `runCanVerify` / `rollbackActionLabel` 纯函数；桌面仍不暴露回滚/修复执行或调度入口。

## 6. 测试计划

| 层   | 用例                                                                                                                                                                                                                                                                                                                                                                                      |
| --- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 契约  | 2 份新 Schema 正反例：`run_id`/`reference_run_id` 非 uuid、`reference_run_id == run_id`、空 reason、缺幂等键、`status` 越界、节点计数为负/不守恒、`verified` 带 verdict、`action` 越界、`affected_slots` 类型错误、payload 正文拒绝                                                                                                                                                                                                |
| 单元  | 验证预检（run 非 success / mode 非 isolated / 参照不存在 / 跨租户 / 自身参照 → 拒绝零写入）；参考解析（显式/缺省最近成功运行/无参考 → 全 ref\_missing）；比对（全匹配 → verified 且 verdict null；任一 mismatch → drifted + affected\_slots；缺 slot → ref\_missing）；`action` 枚举选择；计数守恒校验；幂等重放返回既有验证；fake runtime 断言验证过程**未启动任何子进程**                                                                                                               |
| 集成  | 种子链 `ledger-quality → research-note` 真实两次运行（不同幂等键、同锁链同输入）→ 对第二次发起验证（参考=第一次）→ `verified` 且逐节点 checksum 一致、计数守恒；构造输入漂移场景（换输入源使输出不同）重跑 → `drifted` + 只读回滚裁定（action 枚举合法、baseline 含 reference\_run\_id/chain\_checksum）；对 `failed` 运行发起验证 → 409 且零写入；无 `topology.chain.verify` 权限 → 403 零子进程；幂等键重放返回同一 verification\_id；RLS 跨租户不可见（列表与详情均空）；`run_verifications` 无 UPDATE/DELETE 权限（psql 验证） |
| GUI | Vitest（验证状态标签/按钮禁用态/回滚裁定卡 mock）+ 桌面 typecheck                                                                                                                                                                                                                                                                                                                                           |

回归：`python -m pytest -q`、`python -m ruff check .`、`python -m mypy packages`、`npm run typecheck`、`npm test`。

## 7. 验收对照（M7 六条）

1. **验证语义**：任意 `success` 隔离运行可发起验证（纯 DB 比对、零子进程），逐节点对照显式参考或缺省最近成功运行；验证行独立 `verification_id`，不可覆盖/硬删、可多次追加。
2. **判定正确性**：全匹配 → `verified`（无 verdict）；任一 mismatch/缺参考 → `drifted`；`node_matched + node_mismatched + node_ref_missing == node_total` 恒成立。
3. **并发与幂等**：幂等键重放返回既有验证（201）；同 (run, reference) 重复验证追加新行，终态不可翻转、无 UPDATE/DELETE 路径。
4. **预检门控**：run 非 success / 参照缺失 / 参照跨租户 / 自身参照 → fail-closed 且零写入零子进程；`topology.chain.verify` 默认 inactive → 403 零子进程。
5. **证据约束与回滚只读**：`run_verifications` RLS + FORCE + 仅 INSERT+SELECT；`rollback_verdict` 仅建议投影（action/affected\_slots/baseline），不含执行动作、不触达主机/发布；跨租户不可见。
6. **GUI 受限入口**：桌面经 Policy 网关提供只读验证状态与「发起运行验证」（参考可选 + reason 必填 + 二次确认），回滚裁定仅只读展示，无任何执行/修复入口。

完成后：全量回归、更新 `docs/status.md`、`plugins/README.md` 与本文档状态。

## 9. 已实施并验收（2026-09-07）

### 交付清单

- **契约先行**：新增 `contracts/jsonschema/run-verification-request.schema.json`（`run_id` 必填 uuid、`reference_run_id` 可选 uuid 且 `!= run_id`、`reason`/`idempotency_key` 必填）与 `run-verification.schema.json`（`status ∈ [verified, drifted]`、条件约束：`verified` → `rollback_verdict` 恒 null、`drifted` → 必填 `rollbackVerdict` 子 schema：`action ∈ [re-verify, re-run-locked-release, escalate-human]`、`affected_slots` 非空、`baseline{reference_run_id, chain_checksum, planner_version}`）；`contracts.py` 增加 `RunVerificationRequest.parse` 与 `validate_verification`（JSON Schema 不可表达的**节点计数守恒** `matched + mismatched + ref_missing == total` 强校验）。

- **迁移 0044**：`topology.run_verifications` 证据表（`status` CHECK、节点计数列、`UNIQUE(tenant_id, idempotency_key)`、库级 `CHECK` 守恒背书、`rollback_verdict jsonb` 只读投影、外键锚定 `execution_runs`；RLS + FORCE、仅 `GRANT SELECT, INSERT TO audit_app`、无 UPDATE/DELETE）；`policy.policy_sets` 追加 `topology.chain.verify`（medium/write\_data）默认 `inactive`（fail-closed）；显式应用 test/dev 库、幂等可重放。

- **服务层**：`packages/plugin_topology/verification.py`（`run_projection`/`load_run_baseline`/`choose_reference` 缺省选同链最近 `success` run/`node_checksums`/`verify_run` 比对与确定性裁定/`verification_projection`/`list_verification_rows`/`verification_by_key`；非 success、参照非 success、跨链参照、无账本行 → fail-closed 零写入）；`service.py` 新增 `verify_chain_run`（先裁决 `topology.chain.verify` → 幂等键重放 → verify → 幂等记录）与 `list_chain_verifications`/`get_run_verification`。

- **API**：`POST /api/v1/topology/runs/{run_id}/verifications`（`RunVerificationError`→409、`PermissionError`→403、fail-closed 零写入）+ `GET .../runs/{run_id}/verifications` 与 `GET /api/v1/topology/verifications/{verification_id}`（只读）；Electron IPC 白名单两条验证路径（无通配符）。

- **桌面只读 GUI**：「执行运行」标签页运行表新增「验证状态」列（已验证/漂移/未验证/不可验证）与「发起运行验证」按钮（二次确认弹窗注明只读对标、fail-closed、结果为建议投影）；展开行展示验证历史卡（节点匹配/偏差/缺参照 + 计数守恒行、幂等重放标签、漂移时「只读回滚裁定」卡含动作中文标签 + 受影响节点 + 基线参照/锁链/规划器，显式注明无任何回滚/修复执行入口）；模型层新增 `verificationStatusLabel`/`runCanVerify`/`rollbackActionLabel` 纯函数并配套单测。桌面仍不暴露子进程/回滚/调度入口。

### 验收对照（六条全过）

1. **验证语义**：真实种子链两次运行（不同幂等键、同锁链同输入）→ 对第二次发起验证（缺省参照自动选最近 success）→ `verified` 且逐节点 `output_checksum` 一致、计数守恒；纯 DB 比对、集成测试断言零子进程。
2. **判定正确性**：构造输入漂移重跑 → `drifted` + 只读回滚裁定（action 合法枚举、`affected_slots` 准确、baseline 含 reference\_run\_id/chain\_checksum/planner\_version）；计数守恒在 Schema 条件约束 + 库 CHECK 双层背书。
3. **并发与幂等**：幂等键重放返回同一验证（`idempotent: true`）；同 (run, reference) 重复验证追加独立行；终态不可翻转、无 UPDATE/DELETE 路径。
4. **预检门控**：`failed` 运行 → 409 零写入；参照跨链/非 success → 409；无 `topology.chain.verify` 权限/策略 inactive → 403（API 层 403/409 兼容断言）且零子进程零写入。
5. **证据约束与回滚只读**：`run_verifications` RLS + FORCE + 仅 INSERT+SELECT；`rollback_verdict` 仅建议投影（不含执行动作）；跨租户不可见（列表与详情均空）。
6. **GUI 受限入口**：桌面经 Policy 网关提供只读验证状态与「发起运行验证」（reason 必填 + 二次确认 + 非 success 禁发）；回滚裁定仅只读展示，无任何执行/修复入口。

### 回归结果

- 全量 Python **650 passed, 2 skipped**（两个默认关闭的真实 MinerU 集成）；Ruff 按项目默认规则集（F/E4/E7/E9）全仓通过、Mypy（packages 45 源）通过、桌面 TypeScript typecheck 与 Vitest（11/11）及生产构建均通过。

- 修复：`service.py` 移除两个 M7 未用导入（F401）；补齐 App.tsx 三个验证纯函数导入与模型层单测（Vitest 9 → 11）；M7 验证契约/集成测试 27 例通过。

- 主库/测试库迁移头：`0044_chain_run_verification`。

## 8. 与后续里程碑的衔接

- 本阶段**不创建** Agent 任务、不引入 Temporal/生产调度器/队列租约、不实现真实回滚执行（裁定仅为投影，真实修复/回滚执行面留待另行批准的阶段）。

- 验证面仅限已验证内置插件 + 已发布锁定链的 isolated 运行；第三方插件来源信任/签名、生产运行时完整调用面与自动验证留待后续，需单独流程。

- 默认 fail-closed：`topology.chain.verify` 未显式激活时一切验证请求 403 且零子进程零写入；M4/M5/M6 已验收语义保持兼容。

