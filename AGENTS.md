# Audit Network 开发约束

## 当前阶段

**Phase 9：AIOps 工作台治理已于 2026-09-04 验收完成。** 用户已在 Phase 5 图谱抽取、Phase 6 合并仲裁、Phase 7 审计证据链和 Phase 8 量化证据链交付后明确切换。本阶段仅使用既有的本机模拟 AIOps 数据，不运行生产修复。未指定下一阶段前，不扩展到新的业务范围。

本阶段只允许：建设告警、事件、修复提案、ChangeRequest、canary 执行与验证/回滚的可追溯读取血缘；为桌面 AIOps 工作台提供受策略控制的查询与人工确认入口；用独立测试数据库合成数据验证策略、幂等、回滚状态和审计记录。

本阶段不得调用外部网络、启动任意命令、修改真实基础设施、自动执行生产修复或开放泛化 AUTO 权限；不得创建真实交易。每个读取必须携带租户和 Trace ID；每次写入都必须具备幂等键、Policy Gateway 裁决与可回溯的 ChangeRequest/执行历史。

## 必须遵守

- 先更新契约和测试，再实现服务逻辑。
- 数据库迁移必须显式执行，不允许应用启动时自动升级；开发优先使用本机原生 PostgreSQL 16 + pgvector。
- 不删除或覆盖用户文件；证据和历史记录不可硬删除。
- 所有请求必须具备租户上下文、trace_id 和幂等键（适用时）。
- 所有工具调用都必须经过 Policy Gateway；GUI 不能绕过策略。
- 生产数据库应用账号不得拥有 BYPASSRLS。
- 每个阶段完成后更新 `docs/status.md` 并执行该阶段全部测试。

## 常用检查

```text
python -m pytest -q
python -m ruff check .
python -m mypy packages
npm run typecheck
```

如果某个工具尚未安装，必须在状态文档中记录，而不是伪造通过。

## 数据库开发方式

- 默认连接本机原生 PostgreSQL 16，使用 `DATABASE_URL` 指定连接，不要求 Docker。
- `docker-compose.yml` 仅作为可选环境适配器，不得成为开发或测试前置条件。
- 原生数据库初始化必须通过可重复执行的 PowerShell/SQL 脚本完成，并验证 PostgreSQL 版本、vector 扩展、应用角色和 RLS 能力。

## 阶段边界

执行 AI 每次只接收一个 Phase 的任务。Phase 9 已完成；测试失败时不得标记阶段完成，不得提前实现后续阶段功能。所有 AIOps API/GUI 操作都必须经 Policy Gateway，带租户、Trace ID 和适用的幂等键；真实执行只能保持为数据库内模拟状态投影，不能触达主机、网络或基础设施。
