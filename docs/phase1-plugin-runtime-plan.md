# Phase 1：P1 插件运行时

状态：**验收通过**（2026-09-04）。本阶段由用户于 2026-09-04 明确切换；Phase 0 的统一插件协议与真实数据兼容验收是前置条件。后续功能仍须由用户明确切换到下一阶段后再开始。

## 目标

把 UPP `contract_only` 协议包安全提升为一个可验证的**本地只读**运行样例。首个样例是 `knowledge.document-ingestion`：接收不可变工件引用，读取受声明目录内的 Markdown/TXT/JSON/CSV，输出带 SHA256、媒体类型和来源定位的结构化文档内容。

## 本阶段边界

1. 运行器只接受代码内白名单中的已验证绑定，不能从 Manifest、文件名或请求参数拼接命令或任意 Python 入口点。
2. 每次调用都要具备 tenant、trace、幂等键，并先通过 Policy Gateway 的 `knowledge.extract.document` 裁决。
3. 执行器必须使用子进程 JSON 信封；子进程不持有数据库凭据、网络权限或父进程对象。
4. 输入工件 URI 只允许落在声明的本地只读根目录；路径需规范化后再检查，拒绝越界、目录、符号链接逃逸和超出大小预算的文件。
5. 输出只写回调用方内存中的结构化结果；首个样例不写数据库、不注册外部服务、不调用 MinerU、OCR、LLM、交易或 AIOps 修复。
6. 执行日志只保存摘要、哈希、Trace 和策略结论，不复制原文到控制面日志。

## 交付物与验收

| 交付物 | 验收 |
| --- | --- |
| UPP → Manifest 的绑定契约 | 不匹配 ID/版本/校验和、未声明能力或篡改协议均被拒绝 |
| 隔离运行器 | 任意入口点、网络、越界路径、未知能力和非 JSON 信封均被拒绝 |
| 首个内置实现 | 在 `G:\数据\README-清单.md` 或等价临时夹具上完成一次真实只读调用 |
| Policy/Trace/幂等 | DENY 不启动子进程；ALLOW 的结果有 trace、输入哈希与可复现输出 |
| GUI/API | 只展示已验证的只读状态和执行记录；不存在“自动执行”按钮 |
| 回归 | Unit、契约、真实 PG/文件集成测试、失败隔离与桌面类型检查通过 |

## 本轮验收证据

- 已实现 `knowledge.document-ingestion@0.1.0`：固定白名单绑定、Manifest/UPP SHA256 校验、指定目录内的 Markdown/TXT/JSON/CSV 只读解析、50 MB 输入上限、SHA256 与大小双校验、UTF-8 输出信封。
- `POST /api/v1/plugins/invoke` 的顺序固定为：租户校验 → 已验证绑定 → 显式目录发布 → 持久化策略裁决 → 隔离子进程。拒绝、未登记、越界、篡改哈希和未知能力均不会得到文档输出。
- 使用 `scripts/register-phase1-plugin.ps1` 显式登记；仅在传入 `-EnableReadOnlyPolicy` 时，才发布精确的 `knowledge.extract.document` 只读白名单。它不启用广泛 AUTO，也不授予写入、网络或安装权限。
- 本机 API 使用 `G:\数据\README-清单.md` 完成真实只读调用：返回 SHA256 与源文件一致、运行时代码哈希为 64 位、策略决策和 Trace 已持久化。原始 G 盘文件未修改。
- 桌面端通过 `/api/v1/plugins/verified` 只展示已验证运行时及最近策略记录；`phase1-plugin-runtime-20260904.png` 已核验没有直接执行按钮。
- 回归结果：Python `86 passed`；Ruff、Mypy、桌面端 TypeScript typecheck、Vitest（5 项）和生产构建均通过。

## 不属于本阶段

MinerU/OCR 实际接入、PDF/图片/音视频解析、插件安装市场、第三方容器、网络连接器、写数据能力、任务长期调度、真实交易和 AIOps 自动修复均留在后续独立阶段。
