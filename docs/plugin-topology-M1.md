# Plugin Topology M1 实施计划

> 状态：已批准，2026-09-07 开始实施（契约先行）。
> 关联：`docs/审计组网插件规划-v1.md`、`docs/plugin-topology-orchestration.md`、`AGENTS.md`。
> M1 目标：独立 Plugin Topology 模块——集群/蓝图/成员/边/契约登记、版本化草稿/发布/回收站、纯 `plan_only` 路由计划算法。**不做** Manifest 安装、Agent 任务、任何执行按钮。

## 1. 范围

| 做 | 不做 |
| --- | --- |
| 新 `topology` schema 与 17 张表（版本化/回收站） | Manifest 安装、真实插件启动 |
| 集群/蓝图/成员/边/契约登记与版本化发布 | Agent 任务、执行按钮 |
| 纯 `plan_only` 路由计划算法（收敛→兼容→DAG→预算） | 任何 `execute`/`auto` 路径（契约强制拒绝） |
| 跨图受限桥接登记、配置三级继承、健康快照 Ref | 高频心跳写入、领域私有表读取 |

## 2. 契约先行（先于服务逻辑）

复用 M0 的 4 份 Schema（`contracts/jsonschema/`）：`plugin-cluster`、`plugin-blueprint`、`plugin-topology-release`、`plugin-routing-plan`。

新增 4 份服务契约（同一目录，M0 内联样例测试风格）：

| Schema | 内容 | 关键约束 |
| --- | --- | --- |
| `topology-upsert.schema.json` | 集群/蓝图/成员/边/契约登记与更新载荷（`kind` 判别） | 必须带幂等键；蓝图无 runtime/entrypoint/package/permissions |
| `topology-release.schema.json` | 发布/回滚载荷 | `catalog_checksum` 为冻结目录 SHA256，发布后不可原地覆盖 |
| `topology-recycle.schema.json` | 回收/恢复载荷 | `recycle_type` ∈ recycled/restored；禁止硬删除 |
| `topology-plan.schema.json` | plan 服务请求载荷 | 输出引用 `plugin-routing-plan`；`mode` 恒 `plan_only` |

契约测试：4 份 Schema 合法 + 正例通过 + 反例拒绝（`execute`/缺失幂等键/蓝图携带执行字段等）。

## 3. 表结构

新 schema `topology`，17 张表（DDL 见迁移 0037 正文）：

1. `plugin_clusters` / `cluster_versions` —— L0 集群与已发布版本
2. `plugin_blueprints` / `blueprint_versions` —— L1/L2 槽位与版本（无 runtime/entrypoint）
3. `cluster_memberships` —— 蓝图在 business/capability/resource/governance 多轴集群中的成员关系
4. `interface_contracts` / `compatibility_results` —— 输入输出契约与兼容结果
5. `topology_edges` / `domain_bridges` —— 类型化边（depends_on/data_flow/fallback/bridge）与受限桥接（仅 4 种公共 Ref）
6. `routing_plans` / `routing_plan_nodes` / `routing_plan_edges` —— 只读、可重建的 plan-only 结果
7. `manifest_bindings` —— 已批准蓝图到真实 Manifest 版本绑定（M1 默认为空）
8. `configuration_versions` / `runtime_overrides` —— 三级配置（蓝图默认 → 用户自定义 → TTL 运行时覆盖，仅影响允许字段）
9. `health_snapshot_refs` —— 健康快照引用，不接收高频心跳写入
10. `topology_releases` / `topology_recycle_bin` —— 发布/回滚/软删除/恢复

禁止创建：`plugin_code`、任意命令字段、密钥字段、领域证据正文、直接执行队列。

## 4. 迁移步骤

- 文件：`migrations/versions/0037_plugin_topology.py`
- `revision = "0037_plugin_topology"`（20 字符 < 32 上限），`down_revision = "0036_aiops_exec_verification"`
- `upgrade()` 内：建 schema → 17 张表（IF NOT EXISTS）→ 每表 RLS ENABLE + FORCE + 租户策略 + `GRANT SELECT, INSERT, UPDATE TO audit_app`（`topology_recycle_bin` 额外 GRANT DELETE）→ 索引 → local-dev 种子（4 类集群、2–3 个 planned 蓝图、已验收契约、一条 published release）→ `policy.policy_sets` upsert `local-plugin-topology-read`（3 条 allow）
- `downgrade()` 抛 RuntimeError（证据不硬删）
- 显式执行：`python -m alembic upgrade head`（迁移账号），禁止启动自动升级

## 5. 服务层模块

```
packages/plugin_topology/
├── contracts.py     # 服务载荷模型（Pydantic，extra=forbid）
├── service.py       # 登记/发布/回收/恢复/查询（经 Policy + 租户 + Trace + 幂等键）
├── resolver.py      # 契约兼容匹配 → compatibility_results
├── planner.py       # 纯计划算法（集群收敛 → 契约兼容 → DAG 无环 → 预算）
└── recycle.py       # 回收/恢复（快照入 recycle_bin，恢复校验 restored_from）
```

planner 算法：意图 → L2 契约需求 → 四轴收敛（禁止全目录遍历）→ 契约兼容 → `topology_edges` 组装 DAG + DFS 环检测 → 预算截断 → 锁定 release/planner 版本/健康 Ref → 落 `routing_plans/nodes/edges`，`mode` 恒 `plan_only`。

## 6. 测试计划

| 层 | 用例 |
| --- | --- |
| 契约 | 4 份 M0 + 4 份新 Schema 正反例 |
| 单元 | 收敛交集、兼容拒绝名称猜测、DAG 无环、预算截断、plan_only 拒绝 execute、发布不可覆盖、回收→恢复 |
| 集成 | 独立测试库：登记→发布→plan→Policy ALLOW/REQUIRE_APPROVAL→幂等→RLS 跨租户不可见 |
| E2E | 意图→收敛→候选链→plan_only→策略门禁→审计留痕 |

回归：`python -m pytest -q`、`python -m ruff check .`、`python -m mypy packages`、`npm run typecheck`。

## 7. 验收对照（M1 八条）

1. plan 仅 `plan_only`，契约拒绝 `execute`；2. 蓝图无运行时/入口点字段；3. 计划不超过集群预算且 DAG 无环；4. 跨图仅 4 种公共 Ref；5. 图谱/计划/发布/回收均有版本/trace/操作者；6. GUI 显式"规划，不执行"；7. 计划锁定 release SHA + planner 版本 + 健康 Ref + trace 可复现；8. 单次计划禁止循环。

完成后：全量回归、更新 `docs/status.md` 与 `plugins/README.md`。
