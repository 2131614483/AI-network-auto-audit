# Plugin Topology M2 实施计划

> 状态：已验收，2026-09-07 完成（契约先行）。全量回归：Python **507 passed, 2 skipped**；Ruff / Mypy（packages 38 源）/ 桌面 TypeScript typecheck / Vitest（5/5）/ 生产构建通过；主库迁移头 `0038_topology_bridge_seeds`。
> 关联：`docs/plugin-topology-orchestration.md`、`docs/plugin-topology-M1.md`、`AGENTS.md`。
> M2 目标：补齐跨域桥接服务逻辑，承载预算/循环/跨域桥接系统化测试，并让桌面只读 GUI 消费真实拓扑 API（`/api/v1/topology/*`）。**不做**真实插件运行、外部网络调用与执行入口。

## 1. 范围

| 做 | 不做 |
| --- | --- |
| `domain_bridges` 登记/校验服务逻辑（跨图仅 4 种公共 Ref） | 真实插件运行、外部网络调用 |
| bridge 类型边与公共桥接登记的一致性校验 | 绕过审批的 AUTO 执行、读取领域私有表 |
| 预算/循环/跨域桥接系统化测试（单元 + 集成 + E2E） | 高频心跳写入、Manifest 安装 |
| 桌面插件工作台消费真实拓扑只读 API（集群/蓝图/发布/计划） | 桌面端直接执行或写拓扑 |

## 2. 契约先行

- `topology-upsert.schema.json` 新增 `bridge` kind：
  - payload 必填：`blueprint_key`、`ref_kind`（枚举 `artifact_ref`/`capability_contract`/`released_graph_ref`/`health_signal`）、`bridge_ref`
  - `ref_kind` 只允许 4 种公共 Ref，domain_bridges 表 CHECK 一致（不能携带领域私有表读取语义）
- bridge 类型 `topology_edges`（relation_type=`bridge`）必须引用 source 蓝图已登记的公共桥接，否则拒绝
- 契约测试：bridge 正例通过；`ref_kind` 越界、缺 `bridge_ref`、携带执行字段的合约拒绝

## 3. 服务层

`packages/plugin_topology/service.py`：
- `_CAPABILITY_BY_KIND` 增加 `"bridge": "topology.bridge.write"`
- `_validate_upsert` 校验 bridge payload（ref_kind 枚举、bridge_ref 非空、ref_kind↔bridge_ref 前缀一致性）
- `_apply_upsert` 增加 bridge 分支：写入 `topology.domain_bridges`（`UNIQUE(tenant_id, blueprint_id, ref_kind)` 冲突更新 status=active）
- edge upsert：`relation_type='bridge'` 时校验 source 蓝图已登记对应公共桥接（UNIQUE 存在），否则拒绝

种子（迁移 0037 追加）：`ledger-quality-slot → capability_contract` 一条公开桥接种子；`policy.policy_sets` 增补 `topology.bridge.read`（read_only）策略。

## 4. 测试计划

| 层 | 用例 |
| --- | --- |
| 契约 | bridge 正反例、4 种公共 Ref 枚举、缺 bridge_ref 拒绝 |
| 单元 | bridge 登记校验、bridge 边引用缺失桥接拒绝、预算截断边界、自环/长链环拒绝（已在 M1，补边界） |
| 集成 | 登记 bridge → 发布冻结 → plan 保留桥接边；bridge 类型边缺登记拒绝；公共 Ref 外读取拒绝 |
| E2E | 意图 → 多轴收敛 → 桥接边入链 → plan_only → 策略门禁 → 审计留痕 |

回归：`python -m pytest -q`、`python -m ruff check .`、`python -m mypy packages`、`npm run typecheck`。

## 5. 只读 GUI（桌面插件工作台）

- `desktop/src/App.tsx` 插件拓扑工作台新增「拓扑目录」（真实只读表）：
  - 集群表、蓝图表、发布表（release_key/version/status/catalog_checksum）、计划表（plan_key/mission_key/mode/checksum）
  - 全部经 `window.auditControl.request` 调用 `GET /api/v1/topology/{clusters|blueprints|releases|plans}`（IPC 白名单已声明）
  - 显式横幅「规划，不执行」；展示 Policy/Approval 状态标签（发布状态、plan 恒 plan_only、只读）
- 保留 M0 静态多轴图与意图计划作为参考，新增真实数据并行对照

## 6. 验收对照

1. 跨图桥接只能使用 4 种公共 Ref，不能读取领域私有表（契约 + 服务 + 迁移 CHECK 三重禁止）。
2. 预算不超过集群预算且 DAG 无环（M1 已覆盖，M2 补边界测试）。
3. 计划锁定发布 SHA / planner 版本 / trace 可复现（M1，M2 回归）。
4. GUI 显式「规划，不执行」，并展示真实发布与计划状态。
5. 全部读写通过 Policy 网关，带租户/Trace/幂等键。

完成后：全量回归、更新 `docs/status.md` 与 `plugins/README.md`。