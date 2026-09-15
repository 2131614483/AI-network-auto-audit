# AI 组网 Demo — 原始数据归档（2026-09-11 报告配套）

这个文件夹把 `AI组网Demo-全链路功能验证报告-20260911.md` 涉及的数据、日志、原始输出集中到一处，
避免再去 `.data/`（那个目录既大又乱）里翻。

**归档时间**：2026-09-12
**来源**：`D:\pythonpro\audit_network\.data\`（`.data/` 在 `.gitignore` 里，不会进版本库）

---

## 目录说明

| 目录 | 内容 |
| --- | --- |
| `01-报告/` | 两份 md：9-11 那份验证报告（**修复前**快照）+ 9-12 修复验证与测试指引（**修复后**） |
| `02-运行日志/` | demo 脚本的**完整标准输出**（逐步骤、逐节点、含 sha256）。UTF-8 编码，记事本可直接打开 |
| `03-证据产物/` | `grouping-business-evidence.json` + 10 个证据包 zip（每次真实运行的证据打包，含整体 SHA256 与证明摘要） |
| `04-中间数据/` | 每个 plan 的**节点级产物**（插件真实的中间结果 JSON）、运行日志 spool |
| `05-数据库原始记录/` | 从数据库导出的**原始行**：node_attempts / tool_calls / policy_decisions / outbox |
| `校验清单-sha256.csv` | 本目录全部 61 个文件的字节数与 sha256，可用于核对完整性 |

---

## 关键 run 一览

| run_id（前 8 位） | 时间 | plan_key | 性质 |
| --- | --- | --- | --- |
| **`3b6667c3`** | 09-11 19:57 | `plan-ledger-quality-audit-and-evaluate` | ⭐ **云端 AI 真实组网成功**：模型自定节点名（`ledger-validate-001` / `finding-draft-001`），2 节点全 succeeded |
| `8cbb3b4f` | 09-11 20:03 | `demo-f1a9e446` | 确定性回退（当时云端网络抖动） |
| `6135885f` | 09-12 09:52 | `demo-da2cb68f` | 确定性回退（**API Key 缺失**，见下） |
| `59fc49da` | 09-12 09:54 | `demo-338532a4` | 同上（UTF-8 版日志对应这次） |

`05-数据库原始记录/` 里每个 run 一个 JSON，含：
- `node_attempts` —— 每个节点的状态、输入绑定、产物引用、**是否已终结**（`finished_at`）
- `policy_decisions` —— 策略裁决（decision / risk_score / reason）
- `outbox_events` —— 事务性事件（`node.finished`）
- `trace_ids` —— 贯穿三层的同一 trace
- `runs-总览.csv` —— 最近 60 个 run 的汇总

**四个 run 的 attempt 全部 `finished_at` 非空** —— 即本轮修复的「attempt 永久 running」问题在这些 run 上已不存在。

---

## 三件必须知道的事（诚实说明）

### 1. 云端 API Key 目前**未配置**，所以新跑的 demo 会走回退

09-12 01:03 的系统重启（Windows 更新）清掉了**进程级**环境变量 `OPENAI_COMPAT_API_KEY` —— 它当初是用
`$env:OPENAI_COMPAT_API_KEY = ...` 设在某个 shell 里的，不是 User 级持久变量，注册表里也没有。

所以现在跑 demo 会看到 `OPENAI_COMPAT_API_KEY is not set` → 3 次重试 → **显式降级为确定性模板计划**。
**这是正确的 fail-closed 行为，不是 bug。**

要恢复云端通道，二选一：
- 在**当前 shell** 里设进程级变量后重跑（`docs/cloud-ai-config.md` 记的方式）；
- 或直接用桌面端「**系统 → AI 设置**」页填写并保存（会写进本机 `.env`，长期有效、重启不丢）。

### 2. `grouping-business-evidence.json` 每跑一次就被覆盖

demo 脚本固定写 `.data/demo/grouping-business-evidence.json`，**不按 run 分文件**。
所以 `03-证据产物/` 和 `04-中间数据/` 里那份是**最后一次运行**的（09-12 09:54，回退版）——
9-11 报告所依据的那一份已经被后续运行覆盖，无法找回。

**没丢的是**：证据包 zip 按 run_id 命名（10 个都在），数据库行按 run_id 保留，
节点产物按 plan_key 分目录（含 AI 成功那次的）—— 这些是可追溯的。

### 3. AI 成功那次的**标准输出日志没有留存**

`3b6667c3`（云端 AI 真实组网成功）当时是在终端里跑的，stdout **从未落盘**。
能证明它的是：`05-数据库原始记录/run-3b6667c3-*.json`（2 节点 succeeded、已终结）
与 `04-中间数据/节点产物/plan-ledger-quality-audit-and-evaluate/`（两个节点的真实产物）。
本目录里 `02-运行日志/` 的那份是 09-12 回退版的完整输出。

---

## 怎么复现

```powershell
cd D:\pythonpro\audit_network

# 1) 想看完整输出：直接重跑，输出重定向到文件
.venv\Scripts\python.exe -u scripts/demo-grouping-business.py > demo-run.log 2>&1

# 2) 想让它走云端 AI：先配置通道（桌面「系统 → AI 设置」），或在当前 shell 里设
#    $env:OPENAI_COMPAT_API_KEY = "<你的 key>"

# 3) 核对本归档完整性
.venv\Scripts\python.exe -c "import csv,hashlib,pathlib; base=pathlib.Path('docs/AI组网Demo-20260911-原始数据'); rows=list(csv.DictReader(open(base/'校验清单-sha256.csv',encoding='utf-8-sig'))); bad=[r['相对路径'] for r in rows if hashlib.sha256((base/r['相对路径']).read_bytes()).hexdigest()!=r['sha256']]; print('文件数', len(rows), '| 不一致', bad or '无')"
```

---

## 相关文档

- `01-报告/AI组网Demo-全链路功能验证报告-20260911.md` —— 9-11 的验证报告（**修复前**，其「云端从未通过闸门」「蓝图 0 行」等结论已被证伪）
- `01-报告/修复验证与测试指引-20260912.md` —— **修复后**的状态、可照跑的验证步骤、对 9-11 报告的更正
- `docs/status.md` —— 完整交付记录与待决策事项
- `docs/cloud-ai-config.md` —— 云端通道配置方式
