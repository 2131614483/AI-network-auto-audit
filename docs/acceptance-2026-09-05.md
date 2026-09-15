# 2026-09-05 桌面控制台复验记录

## 结论

**本次 Phase 5--9 增量复验通过；总体设计中的“长期无人值守超级中枢”整体验收仍为部分通过。**

本记录只确认当前本机 `local-dev` 租户、PostgreSQL 16 与 Electron 生产构建的已实现能力。它不把模拟数据、默认跳过的外部运行时测试或尚未实施的生产运行保障误报为已交付能力。

## 本轮发现与整改

| 发现 | 整改与复验 |
| --- | --- |
| 图谱页面加载提案列表时，主租户尚未发布 `knowledge.extract.graph` 的精确规则，导致图谱治理加载失败。 | 已通过既有 `publish_graph_extraction_allow_policy` 为 `local-dev` 发布仅限 `knowledge.extract.graph` 的 `read_only` / `low` 阴影 ChangeSet 规则；未开放活图直写、AUTO、外网或任意命令。`GET /api/v1/graph/extractions/proposals` 复验返回 50 条提案。 |
| 桌面端会把策略拒绝的完整内部裁决 JSON 直接显示在页面。 | Electron 主进程新增受控错误归一化：策略拒绝显示可操作的中文提示，其他结构化错误不再序列化倾倒。新增桌面壳回归断言。 |
| 自动截图默认等待时间不足，可能在数据尚未加载时形成假阴性。 | 截图专用环境变量 `AUDIT_NETWORK_CAPTURE_DELAY_MS` 已限定为 0.5--30 秒；不改变正常桌面启动行为。生产构建逐页等待 15 秒后复验。 |

## 环境与自动化检查

| 项目 | 实测结果 |
| --- | --- |
| 主库与测试库迁移 | 均为 `0036_aiops_exec_verification` |
| 主库运行时 | PostgreSQL 16.13；`vector 0.8.0`、`pgcrypto`、`pg_trgm`、`ltree` 已启用 |
| 权限 | `audit_app`、`audit_migrator` 均无 `BYPASSRLS`；AIOps 核验账本已启用并强制 RLS |
| Python 回归 | 158 passed，2 skipped |
| 静态检查 | Ruff 通过；Mypy（31 个 packages 源）通过 |
| 桌面检查 | TypeScript 通过；Vitest 5/5；生产构建通过 |

两个 skipped 是默认关闭的真实 MinerU 集成与队列集成测试；本轮未将它们计入通过项。已有的 Phase 3 实测证据仍保留在状态文档中。

## 生产控制平面与视觉复验

`http://127.0.0.1:8010` 的 `local-dev` 实例返回健康状态，且所有下列读取都经租户与策略网关：

| 工作台 | API/数据实证 | Electron 生产版视觉结果 |
| --- | --- | --- |
| 知识库 | 159 文档、555 知识块、28 向量；混合检索返回 5 个非降级命中 | 文档导入、文件夹入口、本地解析队列、回收站、适配器和检索标签可用；见 `.data/acceptance-knowledge-ready-20260905.png` |
| 多级图谱 | 治理目录 220 图空间；当前图空间返回 2 节点、1 关系；抽取提案列表 50 条 | L0--L4 选择器、关系画布、受预算提示与节点检查器可用；见 `.data/acceptance-graph-ready-20260905.png` |
| 审计 | 50 项目；选中项目返回证据、异常候选和确认发现血缘 | 审计项目表与证据链入口正常渲染；见 `.data/acceptance-audit-ready-20260905.png` |
| 量化 | 50 模拟回测；数据快照、代码 SHA、点时门和 `simulated_only` 血缘可读 | 回测列表、指标与仅模拟边界正常渲染；见 `.data/acceptance-quant-ready-20260905.png` |
| AIOps | 50 事故、160 修复提案、157 模拟执行；单事故血缘含告警、提案、变更、模拟执行与核验 | 事故列表与人工 Canary 核验边界正常渲染，页面明确不连接基础设施、不提供 live 模式；见 `.data/acceptance-aiops-ready-20260905.png` |

所有工作台共用受限的 `.content` 滚动表面；桌面壳回归覆盖十个视图和滚轮/越界滚动约束。

## 未包含在“通过”结论中的长期目标

- 尚无持续 24×7 运行证据、真实基础设施 Playbook、Temporal/NATS、故障注入或 RPO/RTO 演练。
- 量化仍为本地数据的模拟回测，不存在真实下单或交易权限。
- AIOps 仅记录本地模拟执行和人工 Canary 核验，不接入生产基础设施。
- 自动化测试本轮未实际运行 MinerU；真实 PDF/图片处理应在用户提供的受控样本上按 Phase 3 的 opt-in 流程单独复验。
- 总体设计要求的五域可重复 GUI/API 演示包、长期可观测性和灾备验收仍需作为后续独立交付，而不能由本次页面与回归测试替代。
