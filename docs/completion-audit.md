# 开发完成度审计

审计日期：2026-09-03。结论依据为代码、数据库迁移、真实 PostgreSQL 验证与自动化测试；“有表/有测试”不等于该 Phase 完成。

| 阶段 | 当前结论 | 已验证能力 | 仍未完成的验收项 |
| --- | --- | --- | --- |
| Phase 0 | 完成 | 工程、契约、原生 PG、迁移、质量检查、空库 bootstrap/销毁演练、GitHub CI 门禁 | 远程 CI 尚待首次推送后由 GitHub 实际执行 |
| Phase 1 | 部分完成 | RLS、Outbox/Inbox、策略、审批、授权租约、本机启动、Mission/DAG/TaskRun/AgentRun 最小调度器 | 持久工作流编排、分布式队列与真实插件隔离运行时未完成 |
| Phase 2 | 部分完成 | MD/TXT/CSV/DOCX/XLSX 导入、SHA、版本、文件账本、ChangeSet/Release/回滚、本地 Ollama 1024 维 embedding、全文 GIN/向量 HNSW、GUI 导入与批次进度 | PDF/图片 MinerU/OCR 实际执行、页码级引用和大规模检索 Golden Query 未完成 |
| Phase 3 | 部分完成 | 图空间、跨空间约束、预算局部展开、软删除；桌面端受预算真实拓扑、ECharts 力导向画布、类型图例与节点关系检查器 | L0–L4 路由、Gateway/Bridge 配额、社区/摘要、Golden Query 压测未完成 |
| Phase 4 | 部分完成 | 节点生命周期、回收站、ChangeSet 版本治理 | merge/split、冲突仲裁、并发编辑 UI、蓝绿 embedding 未完成 |
| Phase 5 | 部分完成 | Manifest 校验、声明式 UI 契约、Policy Gateway | SDK、签名/隔离运行时、Capability Resolver、Workflow Compiler、自进化 Agent 未完成 |
| Phase 6 | 部分完成 | CSV 总账、异常候选、原始工件、Evidence→Claim→Finding 链路 | Excel、Hound 代码审计适配、独立 QA 身份与正式报告工作台未完成 |
| Phase 7 | 部分完成 | 点时回测修复、数据/代码 SHA、模拟盘标记 | 聚宽只读适配器、因子/实验血缘、生产一致性与漂移、跨域 RLS 角色隔离未完成 |
| Phase 8 | 部分完成 | 告警收敛、变更单、哈希绑定、canary 验证/回滚、Outbox/Inbox | Temporal/NATS、真实 Playbook 执行、OTel/watchdog、故障注入、14 天运行证明未完成 |
| Phase 9 | 部分完成 | Electron 统一驾驶舱、租户上下文、知识管理/检索、任务/审计/量化/AIOps 运行投影、受策略控制的图谱可视化、插件导航、审批、风险模拟、策略审计表 | 各业务台完整写操作、任务图可视化、生产演练、五条完整 GUI E2E 演示未完成 |

## 本轮修复

1. 将迁移执行改为 `audit_migrator`，不再用应用账户运行 DDL；解决并行开发产生的多 head。
2. 修复量化回测“用当日价格决定当日仓位”的未来函数。
3. AIOps 的 canary/live 执行必须有已批准、未过期且参数哈希一致的 ChangeRequest。
4. 总账审计建立原始 Artifact、Evidence、Claim 和确认 Finding 的可追溯链。
5. Worker 改为本机 Outbox→Inbox 幂等投递；无 Dispatcher 时不再错误确认事件完成。
6. `G:\数据` 以只读 Source 注册；只哈希根目录 `README-清单.md`，禁止递归扫描、复制、解压或执行。
7. 驾驶舱改为 FastAPI 同源托管；界面仅经控制平面调用，所有写入仍走策略网关。
8. 知识导入新增 CSV、DOCX 与 XLSX 的只读文本提取；不执行 Excel 公式、宏或 DOCX 嵌入内容。
9. 策略读取合并同一租户的全部 active policy set，避免高版本的无关规则覆盖审计白名单；deny/freeze 优先级仍由确定性引擎保证。
10. Phase 0 新增独立空库 bootstrap 演练脚本：创建、迁移到 head、校验、断开并删除临时库；审计迁移保持不可破坏性 downgrade 设计。
11. Phase 1 调度器的 task-ready 状态现在在创建与依赖解锁时事务性写入 Outbox；常驻 Worker 可按 Inbox 幂等投递，消费者仍需重新领取任务并经过策略检查。
