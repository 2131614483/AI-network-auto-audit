# DeepSeek Harness 与 Audit Network：定位对比与接入评估

**日期**：2026-09-14
**评估对象**：本项目（`audit_network`，超级审计与量化智能中枢）与 DeepSeek AI 开源的智能体框架 **DeepSeek Harness**（简称 `dsh`）
**问题**：两者有什么区别？哪个好？本项目能不能以 DeepSeek 为底座？

---

## 一、结论速览

| 问题 | 结论 |
| --- | --- |
| **区别** | 不是同类产品。本项目是**领域控制平面**（计划驱动：闸门裁决 + 确定性 DAG 执行 + 证据链）；dsh 是**通用智能体运行时**（模型驱动：模型路由 + 工具/沙箱/循环 + 可回放轨迹）。两条正交的轴。 |
| **哪个好** | 各自在自己目标上更好，属伪二选一。就"完成审计业务并留下可辩护证据"，本项目明显更强（dsh 不提供 RLS、迁移、策略网关、证据包复验）；就"模型自主性与工具生态"，dsh 强一个数量级（本项目可被 AI 规划并真实执行的能力目前仅 **2** 条）。 |
| **能否以 DeepSeek 为底座** | **模型层：已经是。** `.env` 中 `AI_MODEL=deepseek/deepseek-v4.1-flash`，走 OpenAI 兼容通道。**运行时层：可以做，但只做"起草层"。** dsh 官方提供 Python SDK，以子进程 + stdio JSON-RPC 驱动，草稿交回本项目过闸门；**不建议**把本项目迁到 dsh 之上。 |

---

## 二、DeepSeek Harness 是什么

### 2.1 事实清单

| 项 | 内容 | 来源可信度 |
| --- | --- | --- |
| 发布 | 2026-08-13 发布开发者预览版 v0.1，同日 MIT 开源 | 二手（上证报、百度百科、阿里云帮助中心） |
| 仓库 | `github.com/deepseek-ai/deepseek-harness`，当前版本线 **0.1.5** | 一手（仓库 `package.json` 提交信息） |
| 许可 | MIT（提交记录：`Adopt MIT for DSH packages`） | 一手（仓库 `LICENSE`） |
| 核心哲学 | 「Model + Harness = Agent」「Everything is a plugin」 | 二手 + 镜像站 |
| 内核 | Cordis 微内核（`vendor/` 内 cordis 4.0.2）；模型、工具、技能、会话、沙箱、存储、循环、调度、UI 全部是插件 | 一手（仓库 `vendor/` 目录） |
| 运行模式 | 四种：标准 / PTC（程序化工具调用）/ 极简 / 创造 | 二手 |
| 可追溯 | 仅追加会话日志，Trajectory 视图按来源检索，支持恢复 / 分叉 / 回放 | 二手 |
| 模型无关 | 支持接入约 40 家提供方，含 OpenAI 兼容端点与本地模型服务 | 二手 |
| 定位 | 开发者工具与基础设施；对标 Claude Code / Codex；社区插件以 `dsh-plugin` 标签收录 | 二手 |
| 稳定性 | v0.1 开发者预览，**官方警告存在破坏兼容性的变更**；主仓库暂不接收外部 PR | 二手（百度百科、镜像站） |

### 2.2 关键事实：官方自带 Python SDK（决定了"能否接入"）

这是本次评估中最有决定性的一条，来自仓库 `python/README.md`（一手）：

> "Python packages for driving DeepSeek Harness as a subprocess. The client SDK communicates with the bundled runtime over **newline-delimited JSON-RPC on stdio**."

| 包 | 模块 | 作用 |
| --- | --- | --- |
| `deepseek-harness-sdk` | `deepseek_harness` | 高层 turns API + 底层 JSON-RPC 客户端 |
| `deepseek-harness-runtime-bin` | `deepseek_harness_runtime` | 捆绑 `dsh` CLI 可执行文件与原生 sidecar |

其他要点：

- SDK 会启动随包附带的 `dsh --profile sdk` 运行时（可指定其他 `dsh` 或 profile）；另有 `sdk-minimal` 极简 profile。
- **每次启动都必须显式指定 Harness home**，Python 侧不会静默读取 `~/.dsh`——默认行为干净，符合"不隐式读用户环境"的纪律。
- 仓库内含 `pytest.ini` 与 `python/` 组件，说明它是多语言 monorepo（TypeScript 内核 + Python 侧车）。

---

## 三、定位对比：两条正交的轴

```
┌─────────────────────────────────────┐   ┌─────────────────────────────────────┐
│  audit_network                      │   │  DeepSeek Harness                   │
│  领域控制平面 · 计划驱动              │   │  通用智能体运行时 · 模型驱动          │
├─────────────────────────────────────┤   ├─────────────────────────────────────┤
│  闸门：能力白名单 · 数据边界 · 契约    │   │  模型路由：40+ 提供方可换            │
│  执行：编译成固定 DAG，隔离进程        │   │  工具·技能·沙箱·循环：Cordis 插件    │
│  证据：execution_hash · 可离线复验    │   │  轨迹：仅追加日志，可回放/分叉        │
└─────────────────────────────────────┘   └─────────────────────────────────────┘
```

