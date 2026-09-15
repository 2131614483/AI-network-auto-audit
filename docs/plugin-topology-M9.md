# Plugin Topology M9 实施计划：证据链完整性与只读审计导出——补足 M0 四图中的「E 图」

> 状态：**已实施并验收**（2026-09-07）。
> 关联：`docs/plugin-topology-M8.md`、`docs/plugin-topology-orchestration.md`、`AGENTS.md`。
> M9 目标：M1–M8 已产出 9 类相互独立但彼此之间仅有外键与时间序列关系的证据账本（发布、路由计划、链、调用意向、审批、执行账本、运行、验证/裁定、修复提案/决策/重跑血缘）。M9 把这些证据串成**每租户一条不可变哈希链**（逐行 SHA256 + 前驱链指针），提供：(1) **全链一致性证明**——任意一环被改写即校验失败并精确定位首个差异环（表 + 主键 + 环号）；(2) **只读审计导出**——按租户导出证据子集 JSON + 整体 SHA256 + 证明摘要。全程纯 DB、只读源表、零新增执行面、不触达主机/网络/基础设施，补足 M0 四图分治中至今空白的「E 图（证据与数据血缘图）」。**不引入** Agent 任务、调度器、真实回滚执行、第三方插件签名。

## 0. 合规边界（前置声明）

- 锚定由**用户/桌面显式触发**（幂等键 + 单次 + fail-closed），无后台调度、无自动重试、无定时任务；不自动对被锚证据发起任何动作。

- 锚点行是**追加型证据**：`evidence_chain_anchors` 每行仅 INSERT 一次，链只可增长、不可覆写、不可硬删；同一次锚定内的多行在一个事务中提交，部分失败整体回滚。

- 锚定/校验/导出**只读取源表做确定性序列化 + SHA256，不修改任何 M1–M8 已有表、不改其语义、不新增更新路径**；源表行被历史流程正常保留。

- 演练全部落在独立测试库 `audit_network_test`；不触达生产数据库与真实基础设施；桌面仅提供只读入口与证明展示。

- 证据约束延续：`evidence_chain_anchors` 采用 RLS + FORCE + 仅 INSERT+SELECT（不可 UPDATE/DELETE、不可硬删）；写入经 Policy 网关 + 租户 + Trace + 幂等键。

## 1. 范围

| 做                                                                                     | 不做                            |
| ------------------------------------------------------------------------------------- | ----------------------------- |
| M1–M8 证据账本逐行锚定进每租户一条哈希链（行级 SHA256 + 前驱链指针）                                            | 锚定非证据表正文/payload 大字段；锚定不存在的租户 |
| 全链一致性校验：重算行哈希 + 校验前驱连续性，返回 `verified` 或**首个差异环定位**（源表 + 主键 + 环号）                      | 推荐式的自动修复；越界 limit 校验          |
| 只读审计导出：按租户/范围导出证据子集 JSON + 整体 SHA256 + 证明摘要                                           | 创建真实文件/写盘；导出非授权租户数据           |
| 门控 fail-closed：`topology.evidence.anchor/verify/export` 三个能力默认 inactive → 403 零写入零子进程 | 跨租户观测；绕过 Policy 网关            |
| 只读 GUI：拓扑目录「证据链」标签页（链状态 / 发起锚定 / 校验 / 导出摘要展示）                                         | 桌面暴露任何执行/删除/覆盖入口              |

## 2. 契约先行（先于服务逻辑）

新增 4 份 Schema（`contracts/jsonschema/`，命名沿用 `topology-` 前缀，避免与 Phase 7/8 的 `audit-evidence-chain.schema.json` / `evidence-lineage.schema.json` 冲突），不动 M1–M8 已验收语义：

