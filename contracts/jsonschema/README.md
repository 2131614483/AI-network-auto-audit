# Phase 0.2 核心契约

这些 JSON Schema 是控制平面、领域服务、插件和 GUI 之间的唯一数据契约，使用 JSON Schema Draft 2020-12，和 React/Vue/Python/Node 解耦。它们只描述边界消息与声明，不依赖数据库表，也不授予任何执行权限。

## 文件分组

| 文件 | 用途 |
| --- | --- |
| `artifact-ref.schema.json` | 原始文件、底稿、输出和证据的不可变引用 |
| `mission-spec.schema.json` | 长期任务、预算、验收条件和 AUTO 模式 |
| `plugin-manifest.schema.json` | 插件版本、能力、资源、权限、副作用和 GUI 注册 |
| `unified-plugin-protocol.schema.json` | 跨业务、跨语言的插件能力/接口/治理协议；不含执行绑定 |
| `plugin-cluster.schema.json` | L0 插件集群、调度域、跨图桥接边界和路由预算 |
| `plugin-blueprint.schema.json` | 不可执行的 L1/L2 插件槽位、能力和契约依赖声明 |
| `plugin-topology-release.schema.json` | 集群/蓝图目录的版本、校验和与可复现发布锁 |
| `plugin-routing-plan.schema.json` | 不可执行的插件候选路由计划（只允许 `plan_only`） |
| `policy-decision.schema.json` | Policy Gateway 的 ALLOW/DENY/REQUIRE_APPROVAL/FREEZE 裁决 |
| `policy-rule.schema.json` | 白名单、黑名单、审批和冻结规则 |
| `graph-query-budget.schema.json` | 图谱检索的节点、边、跳数、图数和延迟上限 |
| `knowledge-changeset.schema.json` | 摄入、冲突解决、人工编辑和 Agent 提案的知识变更集 |
| `workflow.schema.json` | 版本化、可补偿、幂等的类型化 DAG |
| `ui-contribution.schema.json` | 框架无关的导航、视图、查询、动作和设置声明 |
| `ui-action.schema.json` | GUI 动作的 capability 与类型化参数契约 |
| `ui-query.schema.json` | GUI 数据查询和响应契约 |
| `ui-slot.schema.json` | 可扩展的稳定插槽白名单 |
| `renderer-descriptor.schema.json` | 核心或隔离扩展渲染器声明 |

## 兼容性规则

1. `schema_version` 和插件/工作流 `version` 使用 SemVer；主框架至少支持当前大版本和前一个大版本。
2. 消费者必须忽略未知可选字段；生产者不得删除或改变已发布字段语义。
3. 不兼容变更必须升主版本并提供迁移器；已发布插件版本不可原地覆盖。
4. GUI 动作只是请求，不是授权。所有动作仍须携带 capability、规范化参数哈希，并经过 Policy Gateway、审批和审计链。
5. Schema 校验通过不代表允许执行；权限、租户隔离、资源上限、证据保存和黑名单规则仍由运行时强制执行。
6. `$ref` 采用同目录相对路径，构建工具应按 URI 基址解析，不应把 Schema 当作数据库模型自动生成表。
7. `UnifiedPluginProtocol` 只用于控制面兼容与规划；它不含入口点、命令、URL 或密钥。实际实现必须在后续阶段通过独立 Manifest、签名、隔离和 Policy Gateway 绑定。

## 本地快速校验

在项目根目录执行：

```powershell
Get-ChildItem contracts/jsonschema -Filter *.json | ForEach-Object {
  Get-Content -Raw $_.FullName | ConvertFrom-Json | Out-Null
  Write-Output "OK $($_.Name)"
}
```

这一步只验证 JSON 语法。Phase 0.2 的契约测试还需要使用支持 Draft 2020-12 的校验器，覆盖每个 Schema 的有效样例、缺失必填字段、越界预算、非法能力名、未知 GUI 插槽和禁止副作用组合。