| 维度 | audit_network | DeepSeek Harness |
| --- | --- | --- |
| 要解决什么 | 完成审计 / 量化业务，并留下**可辩护的证据** | 让模型**自主干活**（编程、办公） |
| "插件"是什么 | **业务能力单元**（123 个插件目录，按数据契约连线） | **运行时能力**（模型 / 工具 / 沙箱 / 循环 / UI） |
| 谁做决策 | 人定计划 → 确定性执行；AI 只**起草计划**且必过闸门 | **模型自主循环**决策，人按策略审批 |
| 结果是否可复现 | 是（编译期固定 DAG + `execution_hash`） | 不必（每次是一个新会话） |
| 追溯靠什么 | 证据包 + 离线复验 + attempt 账本 | 仅追加会话日志 + 回放 / 分叉 |
| 底座语言 | Python + PostgreSQL（桌面端另有 Node） | Node / TypeScript（Cordis），另有官方 Python SDK |
| 生态 | 自建 123 插件 | Cordis 插件市场（数千级） |
| 成熟度 | 自迭代中的控制平面 | v0.1 开发者预览，**官方警告破坏性变更** |

### 一个值得注意的观察

两个项目**在哲学上高度重合**，是各自独立收敛的结果：

- 都是「一切皆插件」；
- 都是「仅追加日志 + 全链路可追溯」；
- 都主张沙箱隔离 + 策略审批后才允许执行。

本项目 2026-09-13 的六步收束（引用配方 / 编译封存 / 版本钉住 / 衍生谱系 / 项目锚点 / 可回放）走的是同一条路。这既说明设计直觉正确，也意味着 dsh 的做法（四种模式、Trajectory 视图、插件目录）可以**以概念形式**借鉴，而不必整体引入。

---

## 四、"哪个好"：按目标分，不按阵营

### 4.1 就"完成审计业务并留下可辩护证据"——本项目更强

dsh **不提供**本项目被视为产品本身的那些东西：多租户 RLS、61 个顺序迁移、策略网关、证据包离线复验、确定性重放、端口契约闸门。这不是 dsh 的缺陷，而是它的目标里没有这一项。

### 4.2 就"模型自主性与工具生态"——dsh 强一个数量级

本项目 AI 面的实测数据（2026-09-14 核对）：

| 指标 | 实测值 |
| --- | --- |
| `RUNTIME_CAPABILITIES`（可被 AI 规划并真实执行的能力） | **2** 条：`audit.ledger.validate`、`quant.experiment.evaluate` |
| `PORT_CONTRACTS`（召回给模型的端口契约） | 7 条（已改为从 schema 文件派生真哈希） |
| 意图评测集 | 20 例，其中带恶意草稿的负向用例 12 例 |
| 评测是否走真实推理 | 否——用**注入式脚本模型**，保证可重复 |
| MCP 接入 | 无（`packages/`、`apps/` 内零命中） |

结论：强项在"闸门 + 确定性执行 + 证据"，弱项正好是"智能体运行时"——而那正是 dsh 的全部。

### 4.3 因此正确的问题不是"谁替换谁"

而是：**要不要用 dsh 补上本项目较弱的那一半（智能体运行时）**，同时不放弃现有的一半（裁决与证据）。

---

## 五、能不能以 DeepSeek 为底座

"底座"在这里有三种读法，结论不同。

### 读法 A：用 DeepSeek **模型**做底座 —— 已经在用

- `.env`：`AI_PROVIDER=openai_compat`、`AI_BASE_URL=https://api.commandcode.ai/provider/v1`、`AI_MODEL=deepseek/deepseek-v4.1-flash`。
- `packages/ai/config.py`：`SUPPORTED_CHAT_PROVIDERS = (openai_compat, ollama)`，`DEFAULT_EMBED_PROVIDER = ollama`。
- 桌面端「AI 设置」页可改，下次调用即生效，无需重启。
- `packages/ai/__init__.py` 已写明边界：**"The layer is transport only. It grants no execution authority."**——换模型不改变权限集合。

**换模型 = 改两行 `.env`。此项无需任何改造。**

### 读法 B：用 DeepSeek **Harness** 做智能体底座 —— 可行，但只做"起草层"

可行性来自官方 Python SDK：**子进程 + stdio 上的换行分隔 JSON-RPC + 产物回传**。

这条接口与本项目**已有纪律同构**：插件本来就跑在 `python -I` 子进程里，产物按 `sha256` 复核。dsh 也是"子进程 + 明确定义的协议 + 产物回传"，接进来不改变信任模型。

推荐分工：

```
dsh 子进程（只读 + 草稿）  →  本项目闸门（能力/边界/契约）  →  本项目确定性执行 + 留证
     JSON-RPC over stdio         compile_plan 不过则 gap_report      隔离进程 · 可复现
```

落地形态（最小可行）：