| Schema                                            | 内容                                                                                                                                                       | 关键约束                                                                                                      |
| ------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------- |
| `topology-evidence-anchor-request.schema.json`（新） | 发起一次锚定：`tenant_id`、`scope`（`full` 或 `execution` / `verification` / `remediation` 子域，缺省 `full`）、`idempotency_key`                                         | `scope` 枚举闭合；幂等键必填；`additionalProperties: false`                                                          |
| `topology-evidence-anchor.schema.json`（新）         | 锚点行投影：`anchor_id`、`chain_key`（`evidence://{tenant}/full`）、`seq`、`source_table`、`source_pk`、`row_hash`（sha256）、`prev_hash`、`captured_at`、`trace_id`       | `row_hash`/`prev_hash` 64 位 hex；`seq ≥ 1`；`additionalProperties: false`                                   |
| `topology-evidence-proof.schema.json`（新）          | 完整性证明：`chain_key`、`scope`、`total_anchors`、`tail_hash`、`verified`（bool）、`first_mismatch`（null 或 `{seq, source_table, source_pk}`）、`checked_at`、`trace_id` | `verified` 为 bool；`first_mismatch` 与 `verified` 互斥约束（verified=true 时必 null）；`additionalProperties: false` |
| `topology-evidence-export.schema.json`（新）         | 只读导出：`chain_key`、`scope`、`entries`（锚点/源行摘要数组）、`sha256`（导出内容整体哈希）、`proof_ref`（锚定时的证明摘要：tail\_hash + total\_anchors）、`exported_at`、`trace_id`              | `entries` 数组；`sha256` 64 位 hex；不携带账本正文 payload；`additionalProperties: false`                              |

契约测试：4 组正例（请求/锚点/证明/导出均通过）；`scope` 越界、缺幂等键、`row_hash` 非 64 hex、`seq=0`、证明 `verified=true` 却带 `first_mismatch`、导出携带 payload 正文一律拒绝。

## 3. 表结构（迁移 0046）

`migrations/versions/0046_evidence_chain.py`，`down_revision = "0045_remediation_proposals"`（revision 21 字符 < 32 上限）。全部幂等；锚点表为**不可变证据面**（仅 INSERT+SELECT，无 UPDATE/DELETE）。

```sql
CREATE TABLE IF NOT EXISTS topology.evidence_chain_anchors (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES iam.tenants(id),
  chain_key text NOT NULL,
  seq bigint GENERATED ALWAYS AS IDENTITY (START WITH 1),
  source_table text NOT NULL,
  source_pk text NOT NULL,
  row_hash text NOT NULL,          -- sha256(source_row 确定性序列化)
  prev_hash text NOT NULL,          -- sha256(上一条锚点行完整字段)
  group_id uuid NOT NULL,           -- 同一次锚定事务共享，便于定位锚定批次
  anchor_scope text NOT NULL DEFAULT 'full',
  trace_id text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(chain_key, seq),
  UNIQUE(tenant_id, group_id, source_table, source_pk)
);
CREATE INDEX IF NOT EXISTS topology_evidence_chain_tenant_idx
  ON topology.evidence_chain_anchors (tenant_id, chain_key, seq);
CREATE INDEX IF NOT EXISTS topology_evidence_chain_source_idx
  ON topology.evidence_chain_anchors (tenant_id, source_table, source_pk);
```

- 表 RLS `ENABLE + FORCE` + 租户策略；`GRANT SELECT, INSERT ON topology.evidence_chain_anchors TO audit_app`，**无 UPDATE/DELETE**。

- `prev_hash` 由服务层在事务内计算：读取上一尾穗行（`SELECT ... ORDER BY seq DESC LIMIT 1`），取该行全部锚点字段的确定性序列化哈希作为当前行的 `prev_hash`；无前驱时（首环）`prev_hash = sha256(chain_key)`（种子哈希，可复现）。

- `row_hash`：对源表目标行的**全部列**做确定性序列化（列名字典序 + JSON 编码，uuid/datetime/jsonb 归一化），同一行重复锚定产生相同哈希 → 校验可复现；不含源表正文/payload 之外的任何内容。

- `UNIQUE(tenant_id, group_id, source_table, source_pk)` 保证同一租户同一批次内同一源行只锚一次（同批幂等），跨批允许重复锚定（快照语义：同一行的历史版本随源表只增行 → 新批次捕获新状态）。

**锚定数据源（M1–M8 证据账本）**，按 `scope` 划分：

