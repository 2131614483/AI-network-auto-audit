# Plugin Topology M3 实施计划：精准关系链调用

> 状态：**已完成并验收**（2026-09-07 M3 全量回归通过，验收对照见本文第 7 节）。
> 关联：`docs/plugin-topology-orchestration.md`、`docs/plugin-topology-M1.md`、`docs/plugin-topology-M2.md`、`AGENTS.md`。
> M3 目标：在 `plan_only` 路由计划之上建立**精准调用链**——确定性链序（同输入+同锁版本 → 同链 + 同 checksum）、链间端口接线（输入/输出契约接续，大对象只传公共 Ref）、逐节点 `InvocationIntent`（Policy/审批裁决 + 只做数据库内状态投影），并为未来 Plugin Runtime / Policy / Agent 暴露只读 **Adapter 契约**。**不做**真实执行、Manifest 安装、AUTO 权限、外部网络调用。

## 1. 范围

| 做                                                                      | 不做                          |
| ---------------------------------------------------------------------- | --------------------------- |
| 调用链物化：确定性拓扑序 + 主选/有序替代展开 + 链 checksum                                  | 真实插件运行、外部网络调用、启动任意命令        |
| 端口接线：链内节点输入/输出契约接续，仅契约 Ref 与 4 种公共桥接 Ref                               | 在链/意图中携带证据正文或 payload 副本    |
| `InvocationIntent` 状态投影 + Policy 裁决（allowed/requires\_approval/denied） | 绕过审批的 AUTO 执行、创建真实任务        |
| 三份 Adapter 契约模板（Runtime/Policy/Agent，只读序列化视图）                          | Manifest 绑定、真实执行队列、还原为运行时调用 |
| 只读 GUI：调用链 / 意图 / Adapter 契约面板                                         | 桌面端直接触发执行或写真实运行时            |

## 2. 契约先行（先于服务逻辑）

新增 3 份 Schema（`contracts/jsonschema/`，沿用 M0 内联样例测试风格）：

| Schema                               | 内容                                                                                                                                                                | 关键约束                                                                                                |
| ------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------- |
| `invocation-chain.schema.json`       | 物化链载荷：`chain_key`、`plan_key` 锁、`chain_order`（节点主选+替代展开，纯拓扑序）、`port_bindings`（producer/consumer slot、契约版本、格式、`bind_mode`）、`chain_checksum`                         | `mode` 恒 `plan_only`；拒绝 `execute`/`auto`；禁 payload 副本                                               |
| `invocation-intent.schema.json`      | 逐节点调用意向：`slot_key`、`role`（primary/fallback）、`capability`、`expected_inputs`/`expected_outputs`（契约 Ref）、`policy_decision`、`approval_ref`、`status`（仅投影枚举）、幂等键与 trace | 拒绝 `command`/entrypoint/任意执行字段；`status` 枚举 `materialized/policy_allowed/approved_projection/denied` |
| `topology-chain-request.schema.json` | 物化服务请求载荷（幂等键 + plan\_key）                                                                                                                                         | 必须携带 `Idempotency-Key`；输出引用 `invocation-chain`                                                      |

契约测试：3 份正例通过；`execute`/`auto` 模式、intent 携带 command、绑定直接携带证据正文、缺幂等键的载荷一律拒绝。

## 3. 表结构（迁移 0039）

`migrations/versions/0039_invocation_chain.py`，`down_revision = "0038_topology_bridge_seeds"`（revision 21 字符 < 32 上限）。

新 3 张表（每表 RLS ENABLE+FORCE+租户策略+`GRANT SELECT, INSERT, UPDATE TO audit_app`，全部 `IF NOT EXISTS`）：

1. `topology.invocation_chains` —— `id, tenant_id, chain_key, plan_id(→routing_plans), mode CHECK(='plan_only'), chain_json jsonb, chain_checksum, planner_version, trace_id, created_at`；`UNIQUE(tenant_id, chain_key)`
2. `topology.invocation_chain_nodes` —— `id, tenant_id, chain_id, plan_node_slot_key, ordinal, role CHECK('primary'|'fallback'), input_bindings jsonb, expected_output jsonb`；`UNIQUE(tenant_id, chain_id, plan_node_slot_key, role, ordinal)`（同一槽位可最多 8 个 fallback 序）
3. `topology.invocation_intents` —— `id, tenant_id, chain_id, plan_node_slot_key, role, capability, intent_json, policy_decision CHECK('allowed'|'requires_approval'|'denied'), approval_ref, status CHECK(四态投影枚举), idempotency_key, trace_id, created_at`；`UNIQUE(tenant_id, chain_id, plan_node_slot_key, role, policy_decision, status)`

禁止创建：`command` 字段、密钥字段、真实执行队列、Manifest 绑定写入（沿用 0037 `manifest_bindings` 默认空）。

种子（`ON CONFLICT DO NOTHING` 防重跑）：一条真实 plan（由既有 seed 蓝图 + `ledger-quality-slot → research-note-slot` bridge 边组成，plan\_json/checksum 用服务同款 hashlib+sort\_keys 公式内联计算并注释为规范口径）→ 由该 plan 物化的链（含桥接端口接线）+ 逐节点 intents（能力 read\_only → `policy_allowed`）；`policy.policy_sets` upsert：追加 `topology.chain.read`/`topology.intent.read`（read\_only）与 `topology.chain.write`（low）规则。

