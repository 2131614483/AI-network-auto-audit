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
![tests: 1200+](https://img.shields.io/badge/tests-1200%2B-green)

---

## 目录

- [项目介绍](#项目介绍)
- [组网实战：四份实验报告（核心）](#组网实战四份实验报告核心)
- [核心命题：AI 落地审计场景的三个痛点](#核心命题ai-落地审计场景的三个痛点)
- [总体架构](#总体架构)
- [AI 组网：六道闸门，模型永远不能自授权](#ai-组网六道闸门模型永远不能自授权)
- [关键设计](#关键设计)
- [真实端到端案例](#真实端到端案例)
- [可视化界面（精选）](#可视化界面精选)
- [技术栈](#技术栈)
- [目录结构](#目录结构)
- [快速开始](#快速开始)
- [测试与质量门](#测试与质量门)
- [里程碑](#里程碑)
- [边界与诚实清单](#边界与诚实清单)
- [常见故障](#常见故障)
- [相关文档](#相关文档)

---

## 项目介绍

本项目是**本机私有化部署**的审计 / 量化 / AIOps 智能中枢，交付形态是"控制平面 + 桌面驾驶舱"，既非 SaaS，也不是 Demo 动画。它围绕一条主线构建：**AI 落地审计与财务场景时，过程必须能被复核。**

- **策略平面独立于 LLM 的意愿**：能不能做某个动作，由确定性策略网关裁决，而不是由模型"自觉"。
- **AI 只能填槽位，不能做决定**：模型不能发明能力名、端口或数据源，三道闸门（能力白名单 / 端口契约 / 数据边界）把模型关进一个可测试的盒子。
- **结论可自证**：每个产物锁定 `sha256`，证据包可**离线独立复算**——不复用项目任何代码也能重算出一致的结论。

项目代号 `audit_network`，自 2026-09 起逐阶段实施，已通过 **Phase 0–9、CW0–CW7 与插件拓扑 M1–M10 全部验收里程碑**（详见 [里程碑](#里程碑) 与 [docs/status.md](docs/status.md)）。

## 组网实战：四份实验报告（核心）

本项目的核心不是"又一个带界面的工具"，而是 **让 AI 自己把插件组成一条可跑、可复核、可复算的执行 DAG**。以下四份报告按时间顺序记录了组网从设计方向、真实业务实战、独立验证到多源数据复算的完整链路；每份都带 run_id / trace_id / sha256 / 证据包，可离线复验。**这是理解本项目应先读的部分。**

| 日期 | 报告 | 一句话结论 |
| --- | --- | --- |
| 2026-09-08 | [AI 画布组网与全链路日志优化方案](docs/AI画布组网与全链路日志优化方案-20260908.md) | 确立演进方向：AI 选插件与连线 → 确定性编译器检查 → 统一调度执行 → 画布展示与干预 → 独立日志留痕；立项 5 个 P0 基础缺口（逐边数据流、发布快照冻结、短事务、完整日志） |
| 2026-09-09 | [组网业务实战报告：日记账质检 → 回测](docs/组网业务实战报告-20260909.md) | 云端 AI 真实组网跑通：9 行日记账检出 6 项质量候选，送入回测 recommendation=**hold**；证据包离线复验 `ok=True` |
| 2026-09-11 | [AI 组网 Demo 全链路功能验证报告](docs/AI组网Demo-全链路功能验证报告-20260911.md) | 独立验证（源码通读 + 实机重跑 + 独立复算 + A/B/C 对照）：链路真实可跑、业务结果可独立复算；同时定位 P0 路径穿越与悬挂运行态缺陷并逐条修复 |
| 2026-09-14 | [多源业务数据组网 Demo 合格性审计](docs/审计报告-多源业务数据组网Demo.md) | 6 组开源数据集 / 2,775 条记录组成 **21 节点 / 23 边 / 6 层 DAG**；F1–F5 发现均可逐行回溯到行号金额，AAR 恒等式 **0/776 偏差**为整条网提供可信度锚 |

> 最硬的一条：**不依赖项目任何代码，独立实现质检规则重算，得到与插件产物逐条一致的结果**——这就是"组网结论可被独立复算"的含义。四份报告相互独立、结论冲突处以实机证据为准。

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

    subgraph Brains["三脑一核"]
        B1[审计脑]
        B2[量化脑]
        B3[AIOps 脑]
        B4[平台治理脑]
    end

    Strategy --> Brains
    Brains --> K1[知识 · 证据 · 信念层<br/>六图分治：claim–evidence 模型]
    K1 --> DB[(PostgreSQL 16 + pgvector<br/>内容寻址工件存储<br/>不可变动作与审批账本)]
```

**信息防火墙**（对应投行/券商语境下的"信息隔离墙"）：审计客户的非公开信息不得进入交易信号、回测样本或下单链路；跨域只允许传递经脱敏、授权、标记用途的数据产品。这条边界在代码里是硬约束，不是文档口号。

## AI 组网：六道闸门，模型永远不能自授权

用户输入一句自然语言目标（如"对总账做质量校验并出具量化研究结论"），系统把它变成一张**真正能跑的执行 DAG**。模型在整个链路里只是一个"填槽位的"——不能发明插件名、不能发明端口、不能引用未授权数据源、不能绕过编译器。

```mermaid
flowchart LR
    P1["① 能力召回<br/>只取 DB 蓝图 ∩ 运行时已验证能力"] --> P2["② 模型出草稿<br/>结构化 JSON：nodes / edges / budget"]
    P2 --> P3["③ 草稿闸门 validate_draft<br/>封闭 schema · 能力白名单 · 端口契约 · 数据边界"]
    P3 --> P4["④ 确定性编译器 compile_plan<br/>无环 / 必填端口 / fan-in / 预算 / 适配器"]
    P4 --> P5["⑤ 失败回灌<br/>结构化错误喂回模型重出，上限 3 轮"]
    P5 --> P6["⑥ 诚实收口<br/>仍不合格 → gap_report，绝不伪装成功"]
    P4 -.->|不合格| P2
```

**值得强调的设计**：就算 AI 草稿通过全部闸门，真实执行仍要再过策略网关（`require_policy` 默认高风险从严）、幂等键防重放、隔离子进程沙箱、证据链落 sha256。把 LLM 的不可控性关进了**可测试**的盒子里。

## 关键设计

### 策略平面：让"能不能干"变成可测试的问题

- 能力命名规范 `<domain>.<object>.<verb>`（如 `topology.chain.approve`）；风险分档 `read_only / low / medium / high / critical` → `5 / 20 / 50 / 75 / 100` 分。
- **决策顺序（deny-first，短路求值）**：`deny/freeze 规则（永胜） → 显式 deny 通配 → require_approval → allow → AUTO 策略仅放行 low/read_only`；未知能力或未知风险等级 → 直接 `DENY (critical, 100)`。
- `require_policy` 在 API 层调用 **105 次**，默认就是 `risk_class="high"`、`side_effects="write_data"` —— **默认从严，放行必须显式声明**；非 `ALLOW` 时 `REQUIRE_APPROVAL → 409`、其余 `→ 403`，**fail-closed**。
- 账本表 `policy.tool_calls / policy.decisions` **只授 INSERT，无 UPDATE/DELETE**——数据库层面保证审计轨迹不可篡改；幂等键与策略决策绑定，重放参数不一致直接 409。

### 插件体系：UPP v1.0 统一插件协议

- **契约先行**：`contracts/jsonschema/` 下 86 份 Draft 2020-12 Schema；`unified-plugin-protocol.schema.json` 定义 UPP v1.0，强制顶层 14 字段、`additionalProperties: false`，明文**禁止** entrypoint、命令行、URL、密钥出现在协议包里。
- **四层对象分离**：协议包 / Manifest / 蓝图 / 运行时绑定必须分开，能力声明必须**三方一致**且比对三方 `sha256`。
- **副作用只有 5 档**：`none / read_only / write_data / external_action / financial_execution`；每个能力必须声明自己"凭什么幂等"（不可变工件 SHA256 / 策略哈希 / 行情快照哈希）。
- **插件规模**：审计业务网络 **100 个已验收插件**，按三层组织——数据支撑层 18 个 / 核心业务循环层 76 个（覆盖立项→计划→现场实施→风险识别→证据底稿→问题定性→报告输出→整改闭环八阶段）/ 治理优化层 6 个；另有跨域插件（AIOps 7 / 量化 5 / 知识 4 / 文档解析类 7）。

### 隔离执行与证据链：让结果"可以被别人重算"

- 以 `python -I` 启动**独立子进程**（忽略环境变量、不加载用户 site-packages），只透传最小环境与只读根白名单，严格超时，输入输出走 JSON envelope（ASCII 化防 GBK 乱码）。
- 端口绑定执行以 `(node_instance_id, output_port_id)` 为唯一句柄取值，每个节点单独授权，幂等键按节点派生——**重跑不会重复落数**；插件读取时复核上游产物的 `size_bytes` 与 `sha256`，输入不可篡改。
- 运行结束打包证据 zip（`plan.json / attempts.json / edges.json / manifest.json / 各节点产物`），支持**离线复验** `verify_evidence_bundle`。实测对 6 个历史证据包离线校验，`ok=True, checked=5, missing=(), mismatched=()`。

### 数据库与多租户

- **16 个 schema**（`iam / policy / catalog / control / event / artifact / semantic / graph / knowledge / belief / audit / quant / aiops / risk / ops / experience`），合计 **113 张表**。
- **行级安全（RLS）**：29 张核心表 `ENABLE + FORCE ROW LEVEL SECURITY`，`FORCE` 意味着**表属主也绕不过**；全库 33 条 RLS 策略，以 `current_setting('app.tenant_id')` 做租户隔离。
- **分级权限**：迁移角色 `audit_migrator` 与应用角色 `audit_app` 分离；`audit_app` 无 DELETE、无 DDL、**无 BYPASSRLS**。

### 知识图谱与经验积累

- **六图分治**（而非一张无限膨胀的大图），`claim–evidence` 模型承载"主张—证据"；知识生命周期走 `change_sets → validate → approve → release → activate → rollback` 版本化治理。
- **经验动态更新（自研统计层）**：节点/边权重 = `log1p(n) × Wilson 95% 置信度下界`，时间衰减半衰期 90 天，读取层实时计算、不回写行；候选关系 `proposed → accepted/dismissed` 状态机，终态冻结。
- **检索**：向量 + 全文 + 图谱混合 RRF（Reciprocal Rank Fusion）融合排序；多图谱按预算路由，防止海量图检索爆炸。
- **闭环设计**：运行事实自动投影为经验证据（`L0 自动统计 → L1 候选关系 → L2 人工固化`），三级安全分级，**经验只改变概率分布，不改变权限集合**——这是把"经验"回流到 AI 组网的演进主线。

## 真实端到端案例

**目标**：让 AI 自己组一条"总账质量质检 + 量化回测"的链并真实执行（`run 3b6667c3-…`，2026-09-11 云端 AI 真实组网成功——模型自定节点名，2 节点全 succeeded）。

| 步骤  | 内容                                  | 实测结果                                                 |
| --- | ----------------------------------- | ---------------------------------------------------- |
| 0   | 现场合成 9 行日记账 CSV，刻意植入 6 项质量瑕疵        | sha256 `1f8e4bfd…`，548 B                             |
| 1   | 能力召回（DB 蓝图 ∩ 运行时已验证能力）              | 272 项噪声 → 业务域过滤后 **4 项**可见能力                         |
| 2   | 云端 AI 组网（召回 → 出草稿 → 闸门 → 编译 → 失败回灌） | 经 3 轮修订后 `status=draft_ready`，通过编译闸门                 |
| 3   | 策略裁决 + 落库（幂等键 / 短事务 / outbox）       | 产出 `run_id / trace_id / execution_hash`              |
| 4   | 端口绑定子进程隔离执行                         | 每个 attempt 有 `input_bindings / output_refs` + sha256 |
| 5–6 | 画布投影 + 证据包导出                        | `evidence/{run_id}.zip`，离线复验 `ok=True`               |
| 7   | 同一 `trace_id` 三层下钻                  | 数据 / 日志 / 代码三层均可追溯                                   |
| 8   | 业务结论                                | 见下                                                   |

**质检输出**（`audit.ledger.validate`）：总 9 行、捕获 6 项疑点——不平衡凭证（high）、重复行、缺金额、超期、大额×2。**回测输出**（`quant.experiment.evaluate`）：`recommendation = hold`（不做生产变更）、`max_drawdown = -0.05`。

> 最硬的一条证据：不依赖项目任何代码，独立实现 6 条质检规则重算，得到与插件产物**逐条一致**的 6 项候选——这就是"结论可被独立复算"的含义。

## 可视化界面（精选）

全套界面截图统一维护在 [docs/screenshots/](docs/screenshots/)：桌面驾驶舱 13 个工作台视图 + Run 画布过程序列 + 网页版可视化。核心姿态：**所有工具调用强制经过策略网关，GUI 不能绕过策略**；每个读取携带租户与 Trace ID、每次写入具备幂等键与 ChangeRequest 审计。这里只保留三张代表性画面。

**运行总览** —— 以「租户隔离 · 策略优先」为默认姿态的控制平面首页：

<a href="docs/screenshots/desktop/overview.png"><img src="docs/screenshots/desktop/overview.png" width="80%" alt="运行总览"></a>

**审计证据链血缘** —— 点击项目行下钻：证据（类型 / 工件 ID / rows+sha256）→ 异常候选 → 已确认发现，评审人留痕，confirm 为幂等操作：

<a href="docs/screenshots/desktop/audit-lineage.png"><img src="docs/screenshots/desktop/audit-lineage.png" width="80%" alt="审计证据链血缘"></a>

**Run 画布：AI 组网过程** —— 一次组网（凭证穿透 → 底稿编制 → 证据索引 → 问题定性）从"全员待命"到"全链路导通"的动画：播放 / 暂停 / 单步 / 变速，边三态（未导通灰 → 流动金 → 导通青绿虚线，失败红），下钻面板带 sha256。

<a href="docs/screenshots/flow-canvas-frame-1.png"><img src="docs/screenshots/flow-canvas-frame-1.png" width="48%" alt="Run 画布 帧1：待处理"></a>
<a href="docs/screenshots/flow-canvas-frame-7.png"><img src="docs/screenshots/flow-canvas-frame-7.png" width="48%" alt="Run 画布 帧7：全导通"></a>

> 其余界面（任务编排四层下钻、知识库、多级图谱治理、插件拓扑与影子模拟执行账本、量化 / AIOps 工作台、审批中心、风险模拟，以及四张业务域组网网络图、交互式星图、审计大脑树状图、插件数据流全景等）见 [docs/screenshots/](docs/screenshots/)。
## 技术栈

| 层次    | 选型                                                                             |
| ----- | ------------------------------------------------------------------------------ |
| 数据库   | PostgreSQL 16 原生安装 + `pgcrypto / pg_trgm / ltree / vector`，行级安全多租户             |
| 后端    | Python 3.11 + FastAPI + psycopg + Alembic（`apps/api/main.py` 112 个端点）          |
| 迁移    | Alembic 显式迁移（60 个版本），应用启动**不允许**自动升级                                           |
| 桌面端   | Electron 37 + React 19 + Ant Design 5 + Zustand + ECharts + Three.js（13 工作台视图） |
| AI 通道 | 统一网关（Ollama 本地 / OpenAI 兼容云端双协议），一份配置、一个客户端                                    |
| 质量工具  | ruff + mypy strict（107 源文件）+ pytest（1200+ 用例）+ vitest + tsc strict             |

## 目录结构

```
apps/         应用入口（api / worker / policy_service）
packages/     领域包 25 个（ai / ai_planner / policy / plugin_topology / plugin_runtime /
              knowledge / graph / experience / audit / quant / aiops / control /
              observability / llm / ...），107 个源文件
plugins/      插件 SDK + 内置插件（审计网络 100 个 + 跨域 25+），UPP v1.0
contracts/    86 份版本化 JSON Schema（Draft 2020-12）
migrations/   60 个 Alembic 显式迁移
desktop/      Electron + React 19 + AntD 5 桌面控制台（13 视图）
web/          API 同源托管的静态兼容 Shell（非用户入口）
tests/        177 个测试文件 / 33,068 行
docs/         设计文档、阶段状态、验证报告、缺陷审计报告、界面截图（docs/screenshots/）
```

## 快速开始

### 前置要求

| 组件         | 版本                 | 说明                                                               |
| ---------- | ------------------ | ---------------------------------------------------------------- |
| Windows    | 10/11              | 启动脚本使用系统自带 `powershell.exe` 与 `.bat`                             |
| Python     | **3.11+**（推荐 3.12） | `pyproject.toml` 要求 `>=3.11`                                     |
| PostgreSQL | **16+**            | 迁移依赖较新特性；实测 16.13                                                |
| pgvector   | 与 PG 版本匹配          | **最大的坑**：EDB 官方 Windows 安装包**不含** `vector`，需单独装（见 [常见故障](#常见故障)） |
| Node.js    | 20+                | 仅桌面控制台需要                                                         |

数据库还需要扩展 `pgcrypto / pg_trgm / ltree / vector`。不想装 PostgreSQL？`docker-compose.yml` 提供 `pgvector/pgvector:pg16`（端口映射到本机 **54329**）——这只是可选适配器，本机原生 PG 仍是默认路径。

### 一键自举（推荐）

```powershell
powershell -ExecutionPolicy Bypass -File scripts\bootstrap.ps1 -Check   # 只体检，不写任何东西
powershell -ExecutionPolicy Bypass -File scripts\bootstrap.ps1          # 按顺序建 .venv / npm install / .env / 建库建扩展 / alembic upgrade / 测试库
```

### 手动安装（等价步骤）

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[dev]"
npm install --prefix desktop
Copy-Item .env.example .env
# 用超级用户建库、装扩展与授权
psql -U postgres -c "CREATE DATABASE audit_network OWNER audit_migrator;"
psql -U postgres -d audit_network -f scripts/init-native-postgres.sql
psql -U postgres -c "ALTER ROLE audit_app PASSWORD 'admin'; ALTER ROLE audit_migrator PASSWORD 'admin';"
# 迁移（应用账号无 DDL 权限，必须用 migrator 连接）
$env:DATABASE_URL = "postgresql://audit_migrator:admin@localhost:5432/audit_network"
.venv\Scripts\python.exe -m alembic -c alembic.ini upgrade head
# 测试库（只有 pytest 需要）
powershell -ExecutionPolicy Bypass -File scripts\init-test-postgres.ps1
```

### 配置 AI 通道（可选）

`.env` 被 git 忽略，仓库只有不含密钥的 `.env.example`。未配置 `AI_API_KEY` 不影响启动——API、Worker、桌面控制台照常运行，只有 AI 组网规划、画布助手、知识库向量化会被拒绝（**fail-closed，不是故障**）。也可在桌面端「系统 → AI 设置」页填写，无需重启。

```ini
AI_PROVIDER=openai_compat
AI_BASE_URL=https://api.commandcode.ai/provider/v1
AI_MODEL=meituan/LongCat-2.0:free
AI_API_KEY=            # ← 填自己的密钥
```

### 启动桌面中枢

```bat
启动审计智能中枢.bat        # 先启 API + Worker，再打开本地 Electron 控制台（不会自动迁移）
启动审计智能中枢.bat --migrate   # 明确执行迁移后再启动
```

API 文档：`http://127.0.0.1:8010/docs`。

## 测试与质量门

每个阶段都必须全绿：

```powershell
.venv\Scripts\python.exe -m pytest -q          # 需要 audit_network_test 已初始化
.venv\Scripts\python.exe -m ruff check .
.venv\Scripts\python.exe -m mypy packages
npm --prefix desktop run typecheck
npm --prefix desktop run test
```

最近一次实测：pytest **1215 passed / 6 skipped / 0 failed**、`mypy packages` strict Success（107 源文件）、桌面 `vitest --pool=forks` 73 passed、`electron-vite build` 通过。`.github/workflows/quality.yml` 在 ubuntu + pgvector 容器上执行同一套门禁，是上述步骤的权威参照。**测试设计原则**：先写契约测试再实现（先红后绿）；写入类测试重定向到临时路径，从不触碰真实 `.env` 与生产库。

## 里程碑

| 阶段        | 内容                                                                                            | 状态               |
| --------- | --------------------------------------------------------------------------------------------- | ---------------- |
| Phase 0–4 | 插件运行时 / 知识库 / 富媒体 / 多图路由 / 图谱抽取                                                               | ✅ 验收             |
| Phase 5–8 | 图谱合并仲裁 / 审计证据链 / 量化证据链 / 全链路闭环                                                                | ✅ 验收             |
| Phase 9   | AIOps 工作台治理（可追溯读取血缘 + 受控人工确认 + 独立测试库验证）                                                       | ✅ 验收（2026-09-04） |
| CW0–CW7   | 控制平面与插件拓扑系列（Trace、端口契约、日志、DAG 执行、AI 适配器等）                                                     | ✅ 验收             |
| M1–M10    | 插件拓扑里程碑：契约 → 蓝图 → 拓扑服务 → 图谱规划（`planning_intents` 追加式证据、零 LLM 确定性意图匹配、`plan_only` fail-closed） | ✅ 验收（2026-09-08） |

每阶段先更新契约与测试、再实现服务逻辑，验收后更新 [docs/status.md](docs/status.md)（当前阶段真实状态与已验证结论）。

## 边界与诚实清单

- 不连接真实基础设施、不运行生产 Playbook、不提供 `live` 模式、不开放泛化 AUTO 权限；不创建真实交易，量化回测为 `simulated_only` 口径。
- 真实执行只保持为**数据库内的状态投影**，不触达主机、网络或基础设施。
- 未完成：LLM 自动抽图、音视频转写、持久工作流引擎、多插件并发隔离调度、Temporal/NATS、生产级密钥管理、真实交易执行。
- 项目做过一次**独立验证**（源码通读 + 实机重跑 + 独立复算 + 对照实验），主动发现的 9 个缺陷（含 P0 路径穿越、悬挂运行态、种子硬编码）已全部修复并记录在案，见 [docs/status.md](docs/status.md) 与 [缺陷审计报告](docs/缺陷审计报告-数据支撑-下钻-24x7就绪度-20260908.md)。

## 常见故障

**`psql.exe was not found` / `pg_dump not found`**：把 PostgreSQL 的 `bin` 目录加入 `PATH`，或确认装默认位置（脚本会扫描 `*\PostgreSQL\*` 全部版本）。

**迁移报 `permission denied to create extension "vector"`**：`vector` 是非受信扩展，只有超级用户能创建，需对目标库（含测试库）执行 `scripts/init-native-postgres.sql`。**这是新机器上最常见的失败点**；`scripts\bootstrap.ps1 -Check` 会明确列出缺哪些扩展。

**Electron 下载失败**：`npm install --prefix desktop` 时从 GitHub 下载二进制，代理/离线环境会失败；设置 `ELECTRON_MIRROR` 或从其它机器拷贝 `desktop/node_modules`。

**启动后 API 报数据库错误**：默认启动不会迁移；先执行 `alembic upgrade head` 或运行 `启动审计智能中枢.bat --migrate`。

**AI 相关功能被拒绝**：属预期 fail-closed；检查 `.env` 里 `AI_API_KEY` 或改用桌面端「AI 设置」页填写。

## 相关文档

**组网实验报告（核心，详见上文[组网实战](#组网实战四份实验报告核心)）：**

- [docs/AI画布组网与全链路日志优化方案-20260908.md](docs/AI画布组网与全链路日志优化方案-20260908.md)：组网演进方向与 P0 缺口立项
- [docs/组网业务实战报告-20260909.md](docs/组网业务实战报告-20260909.md)：日记账质检 → 回测的真实业务组网
- [docs/AI组网Demo-全链路功能验证报告-20260911.md](docs/AI组网Demo-全链路功能验证报告-20260911.md)：独立验证（实跑 + 复算 + 对照实验）
- [docs/审计报告-多源业务数据组网Demo.md](docs/审计报告-多源业务数据组网Demo.md)：21 节点 / 23 边 / 6 层 DAG 合格性审计

**其他：**

- [docs/status.md](docs/status.md)：当前阶段真实状态与已验证结论
- [项目说明（完整版）](docs/项目说明-审计智能中枢.md)：本 README 的原始素材，含全部设计细节
- [docs/独立化可迁移改造方案-20260912.md](docs/独立化可迁移改造方案-20260912.md)：跨机迁移与离线打包方案
- [docs/仓库可迁移性检查报告-20260913.md](docs/仓库可迁移性检查报告-20260913.md)：仓库"别人下载能否运行"的体检结论
- [docs/plugin-topology-M10.md](docs/plugin-topology-M10.md)：图谱驱动组网规划（最近验收里程碑）