| scope          | 源表                                                                                                      |
| -------------- | ------------------------------------------------------------------------------------------------------- |
| `topology`     | `topology.topology_releases`、`routing_plans`、`routing_plan_nodes`、`routing_plan_edges`、`domain_bridges` |
| `chain`        | `invocation_chains`、`invocation_chain_nodes`、`invocation_intents`                                       |
| `execution`    | `invocation_approvals`、`execution_runs`、`execution_ledger`                                              |
| `verification` | `run_verifications`                                                                                     |
| `remediation`  | `remediation_proposals`、`remediation_decisions`、`remediation_run_links`                                 |
| `full`         | 以上全部                                                                                                    |

> 说明：控制面注册表（clusters / blueprints / compatibility\_results 等）属可变登记目录，发布层面的冻结语义已由 `topology_releases` 携带校验和；M9 锚定清单聚焦「证据与账本」，不锚定目录正文，保持 E 图「仅不可变引用/投影」边界。

## 4. 服务层

```
packages/plugin_topology/
├── remediation.py   # 不变（M8 语义保持原样）
├── evidence.py      # 新增：EvidenceService——确定性序列化 → 锚定 → 链校验 → 导出
└── service.py       # 扩展：暴露 anchor_evidence / verify_evidence_chain / export_evidence / get_evidence_chain_status（委托 evidence.py）
```

- **确定性序列化**：`canonical_row(row)`——按列名 dict 序编码（`psycopg2` 行 dict → `json.dumps(..., sort_keys=True, default=str)`，uuid/Decimal/datetime/jsonb 归一化），输出唯一字符串，`sha256` 得 `row_hash`。

- **锚定（fail-closed，先于任何写入）**：请求 schema 校验 + 能力门控 `topology.evidence.anchor` → 租户归属校验 → 按 scope 解析源表清单 → `SELECT`（注入 `tenant_id` 过滤，受 RLS 约束）→ 逐行 `canonical_row` → 事务内：读尾穗 → 计算 prev\_hash → 批量 INSERT 锚点行（同批 `group_id`）→ 幂等键冲突重放返回既有一侧锚定摘要（`group_id` 复用）。任一源表缺权限 → 整体 `EvidenceError` 零写入。

- **链校验（只读）**：能力门控 `topology.evidence.verify` → 重读全部锚点行（受 RLS）→ 对每行重算 `row_hash`（重读源表对应行）比对 → 沿 `seq` 校验 `prev_hash` 连续性 → 返回 `verified=True + tail_hash`，或 `verified=False + first_mismatch{seq, source_table, source_pk}`（首个差异环：源行哈希不符优先，其次前驱断链）。

- **只读导出（只读）**：能力门控 `topology.evidence.export` → 按 scope 读锚点 + 源行摘要（id/时间戳/哈希级字段，不携带 payload 正文）→ 组装 entries 为排序 JSON → 整体 `sha256` → 返回 export + 证明摘要（tail\_hash / total\_anchors）。导出为 API 响应体，不落盘为真实文件。

## 5. API 与桌面只读 GUI

- API（`apps/api/main.py`，全部经 Policy 网关 + 租户 + Trace；锚定为写操作需幂等键，校验/导出为只读）：

  - `POST /api/v1/topology/evidence/anchors`（`topology.evidence.anchor`/medium）→ 发起一次锚定；scope 越界/跨租户 → 400/403 fail-closed。

  - `GET /api/v1/topology/evidence/chain`（`topology.evidence.verify`/read\_only）→ 链状态（total\_anchors、tail\_hash、最近锚定时间）。

  - `POST /api/v1/topology/evidence/chain/verify`（`topology.evidence.verify`/read\_only）→ 执行全链校验，返回 proof（verified / first\_mismatch）。

  - `GET /api/v1/topology/evidence/export`（`topology.evidence.export`/read\_only，`?scope=`）→ 只读导出 JSON + sha256 + 证明摘要。

  - Electron IPC 白名单新增四条 evidence 路径（沿用精确路径风格，无通配符）。

- GUI（`desktop/src/App.tsx` + `styles/globals.css`，拓扑目录卡新增「证据链」标签页）：

  - 链状态卡：`chain_key`、锚点总数、尾哈希（截断展示）、最近锚定时间；无状态时展示「尚未锚定」。

  - 操作区（经 Policy 网关 + 幂等键）：「发起锚定」（scope 选择 full/execution/verification/remediation + 二次确认注明纯证据投影、未获 `topology.evidence.anchor` 将 403）、「校验完整性」（结果 `verified` 绿色标签或 `first_mismatch` 红色定位卡：源表/主键/环号）、「导出审计摘要」（展示 sha256 + tail\_hash + entries 条数）。

  - 模型层新增 `evidenceChainStatusLabel` / `evidenceMismatchLabel` 纯函数 + Vitest 单测；桌面仍不暴露任何执行/删除/覆盖入口。