## 4. 服务层模块

```
packages/plugin_topology/
├── chain.py        # ChainResolver（确定性链序+端口接线+链 checksum）+ IntentGenerator（状态投影+Policy 决策动作）
└── adapters.py     # 三份只读 Adapter 契约模板（序列化视图，绝不产生运行时调用）
```

- **ChainResolver**：`materialize(plan, catalog)` → Kahn 拓扑排序（slot\_key 字典序 tie-break）保证确定性；主选 + 至多 8 个有序 fallback（契约兼容、风险不高于原节点）展开进 `chain_order`；对 `data_flow`/`depends_on`/`bridge` 边做端口接线（output 契约 ↔ input 契约，版本+格式一致；`bridge` 只允许 4 种公共 Ref）；`chain_checksum = sha256(json.dumps(chain_json, sort_keys=True))`；发现环抛 `PlannerCycleError`（复用）；`mode` 恒 `plan_only`。

- **IntentGenerator**：逐节点生成 `InvocationIntent`（expected\_inputs 引用链上前序产出 Ref、隔离边界 `isolated_subprocess`、副作用 `read_only` 投影、幂等键、trace）；经本模块 Policy Port 作裁决定序 `planned → materialized → policy_allowed | requires_approval | denied`；`approval_ref` 留痕；全部只落 `invocation_intents` 行，不触达任何运行时。

- **adapters.py**：输出三份只读视图（模板 JSON，全部 `additionalProperties=false`）：

  - `PluginRuntimeAdapterContract`：future 调用签名投影（capability 契约版本、输入物化清单仅 Ref、期望输出契约、隔离/幂等语义）；

  - `PolicyDecisionAdapterContract`：intent → `policy_decision/risk_class/requires_approval/decision_ref`；

  - `AgentAdapterContract`：approved 后的任务装配说明、审批触发点、回滚协调引用（全部 projected，禁止创建任务）。

## 5. API 与桌面只读 GUI

- API（`apps/api/main.py`，全部经 Policy 网关 + 租户 + Trace）：

  - `POST /api/v1/topology/chains/materialize`（idempotent，`topology.chain.write`/low）→ 物化并返回链；重复幂等键返回既有链

  - `GET /api/v1/topology/chains`（`topology.chain.read`/read\_only）

  - `GET /api/v1/topology/chains/{chain_key}/intents`（`topology.intent.read`/read\_only）

  - Electron IPC 白名单追加两个只读端点；materialize 不进白名单（桌面只读）。

- GUI（`desktop/src/App.tsx`，复用 M2 拓扑卡片模式）：新增「调用链与调用意向（只读）」卡片——链表（chain\_key/plan\_key/checksum/链序摘要）、端口接线表、意图表（decision 标签 ALLOW / REQUIRE / DENY 着色）、只读 Adapter 契约 JSON 面板；横幅「规划，不执行 · 仅状态投影」。显式说明：materialize 只在控制平面 API 发生，桌面端只读展示。

## 6. 测试计划

| 层   | 用例                                                                                                                                         |
| --- | ------------------------------------------------------------------------------------------------------------------------------------------ |
| 契约  | 3 份新 Schema 正反例（execute/auto、intent 携带 command、绑定带证据正文、缺幂等键拒绝）                                                                             |
| 单元  | 确定性（同输入+同锁版本 → 同链序+同 checksum）、tie-break 稳定、环拒绝、fallback 展开序、端口接线版本/格式失配拒绝、bridge 端口只允许公共 Ref、IntentGenerator 四态流转、Adapter 序列化 schema 稳定   |
| 集成  | materialize API：幂等重复返回既有链；Policy 门禁（chain.read/intent.read 403、chain.write 无权限 403）；RLS 跨租户不可见；种子链可被服务重新物化且 chain\_key/checksum 一致（发现种子漂移） |
| E2E | 意图 → plan → materialize 链 → intents 决策（ALLOW/REQUIRE\_APPROVAL）→ GUI 只读展示 + 横幅                                                             |
| GUI | Vitest（新组件渲染真实 API mock）+ 桌面 typecheck                                                                                                     |

回归：`python -m pytest -q`、`python -m ruff check .`、`python -m mypy packages`、`npm run typecheck`。

## 7. 验收对照（M3 六条）

1. 链与意图恒 `plan_only`，契约拒绝任何 `execute`/`auto`/command 字段。
2. 同 plan + 同锁版本 → 同链序 + 同 chain\_checksum（确定性复现；种子链与服务重物化一致）。
3. 链序为无环拓扑序；拓扑环 / 契约失配 / 公共 Ref 外读取全部被拒绝。
4. 端口接线只引用契约与公共 Ref，任何载荷不得携带证据正文或领域私有数据。
5. Intent 决策全部经 Policy 并留痕（ALLOW/REQUIRE\_APPROVAL/DENIED，approval\_ref），仅状态投影，不触达运行时。
6. GUI 只读展示链/意图/Adapter 契约，显式「规划，不执行 · 仅状态投影」；materialize 仅在控制平面且幂等。

完成后：全量回归、更新 `docs/status.md` 与 `plugins/README.md`。