1. 给 `AiPlanner` 的 `DraftLLM` 增加一个 `dsh` 适配器（与现有 ollama / openai_compat 并列）；
2. dsh 在子进程内读取本项目的**能力召回表**（`RUNTIME_CAPABILITIES` + `PORT_CONTRACTS`）、探索、产出**草稿 JSON**；
3. 草稿回到 Python 侧后，走**完全相同**的闸门链：能力白名单 → 数据边界 → 端口契约逐字段一致 → `compile_plan`；
4. 闸门不过照旧出 `gap_report`，dsh 拿不到任何执行权。

### 读法 C：把整个项目迁到 dsh 之上 —— 不建议

会丢掉本项目真正值钱的部分：61 个顺序迁移、36k 行测试锁住的累积不变量、RLS 证据链、策略网关、可离线复验的证据包。dsh 不提供这些——它不是领域平台，是运行时。迁移等于拿护城河换轮子。

---

## 六、接入时的两条硬红线

沿用本项目 `AGENTS.md` 的既有约束，接入 dsh 必须满足：

1. **不给 dsh 工作区写权限。** dsh 的默认形态是"对工作目录读写、跑命令、派子 Agent"的编程代理，而本项目明令"不得启动任意命令"。因此只允许它运行在**只读 + 草稿产出**模式，工作目录隔离，产物是 JSON 而不是补丁。
2. **不让 dsh 成为必要依赖。** dsh 处于 v0.1 开发者预览且官方警告破坏性变更，主仓不接收外部 PR。应把它放进**可选路径**（`AI_PROVIDER` 新增一路 `dsh`），保留 ollama / openai_compat 两条现成通道；dsh 不可用时既有流程不受影响（fail-closed 到 `gap_report`，而不是失败）。

---

## 七、建议的验证方式（可量化）

本项目已有 `tests/evaluation/test_ai_planning_intents.py`（20 个固定意图，含 12 个负向 / 3 个显式恶意），可直接做**开 / 关对照**：

| 指标 | 含义 |
| --- | --- |
| 首次通过闸门率 | dsh 起草的草稿一次过闸门的比例 |
| 平均修订轮数 | 需要几轮修订才过 |
| 端到端时延 | 起草 + 裁决 + 编译 |
| 候选 token 量 | 召回表 + 草稿体量 |
| 负向用例是否仍被拒绝 | **安全回归**：12 个负向 / 3 个恶意必须全部被拒 |

最后一条是准入条件：**任何提高通过率但放松负向拦截的改动都不可接受**——沿用本项目"经验只改变概率分布，不改变权限集合"的通用约束。

---

## 八、风险与未验证项

**风险**

- dsh 为 v0.1 预览版，接口与插件协议可能破坏性变更 → 故只放在可选路径，不进入必要链路。
- 引入 Node 运行时与 `dsh` 可执行文件（本项目桌面端已依赖 Node，非全新依赖，但会新增一个二进制分发面）。
- 自主探索带来的**非确定性**：必须严格限制在"起草层"，不得进入执行层与证据链。

**未验证项（不要当成已确认）**

- ❌ 未在**本机实际跑通** dsh 的 Python SDK（本轮只做了资料核实与本项目侧代码核对，未安装 dsh、未发起任何 dsh 调用）。
- ❌ 未验证 dsh 在受限网络 / 离线环境下的可用性。
- ⚠️ 「40+ 模型提供方」「四种运行模式」「Trajectory 回放/分叉」等描述来自**二手来源**（阿里云帮助中心、上证报、百度百科、第三方镜像站）；官方文档站本次抓取为空，未能一手确认。已一手确认的仅：MIT 许可、`python/` SDK 与其 stdio JSON-RPC 通信方式、0.1.5 版本线、Cordis 依赖。

---

## 附录：证据与来源

**本项目侧（一手，2026-09-14 实测）**

- `.env`：`AI_PROVIDER` / `AI_BASE_URL` / `AI_MODEL` / `AI_EMBED_PROVIDER` 配置项。
- `packages/ai/config.py`：`SUPPORTED_CHAT_PROVIDERS`、`DEFAULT_EMBED_PROVIDER`。
- `packages/ai/__init__.py`：AI 层"仅传输、不授予执行权"的设计声明。
- `packages/llm/`：`ollama_client.py`、`openai_compat_client.py` 两条传输通道。
- `packages/ai_planner/catalog.py`：`RUNTIME_CAPABILITIES`（2 条）、`PORT_CONTRACTS`（7 条）。
- `packages/ai_planner/evaluation.py`：`EVALUATION_CASES`（20 例）。
- 插件执行隔离与产物校验：`packages/plugin_runtime/runner.py`、`packages/plugin_topology/isolated.py`。

**dsh 侧**

- 一手：`github.com/deepseek-ai/deepseek-harness` —— `python/README.md`（Python SDK 与 stdio JSON-RPC）、`LICENSE`（MIT）、`package.json`（0.1.5 版本线）、`vendor/`（Cordis 4.0.2）。
- 二手：阿里云帮助中心《DeepSeek Harness：构建插件化智能体》、上证报《DeepSeek Harness 开发者预览版上线》、百度百科「DeepSeek Harness」、第三方镜像站 `deepseekharness.io`。