## 6. 测试计划

| 层   | 用例                                                                                                                                                                                                                                                  |
| --- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 契约  | 4 份新 Schema 正反例：scope 越界、缺幂等键、row\_hash/prev\_hash 非 64 hex、seq=0、verified=true 带 first\_mismatch、导出携带 payload 正文拒绝                                                                                                                                 |
| 单元  | 确定性序列化（同一行两次哈希一致、列序无关）；锚定预检（能力/scope/租户 → 拒绝零写入）；幂等键重放返回同 group\_id；链校验（伪造断链定位首个差异环）；导出哈希与组装稳定（fake cursor 脚本化驱动）                                                                                                                                   |
| 集成  | 种子链（复用既有 M3/M6/M7 数据）：锚定 full → 手工篡改一行源表（测试库用审计管理员连接直接 UPDATE）→ verify 返回 first\_mismatch 定位该表/主键/环号 → 恢复后再校验 verified；指定 scope 锚定仅含对应域；无 `topology.evidence.anchor/verify/export` 权限 → 403 零写入零子进程；幂等键重放；RLS 跨租户不可见；锚点表无 UPDATE/DELETE 权限（psql 验证） |
| GUI | Vitest（链状态/差异定位/导出摘要标签、按钮禁用态、项目投影 mock）+ 桌面 typecheck                                                                                                                                                                                               |

回归：`python -m pytest -q`、`python -m ruff check .`、`python -m mypy packages`、`npm run typecheck`、`npm test`、`npm run build`。

## 7. 验收对照（M9 六条）

1. **锚定语义**：M1–M8 证据账本可被明确触发的锚定追加进每租户一条哈希链；锚点行仅 INSERT+SELECT，链只可增长、不可覆写/硬删；同批锚定单事务原子（部分失败整体回滚）。
2. **完整性证明**：校验全量重算行哈希 + 校验前驱链连续性；任何一环被改写 → `verified=False` 并**精确定位首个差异环**（源表 + 主键 + 环号）。
3. **只读导出**：按租户/范围导出证据子集 JSON + 整体 SHA256 + 锚定证明摘要；导出为 API 响应体，不产生真实文件；不携带账本正文 payload。
4. **门控 fail-closed**：`topology.evidence.anchor/verify/export` 三个能力默认 inactive → 403 零写入零子进程；scope 越界/跨租户 → 拒绝。
5. **证据约束延续**：锚点表 RLS + FORCE + 仅 INSERT+SELECT，无表级 UPDATE/DELETE；跨租户不可见；不修改任何 M1–M8 既有表。
6. **GUI 受限入口**：桌面经 Policy 网关提供「发起锚定 / 校验完整性 / 导出审计摘要」入口（scope 选择 + 二次确认 + 能力未授予提示），链状态与差异定位只读展示，无任何执行/回滚/删除按钮。

完成后：全量回归、更新 `docs/status.md`、`plugins/README.md` 与本文档状态。

## 8. 与后续里程碑的衔接

- 本阶段**不创建** Agent 任务、不引入 Temporal/生产调度器/队列租约、不实现真实回滚执行、不做第三方插件签名/信任。

- 后续候选：规则驱动自动处置（策略矩阵）、跨链漂移影响面分析、健康信号落地调度、E 图血缘可视化（锚点之上的派生关系投影）。

***

## 9. 交付记录（2026-09-07 已实施并验收）

### 9.1 交付清单

- [x] **契约先行**：`contracts/jsonschema/` 新增 4 份 Schema（`topology-evidence-anchor-request` / `topology-evidence-anchor` / `topology-evidence-proof` / `topology-evidence-export`），`contracts.py` 增加对应 `validate_*`；契约正反例测试全绿（scope 越界、缺幂等键、非 64hex 哈希、seq=0、verified=true 带 first\_mismatch、导出携带 payload 正文一律拒绝）。命名沿用 `topology-` 前缀规避与 Phase 7/8 及 AIOps 既有共享契约冲突。

