# 审计 AIOps 智能中枢

> **一个可靠中枢 + 专业领域脑 + 一个确定性风控内核 + 一套可插拔的插件网络**
>
> 把"AI 会胡说、会越权、结果不可信"三个致命问题，收敛成一条**可授权、可追溯、可复算、可回滚**的执行链路：AI 只负责"提出方案"，确定性编译器与策略网关负责"审核方案"，隔离沙箱负责"执行方案"，证据链负责"证明结果"。

![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)
![PostgreSQL 16](https://img.shields.io/badge/PostgreSQL-16-4169E1?logo=postgresql&logoColor=white)
![pgvector](https://img.shields.io/badge/pgvector-0.8-00A3E0)
![FastAPI](https://img.shields.io/badge/FastAPI-API-009688?logo=fastapi)
![Electron 37](https://img.shields.io/badge/Electron-37-47848F?logo=electron&logoColor=white)
![React 19](https://img.shields.io/badge/React-19-61DAFB?logo=react&logoColor=black)
![TypeScript strict](https://img.shields.io/badge/TypeScript-strict-3178C6?logo=typescript&logoColor=white)
![tests: 2230+](https://img.shields.io/badge/tests-2230%2B-green)

---

## 目录

- [项目介绍](#项目介绍)
- [展示视频](#展示视频)
- [核心命题：AI 落地审计场景的三个痛点](#核心命题ai-落地审计场景的三个痛点)
- [总体架构](#总体架构)
- [AI 组网：六道闸门，模型永远不能自授权](#ai-组网六道闸门模型永远不能自授权)
- [关键设计](#关键设计)
- [本轮亮点：七环全通 · 图谱证据化 · 连通性接线](#本轮亮点七环全通--图谱证据化--连通性接线)
- [真实端到端案例](#真实端到端案例)
- [可视化界面](#可视化界面)
- [技术栈](#技术栈)
- [快速开始](#快速开始)
- [测试与质量门](#测试与质量门)
- [边界与诚实清单](#边界与诚实清单)
- [常见故障](#常见故障)
- [相关文档](#相关文档)

---

## 项目介绍

本项目是**本机私有化部署**的审计 / AIOps 智能中枢，交付形态是"控制平面 + 桌面驾驶舱"，既非 SaaS，也不是 Demo 动画。它围绕一条主线构建：**AI 落地审计与财务场景时，过程必须能被复核。**

- **策略平面独立于 LLM 的意愿**：能不能做某个动作，由确定性策略网关裁决，而不是由模型"自觉"。
- **AI 只能填槽位，不能做决定**：模型不能发明能力名、端口或数据源，三道闸门（能力白名单 / 端口契约 / 数据边界）把模型关进一个可测试的盒子。
- **结论可自证**：每个产物锁定 `sha256`，证据包可**离线独立复算**——不复用项目任何代码也能重算出一致的结论。

项目代号 `audit_network`，自 2026-09 起逐阶段实施，已通过 **Phase 0–9、CW0–CW7 与插件拓扑 M1–M10 全部验收里程碑**。最近一轮（2026-09-23）完成了进化闭环七环全通、图谱证据化和连通性接线。

## 展示视频





— 25 个真实界面镜头，中文旁白 + 字幕，4 分 11 秒。

分镜文案见 [docs/showcase-video/storyboard-v2.md](docs/showcase-video/storyboard-v2.md)。

## 核心命题：AI 落地审计场景的三个痛点

| 痛点     | 传统做法的问题     | 本项目做法                                         |
| ------ | ----------- | --------------------------------------------- |
| AI 会胡说 | 让模型直接给结论    | 模型只能**填槽位**，不能发明能力名、端口、数据源；草稿必须通过确定性编译器       |
| AI 会越权 | 靠 prompt 约束 | 策略平面独立于 LLM，`deny-first` + 未知能力直接拒绝，拿到不裁决就不执行 |
| 结果不可信  | 只输出一份报告     | 每个产物锁定 `sha256`，证据包可**离线独立复算**，可脱离系统重算验证      |

## 总体架构

四类平面必须分开——这是整个项目最重要的一条架构决定：

1. **控制平面**：决定做什么、谁做、何时做（不碰大数据）
2. **数据平面**：插件/Agent 读数据、计算、产出工件
3. **策略平面**：决定某动作能否执行，独立于 LLM 的意愿与 prompt
4. **观测平面**：记录发生了什么、花了多少、如何回放

只要把"策略"从"执行"里切出来，AI 的权限问题就从一个"提示词工程问题"变成一个**可测试的软件问题**。

```mermaid
flowchart TB
    subgraph Control["认知控制平面"]
        A1[目标拆解] --> A2[工作流编译]
        A2 --> A3[持久调度]
        A2 --> A4[共享黑板]
    end

    subgraph Strategy["策略平面（独立于 LLM）"]
        S1[Policy Enforcement Point]
        S2[白名单 / 黑名单 / 权限]
        S3[路径 / 数据分类 / 副作用 / 风险预算]
        S1 --> S2
        S1 --> S3
    end

    Control --> Strategy

    subgraph Brains["业务脑"]
        B1[审计脑]
        B2[AIOps 脑]
        B4[平台治理脑]
    end

    Strategy --> Brains
    Brains --> K1[知识 · 证据 · 信念层<br/>六图分治：claim–evidence 模型]
    K1 --> DB[(PostgreSQL 16 + pgvector<br/>内容寻址工件存储<br/>不可变动作与审批账本)]
```

## AI 组网：六道闸门，模型永远不能自授权

用户输入一句自然语言目标，系统把它变成一张**真正能跑的执行 DAG**。模型在整个链路里只是一个"填槽位的"——不能发明插件名、不能发明端口、不能引用未授权数据源、不能绕过编译器。

```mermaid
flowchart LR
    P1["① 能力召回<br/>只取 DB 蓝图 ∩ 运行时已验证能力"] --> P2["② 模型出草稿<br/>结构化 JSON：nodes / edges / budget"]
    P2 --> P3["③ 草稿闸门 validate_draft<br/>封闭 schema · 能力白名单 · 端口契约 · 数据边界"]
    P3 --> P4["④ 确定性编译器 compile_plan<br/>无环 / 必填端口 / fan-in / 预算 / 适配器"]
    P4 --> P5["⑤ 失败回灌<br/>结构化错误喂回模型重出，上限 3 轮"]
    P5 --> P6["⑥ 诚实收口<br/>仍不合格 → gap_report，绝不伪装成功"]
    P4 -.->|不合格| P2
```

就算 AI 草稿通过全部闸门，真实执行仍要再过策略网关、幂等键防重放、隔离子进程沙箱、证据链落 sha256。

## 关键设计

### 策略平面：让"能不能干"变成可测试的问题

- 能力命名 `<domain>.<object>.<verb>`，风险分档 `read_only / low / medium / high / critical`。
- **deny-first**：未知能力直接 `DENY`，未裁决即拒绝（fail-closed）。
- 决策账本**只 INSERT，无 UPDATE/DELETE**——数据库层面保证审计轨迹不可篡改。

### 插件体系：UPP v1.0 统一插件协议

- 契约先行：86 份版本化 JSON Schema；`additionalProperties: false`，明文禁止 entrypoint、命令行、URL、密钥。
- **123 个插件**按四域组织：审计业务 / 知识工程 / 量化研究 / 智能运维。
- 副作用 5 档，每个能力必须声明幂等依据。

### 隔离执行与证据链

- `python -I` 独立子进程，最小环境白名单，严格超时。
- 每个产物锁 `sha256`，证据 zip 支持**离线复验**。

### 数据库与多租户

- 16 个 schema、113 张表；29 张核心表强制行级安全（RLS）。
- 应用账号无 DELETE、无 DDL、无 BYPASSRLS。

### 知识图谱与经验积累

- **六图分治**，`claim–evidence` 模型；版本化治理。
- 经验权重 = Wilson 置信度 × 时间衰减，**经验只改变概率分布，不改变权限集合**。
- 向量 + 全文 + 图谱混合 RRF 检索。

## 本轮亮点：七环全通 · 图谱证据化 · 连通性接线

### 进化闭环：七环 1→7 全部打通

自我进化的七环——运行发生、事实投影、统计聚合、建议产生、人工决策、知识发布、回流生效——从「1 通 4 半通 2 断」变成**七环全通**。运行事实自动投影为经验证据，人工采纳后固化为已确认关系，下次组网召回时自动作为先验。

### 图谱：147 节点能力网络，关系带文字依据

默认落在内容最多的 `capability-l2` 空间：**147 个节点 / 203 条关系**。节点按层级上色（能力族暖黄 / 能力契约蓝），每条边的权重来自真实证据：

- **经验实测**（66 条）：交接次数 × Wilson 置信度 × 时间衰减
- **契约衔接**（14 条）：两端输出端口 ∩ 输入端口
- **声明层级**（123 条）：`contains` 声明边，权重 1.0

点开节点能看到能力描述、输入输出端口、上下级关系——不再是孤零零的数字。

### 连通性与供需闭合

`GET /api/v1/connectivity/report` 只读端点上线：107 个插件的供需矩阵，35 个完全孤立，每个都给出分类依据（外部输入 / 被调用支撑 / 真缺口）。

## 真实端到端案例

**目标**：让 AI 自己组一条"总账质量质检 + 回测"的链并真实执行。

| 步骤  | 内容                                  | 实测结果                                                 |
| --- | ----------------------------------- | ---------------------------------------------------- |
| 0   | 现场合成 9 行日记账 CSV，刻意植入 6 项质量瑕疵        | sha256 `1f8e4bfd…`，548 B                             |
| 1   | 能力召回（DB 蓝图 ∩ 运行时已验证能力）              | 272 项噪声 → 业务域过滤后 **4 项**可见能力                         |
| 2   | 云端 AI 组网（召回 → 出草稿 → 闸门 → 编译 → 失败回灌） | 经 3 轮修订后 `status=draft_ready`                 |
| 3   | 策略裁决 + 落库（幂等键 / 短事务 / outbox）       | 产出 `run_id / trace_id / execution_hash`              |
| 4   | 端口绑定子进程隔离执行                         | 每个 attempt 有 `input_bindings / output_refs` + sha256 |
| 5–6 | 画布投影 + 证据包导出                        | `evidence/{run_id}.zip`，离线复验 `ok=True`               |
| 7   | 同一 `trace_id` 三层下钻                  | 数据 / 日志 / 代码三层均可追溯                                   |
| 8   | 业务结论                                | 9 行检出 6 项疑点；回测 `recommendation = hold`            |

> 最硬的一条证据：不依赖项目任何代码，独立实现 6 条质检规则重算，得到与插件产物**逐条一致**的 6 项候选。

## 可视化界面

全套界面截图在 [docs/screenshots/desktop/](docs/screenshots/desktop/)。核心姿态：**所有工具调用强制经过策略网关，GUI 不能绕过策略**；每个读取携带租户与 Trace ID，每次写入具备幂等键与 ChangeRequest 审计。

### 中枢与运行

**信息中枢** —— 全站只读快照，断环与缺口如实标红：

<a href="docs/screenshots/desktop/hub.png"><img src="docs/screenshots/desktop/hub.png" width="100%" alt="信息中枢"></a>

**任务编排** —— Mission → Workflow → Task → Agent 四层运行投影：

<a href="docs/screenshots/desktop/operations.png"><img src="docs/screenshots/desktop/operations.png" width="100%" alt="任务编排"></a>

**运行与产物库** —— 126 次运行，证据包分列，孤儿包如实标注：

<a href="docs/screenshots/desktop/runs.png"><img src="docs/screenshots/desktop/runs.png" width="100%" alt="运行与产物库"></a>

**运行诊断** —— 63 次失败归成 15 个错误签名簇：

<a href="docs/screenshots/desktop/diagnose.png"><img src="docs/screenshots/desktop/diagnose.png" width="100%" alt="运行诊断"></a>

### 组网执行

**Run 画布** —— 一次真实组网：100 节点 DAG，节点=真实执行 attempt：

<a href="docs/screenshots/desktop/runcanvas.png"><img src="docs/screenshots/desktop/runcanvas.png" width="100%" alt="Run 画布"></a>

**Run 画布节点检查器** —— 数据从哪来、产出什么、落在哪：

<a href="docs/screenshots/desktop/runcanvas-node.png"><img src="docs/screenshots/desktop/runcanvas-node.png" width="100%" alt="Run 画布节点"></a>

### 图谱与连通性

**能力图谱** —— 147 节点按层级上色：

<a href="docs/screenshots/desktop/graph-tiers.png"><img src="docs/screenshots/desktop/graph-tiers.png" width="100%" alt="能力图谱"></a>

**图谱关系依据** —— 每条关系都有文字依据，不只是权重：

<a href="docs/screenshots/desktop/graph-relations.png"><img src="docs/screenshots/desktop/graph-relations.png" width="100%" alt="图谱关系依据"></a>

**连通性与供需闭合** —— 谁悬空、缺什么上游：

<a href="docs/screenshots/desktop/connectivity.png"><img src="docs/screenshots/desktop/connectivity.png" width="100%" alt="连通性"></a>

### 知识与检索

**知识工厂** —— 入库 / 分块 / 向量化全链路：

<a href="docs/screenshots/desktop/knowledge.png"><img src="docs/screenshots/desktop/knowledge.png" width="100%" alt="知识工厂"></a>

**文档资产库** —— 143 份文档索引，带关联 run 与案例：

<a href="docs/screenshots/desktop/library.png"><img src="docs/screenshots/desktop/library.png" width="100%" alt="文档资产库"></a>

**知识星云** —— 插件按业务阶段组成星系：

<a href="docs/screenshots/desktop/nebula.png"><img src="docs/screenshots/desktop/nebula.png" width="100%" alt="知识星云"></a>

### 插件与策略

**插件拓扑工作台** —— 16 集群 / 10 蓝图 / 10 契约：

<a href="docs/screenshots/desktop/plugins.png"><img src="docs/screenshots/desktop/plugins.png" width="100%" alt="插件拓扑"></a>

**插件清单** —— 123 个插件的端口契约与生命周期：

<a href="docs/screenshots/desktop/plugin-catalog.png"><img src="docs/screenshots/desktop/plugin-catalog.png" width="100%" alt="插件清单"></a>

**策略网关** —— 209 个策略集，182 个疑似残留标红：

<a href="docs/screenshots/desktop/policy.png"><img src="docs/screenshots/desktop/policy.png" width="100%" alt="策略网关"></a>

**审批中心** —— 高风险动作先落审批，账本可查：

<a href="docs/screenshots/desktop/approvals.png"><img src="docs/screenshots/desktop/approvals.png" width="100%" alt="审批中心"></a>

### 业务证据链

**审计工作台** —— 50 项目 / 16 已确认 / 102 待确认：

<a href="docs/screenshots/desktop/audit.png"><img src="docs/screenshots/desktop/audit.png" width="100%" alt="审计工作台"></a>

**审计证据链血缘** —— 证据 → 异常候选 → 已确认发现：

<a href="docs/screenshots/desktop/audit-lineage.png"><img src="docs/screenshots/desktop/audit-lineage.png" width="100%" alt="审计血缘"></a>

**回测工作台** —— 全部 simulated_only，指标可追溯：

<a href="docs/screenshots/desktop/quant.png"><img src="docs/screenshots/desktop/quant.png" width="100%" alt="回测工作台"></a>

**AIOps 工作台** —— 告警 → 提案 → 变更授权 → 模拟执行 → 核验：

<a href="docs/screenshots/desktop/aiops.png"><img src="docs/screenshots/desktop/aiops.png" width="100%" alt="AIOps"></a>

**证据链复验** —— 重算 sha256，不落盘：

<a href="docs/screenshots/desktop/evidence.png"><img src="docs/screenshots/desktop/evidence.png" width="100%" alt="证据链复验"></a>

### 自我进化

**七环闭环** —— 1→7 全部打通：

<a href="docs/screenshots/desktop/evolution.png"><img src="docs/screenshots/desktop/evolution.png" width="100%" alt="七环闭环"></a>

**建议与决策** —— 设计外的真实协作被识别为候选关系：

<a href="docs/screenshots/desktop/suggestions.png"><img src="docs/screenshots/desktop/suggestions.png" width="100%" alt="建议与决策"></a>

### 系统

**健康与自检** —— 每一项都标注数据来源：

<a href="docs/screenshots/desktop/health.png"><img src="docs/screenshots/desktop/health.png" width="100%" alt="健康自检"></a>

## 技术栈

| 层次    | 选型                                                                             |
| ----- | ------------------------------------------------------------------------------ |
| 数据库   | PostgreSQL 16 + `pgcrypto / pg_trgm / ltree / vector`，行级安全多租户             |
| 后端    | Python 3.11 + FastAPI + psycopg + Alembic（0068 个迁移版本）          |
| 桌面端   | Electron 37 + React 19 + Ant Design 5 + Zustand + ECharts + Three.js |
| AI 通道 | 统一网关（Ollama 本地 / OpenAI 兼容云端双协议）                                    |
| 质量工具  | ruff + mypy strict + pytest（2230+ 用例）+ vitest + tsc strict                |

## 快速开始

### 前置要求

| 组件         | 版本                 |
| ---------- | ------------------ |
| Windows    | 10/11              |
| Python     | **3.11+**（推荐 3.12） |
| PostgreSQL | **16+**（实测 16.13） |
| pgvector   | 与 PG 版本匹配          |
| Node.js    | 20+                |

### 一键自举

```powershell
powershell -ExecutionPolicy Bypass -File scripts\bootstrap.ps1 -Check   # 只体检
powershell -ExecutionPolicy Bypass -File scripts\bootstrap.ps1          # 完整自举
```

### 启动桌面中枢

```bat
启动审计智能中枢.bat
```

API 文档：`http://127.0.0.1:8010/docs`。

## 测试与质量门

```powershell
python -m pytest -q
python -m ruff check .
python -m mypy packages
npm run --prefix desktop typecheck
```

最近实测（2026-09-23）：pytest 收集 **2230 个用例**，契约层 **812 passed / 0 failed**，mypy strict Success（138 源文件），桌面 vitest 90 passed。

## 边界与诚实清单

- 不连接真实基础设施、不运行生产 Playbook、不提供 `live` 模式；不创建真实交易。
- 真实执行只保持为**数据库内的状态投影**，不触达主机、网络或基础设施。
- 未完成：LLM 自动抽图、音视频转写、持久工作流引擎、多插件并发隔离调度、生产级密钥管理。
- 界面如实标注所有断点与缺口（向量覆盖率、策略集残留、端点未接线等），不伪装正常。

## 常见故障

- **`psql.exe was not found`**：把 PostgreSQL `bin` 目录加入 `PATH`。
- **迁移报 `permission denied to create extension "vector"`**：需超级用户执行 `scripts/init-native-postgres.sql`。
- **Electron 下载失败**：设置 `ELECTRON_MIRROR` 或从其它机器拷贝 `desktop/node_modules`。
- **启动后 API 报数据库错误**：先执行 `alembic upgrade head`。
- **AI 相关功能被拒绝**：属预期 fail-closed，检查 `.env` 里 `AI_API_KEY`。

## 相关文档

- [docs/status.md](docs/status.md)：当前阶段真实状态与已验证结论
- [docs/showcase-video/storyboard-v2.md](docs/showcase-video/storyboard-v2.md)：展示视频分镜文案
- [docs/进化闭环断点-根因与修复方案-20260923.md](docs/进化闭环断点-根因与修复方案-20260923.md)：七环全通的根因与修复
- [docs/项目说明-审计智能中枢.md](docs/项目说明-审计智能中枢.md)：完整设计文档
