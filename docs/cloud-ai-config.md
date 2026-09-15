# 云端 AI 模型配置（画布 AI 助手 / CW5 AI 组网）

本机使用的云端 OpenAI 兼容通道配置记录。
**2026-09-12 更新**：切换到 `deepseek/deepseek-v4.1-flash`，配置改由统一 AI 接入层（`packages/ai`）管理。

## 模型通道

| 项 | 值 |
| --- | --- |
| 端点 base_url | `https://api.commandcode.ai/provider/v1` |
| 模型 model | `deepseek/deepseek-v4.1-flash` |
| API key | 只存在于本机 `.env`（`AI_API_KEY`，已 gitignore）；本文不保存明文 |
| 请求超时 | `600` 秒 |

> 安全提醒：本文件不保存真实密钥，可安全随项目文档同步。
> 密钥只保存在本机 `.env` 的 `AI_API_KEY` 中；项目源码与版本库内**零明文**（有全仓扫描器把关，
> 见 `tests/integration/test_cw5_second_llm_adapter.py`）。

## 配置方式（2026-09-12 起统一走 packages/ai）

推荐用桌面端「**系统 → AI 设置**」页填写并保存——它会写进本机 `.env` 并立即生效，无需重启。

`.env` 里的键（跑任何入口都生效：API、Worker、`scripts/*.py`）：

```dotenv
AI_PROVIDER=openai_compat
AI_BASE_URL=https://api.commandcode.ai/provider/v1
AI_MODEL=deepseek/deepseek-v4.1-flash
AI_API_KEY=<你的 key>
AI_TIMEOUT=600
```

解析顺序：`AI_*` → 旧的 `OPENAI_COMPAT_*` / `OLLAMA_*` → 内置默认。**显式导出的进程变量始终优先于 `.env`**。
测试进程通过 `AUDIT_NETWORK_SKIP_DOTENV=1` 完全屏蔽 `.env`，保证测试不受本机配置影响。

## 连通性验证

```powershell
.venv\Scripts\python.exe -c "from packages.ai import probe; r = probe(); print(r.ok, r.latency_ms, r.detail)"
```
2026-09-12 实测：`ok=True`，延迟约 1.6 秒。

## 环境变量（运行时读取）

| 变量 | 值 | 说明 |
| --- | --- | --- |
| `OPENAI_COMPAT_API_KEY` | 用户环境变量值（不写入文档） | 必填；未设置时拒绝调用（fail-closed，返回 503） |
| `OPENAI_COMPAT_BASE_URL` | `https://api.commandcode.ai/provider/v1` | 可选；默认即此值 |
| `OPENAI_COMPAT_MODEL` | `meituan/LongCat-2.0:free` | 可选；默认即此值 |
| `OPENAI_COMPAT_TIMEOUT` | `600` | 单次模型调用超时（秒），默认 180 |
| `OPENAI_COMPAT_PROXY` | 按需 | 需要代理访问时设置（如 `http://127.0.0.1:7890`） |

配置命令（PowerShell）：

```powershell
[Environment]::SetEnvironmentVariable("OPENAI_COMPAT_API_KEY", "<key>", "User")
[Environment]::SetEnvironmentVariable("OPENAI_COMPAT_BASE_URL", "https://api.commandcode.ai/provider/v1", "User")
[Environment]::SetEnvironmentVariable("OPENAI_COMPAT_MODEL", "meituan/LongCat-2.0:free", "User")
[Environment]::SetEnvironmentVariable("OPENAI_COMPAT_TIMEOUT", "600", "User")
```

> 注意：用户级环境变量只对之后启动的新进程生效；正在运行的 API/桌面端需
> 注入进程级变量并重启（`$env:OPENAI_COMPAT_API_KEY = ...`）。

## 使用入口

- 桌面端：Run 画布 → 右侧「AI 画布助手」聊天面板
- API：`POST /api/v1/topology/canvas/chat`（经 `topology.intent.plan` 策略门，写入幂等证据）
- 通道切换：`AI_PLANNER_BACKEND=ollama` 可回退本地 Ollama（本机未安装）

## 实测结论（2026-09-09）

- 极简探针（`{"ok": true}`）在 90 秒内返回，认证与网络正常。
- 真实规划请求（「帮我生成链路解决数据审计」）单次可能超过 180 秒，需将
  `OPENAI_COMPAT_TIMEOUT` 提升到 600 秒，桌面主进程与客户端超时需同步放宽。
