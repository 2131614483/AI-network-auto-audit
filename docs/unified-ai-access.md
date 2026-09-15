# 统一 AI 接入（packages/ai）

更新时间：2026-09-11

## 目标

全项目**只有一条**模型接入路径。此前画布 AI 助手、AI 组网规划、知识库向量化
各自 new 客户端、各自读一套环境变量、各自捕获各自的异常类型；本轮把它们收敛到
`packages/ai`：一份配置、一个网关、一个错误基类、一个设置界面。

网关**只是传输层**，不增加任何执行权限：经它产出的草稿仍必须通过能力召回、
数据边界与确定性编译三道闸门，才可能产生可执行计划。

## 分层

| 模块 | 职责 |
| --- | --- |
| `packages/ai/config.py` | provider/端点/模型/密钥/超时的唯一解析处；密钥掩码 |
| `packages/ai/gateway.py` | `UnifiedAIClient`（complete / complete_json / embed）、`probe()` 连通性探针 |
| `packages/ai/embedding.py` | 向量通道：`ollama`（`/api/embed`）与 `openai_compat`（`/embeddings`） |
| `packages/ai/env_store.py` | `.env` 安全读写（保留用户内容、原子替换、注入防护） |
| `packages/ai/errors.py` | `AIClientError` / `AIConfigurationError` |

旧的 `packages/llm/openai_compat_client.py`、`packages/llm/ollama_client.py`
仍是实际传输实现，但它们的错误类型现在都继承 `AIClientError`，因此既有调用点
一行不改也能继续工作，新代码只需捕获一个类型。

## 配置解析顺序

先命中先用，所以现有 `.env` 不需要任何改动即可继续运行：

```
AI_*                              统一变量（AI 设置页写入这一组）
  → OPENAI_COMPAT_* / OLLAMA_*    旧的按客户端命名空间
    → 内置默认值
```

provider 专属的旧变量只在 provider 匹配时参与解析：环境里存在 `OLLAMA_URL`
**不会**把云端通道指向本地（这是无条件读取旧变量最容易踩的坑）。

**值在调用时读取，不在 import 时读取**。这是刻意设计：设置页写入 `.env` 并同时
更新进程 `os.environ`，下一次模型调用立即使用新值，无需重启。相比随后的网络往返，
每次读十来个环境变量的成本可以忽略。

| 设置项 | 统一变量 | 旧变量回退 |
| --- | --- | --- |
| 通道 | `AI_PROVIDER` | `AI_PLANNER_BACKEND` |
| 端点 | `AI_BASE_URL` | `OPENAI_COMPAT_BASE_URL` / `OLLAMA_URL` |
| 模型 | `AI_MODEL` | `OPENAI_COMPAT_MODEL` / `OLLAMA_CHAT_MODEL` |
| 密钥 | `AI_API_KEY` | `OPENAI_COMPAT_API_KEY` |
| 超时 | `AI_TIMEOUT` | `OPENAI_COMPAT_TIMEOUT` / `OLLAMA_CHAT_TIMEOUT` |
| 上下文窗口 | `AI_NUM_CTX` | `OLLAMA_CHAT_NUM_CTX` |
| 向量通道 | `AI_EMBED_PROVIDER` / `AI_EMBED_BASE_URL` / `AI_EMBED_MODEL` / `AI_EMBED_TIMEOUT` / `AI_EMBED_DIMENSIONS` | `OLLAMA_URL` / `OLLAMA_EMBED_MODEL` / `EMBEDDING_DIMENSION` |

## 记忆（.env）

设置页填写一次即持久化到项目根 `.env`（已在 `.gitignore`，不进版本库），
重启后依然生效。写入遵循三条规则：

1. **不破坏用户内容**：注释、空行、`DATABASE_URL`、行序都逐字节保留，只有
   `packages.ai.config.MANAGED_ENV` 里的键会被新增/替换/删除。
2. **不泄露密钥**：密钥只写入 `.env`；读取接口只返回 `api_key_set` 与掩码尾 4 位。
   策略审计账本里记录的是**被修改的键名**，不含值。
3. **不写坏文件**：临时文件 + `os.replace` 原子替换，覆盖前留 `.env.bak` 快照；
   值里出现换行直接拒绝（否则等于允许注入任意环境变量行）。

进程启动时以**默认值**语义加载（`load_env_file()`, `override=False`）：显式导出的
进程变量（启动脚本的 `DATABASE_URL`、用户级 `OPENAI_COMPAT_API_KEY`）始终优先，
`.env` 是持久默认值而不是劫持。

## API

| 方法 | 路径 | 策略 CAP | 风险 |
| --- | --- | --- | --- |
| GET | `/api/v1/ai/settings` | `ai.settings.read` | read_only |
| PUT | `/api/v1/ai/settings` | `ai.settings.write` | low |
| POST | `/api/v1/ai/test` | `ai.provider.test` | low |

- 三个端点都要求 `X-Tenant-Id`；PUT 还要求 `Idempotency-Key`。
- PUT 是**部分更新**：字段缺省（`null`）表示不改，空字符串表示清除（回退到旧变量
  或内置默认）。`api_key` 只写不读。
- POST 是用户主动触发的诊断：通道不通时返回 **200 + `ok=false` + 原因**，
  而不是 HTTP 错误——界面需要把失败原因渲染出来。
- 迁移 `0058_ai_settings_policy` 在 local-dev 基线策略集里种入这三个 CAP，
  与 0054/0056 同样只授配置与诊断权限，不新增任何执行能力。

## 桌面端

左侧「系统 → AI 设置」：可填通道类型、端点、模型、密钥、超时、代理、
向量模型与维度；每个字段标注**当前值来自哪个环境变量**（旧变量会显式标出），
便于解释"为什么这里有一个我没填过的值"。保存后立即生效，并可一键测试连接。

Electron 主进程白名单新增两条路径；`SafeRequest.method` 扩展支持 `PUT`
（PUT 与 POST 一样自动带 `Idempotency-Key`）。渲染器无法访问白名单外的接口。

## 边界

- 网关不授予执行权：草稿仍走同一套召回/边界/编译闸门。
- 未配置密钥时远程通道 fail-closed 拒绝，绝不返回假结果。
- 探针是**用户显式触发**的诊断，不进入自动化热路径。
- 密钥零硬编码；`.env` 不进版本库；接口不回传明文密钥。
