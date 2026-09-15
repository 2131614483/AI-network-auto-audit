# Phase 9：AIOps 工作台治理

## 目的

把既有本地 AIOps 模拟链路以可审计、可人工确认的方式呈现在桌面工作台中：
`告警 → 事故 → 修复提案 → 变更授权 → 本地模拟 Canary → 人工核验/回滚`。

本阶段的“执行”始终是数据库内的模拟记录；系统不连接基础设施、不调用 Shell、网络、容器、插件或生产 Playbook。桌面端不提供创建提案、批准变更或启动执行的入口，只提供受策略网关控制的读取和对既有 Canary 模拟结果的人工核验。

## 范围

### 本阶段只做

- 事故列表：在租户 RLS 范围内显示事故状态、关联告警数量、提案数、变更申请数和模拟执行数。
- 单事故血缘：返回告警、提案、变更申请、模拟执行与不可变核验记录；不返回可执行命令或密钥。
- 人工 Canary 核验：仅对已经 `completed` 的 `canary` 模拟记录写入一次 `healthy` 或 `rollback` 结果。回滚会打开对应提案的本地熔断状态，但不会调用任何外部回滚动作。
- 显式迁移 `0036_aiops_execution_verifications`：核验作为独立、保留的账本记录，绑定租户、Trace、幂等键和评审标签。
- 策略白名单：`aiops.chain.read`（只读）与 `aiops.canary.verify`（medium / write_data）两项；每个 API 请求携带租户、Trace，写请求要求幂等键。
- 桌面 AIOps 工作台：事故列表、血缘面板、模拟状态说明，以及经过二次确认的“核验通过”/“记录模拟回滚”动作。

### 本阶段明确不做

- 不暴露提案创建、变更批准、Canary 启动或任何 `live` 模式的 API/GUI。
- 不执行任意命令，不接入 SSH、Kubernetes、监控平台、网络、真实环境或自动修复。
- 不自动核验，不批量核验，不删除告警、事故、提案、执行或历史记录。
- 不扩大 AUTO 策略；只允许上述两项精确 capability，未登记能力默认拒绝。

## 数据契约

- `GET /api/v1/aiops/incidents?limit=50`：事故摘要；`limit` 仅可为 1–500。
- `GET /api/v1/aiops/incidents/{incident_id}/lineage`：事故的全链路只读视图，响应恒带 `simulated_only: true`。
- `POST /api/v1/aiops/executions/{execution_id}/verify`：请求体为 `{ "outcome": "healthy" | "rollback", "reviewer_label": "..." }`，要求 `Idempotency-Key`。同一结果重放返回既有核验；结果相反、非 Canary、非 completed 或未知执行一律拒绝。
- `aiops.execution_verifications`：一条执行最多一个核验账本条目；记录 `outcome`、`reviewer_label`、`trace_id`、`idempotency_key`、时间。历史不硬删除。

## 验收清单

1. 服务层只返回租户内事故与血缘；未知 ID、越界 limit、非 Canary/非 completed 核验均拒绝。
2. 核验 `healthy` 和 `rollback` 均写入不可变账本；重复同结果可安全重放，相反结果 fail-closed；rollback 把提案置为 `circuit_open`。
3. API 两类读取、写入均经过 Policy Gateway；未发布能力拒绝，写入具租户、Trace、幂等键。
4. 策略注册仅发布 `aiops.chain.read`、`aiops.canary.verify`，没有 live、execute、network 或 arbitrary-command capability。
5. Electron 仅增加两个精确只读路径和一个 UUID 限定的核验 POST 路径；工作台文案明确“仅本地模拟”。
6. Golden/集成数据只写 `audit_network_test`；显式迁移后主库与测试库迁移头一致。