- [x] **迁移 0046 + 0047**：`topology.evidence_chain_anchors` 追加式哈希链表（RLS + FORCE、仅 SELECT+INSERT 无 UPDATE/DELETE、`UNIQUE(tenant_id, chain_key, seq)` 链内序号唯一、`UNIQUE(tenant_id, group_id, source_table, source_pk)` 批量幂等、`row_hash/prev_hash` 64hex CHECK）；0047 移除 `seq` 的 IDENTITY 改为应用侧自管链内序号（适配链式连续性与批量空集场景）；三个策略 `topology.evidence.anchor/verify/export` 种子默认 `inactive`（fail-closed）；显式应用 test/dev 库。

- [x] **服务层**：`packages/plugin_topology/evidence.py` 提供确定性序列化 `canonical_row`、`anchor_evidence`（幂等批量追加、`_row_pk` 处理无 `id` 表）、`verify_evidence_chain`（两趟校验 + 首位失配定位）、`export_evidence`（响应体导出 + proof 摘要）、`chain_status`；`service.py` 新增 4 个策略网关方法。

- [x] **API + IPC**：`POST /api/v1/topology/evidence/anchors`、`GET /api/v1/topology/evidence/verify|export|status` 共 4 端点（全部经 Policy 网关 + 租户 + Trace + 幂等键；400 scope 越界 / 403 fail-closed）；Electron IPC 白名单声明 4 条 evidence 路径。

- [x] **测试（零子进程）**：单元 `tests/unit/test_plugin_topology_evidence_unit.py`（fake cursor 脚本化驱动）+ 集成 `tests/integration/test_plugin_topology_evidence_integration.py`（篡改定位闭环 + try/finally 清理 + 幂等重放 + RLS + 无写权限 psql 验证 + 403 零写入零子进程）。

- [x] **桌面只读 GUI**：「拓扑目录」新增「证据链」标签页（链状态 / scope 选择 + 锚定/校验/只读导出 / 一致性证明卡 / 导出摘要卡 / 锚点条目表）；模型层新增 5 个纯函数 + 4 组类型，配套 5 项 Vitest 单测（`pluginTopology.test.ts` 新增 M9 describe）。

### 9.2 验收对照（六条全部满足）

| 验收点              | 落实                                                                                                             |
| ---------------- | -------------------------------------------------------------------------------------------------------------- |
| 1 锚定语义           | `evidence_chain_anchors` 仅 INSERT+SELECT、链只增不可覆写/硬删；同批单事务原子；幂等键重放返回既有批                                         |
| 2 完整性证明          | verify 重算行哈希 + 回放前驱链；改写任一环 → `verified=False` 且 `first_mismatch` 定位表/主键/环号（集成测试真实 UPDATE 篡改→定位→恢复→verified 闭环） |
| 3 只读导出           | 按租户/scope 导出锚点元数据 + 整体 sha256 + `proof_ref{tail_hash, total_anchors}`；仅 API 响应体不落盘、不携带账本正文 payload             |
| 4 门控 fail-closed | 三能力默认 inactive → 403 零写入零子进程；scope 越界/跨租户拒绝（集成 403 断言）                                                         |
| 5 证据约束延续         | RLS + FORCE + 仅 INSERT+SELECT（psql 验证无 UPDATE/DELETE）；不修改任何 M1–M8 既有表                                          |
| 6 GUI 受限入口       | 桌面仅锚定/校验/导出 + 状态展示；无执行/回滚/删除按钮；能力未授予 403 提示                                                                    |

### 9.3 回归结果

- Python：**733 passed, 2 skipped**（两个默认关闭的真实 MinerU 集成）。

- Ruff 全仓通过；Mypy（packages **47** 源）无告警。

- 桌面：TypeScript typecheck 通过；Vitest **19/19**（含 M9 新增 5 项）；生产构建（electron-vite + vite renderer）通过。

- 主库/测试库迁移头：`0046_evidence_chain`（含 0047 序号修正）。

- 验收记录同步：`docs/status.md`（M9 七条记录）、`plugins/README.md`（M9 验收段）。

