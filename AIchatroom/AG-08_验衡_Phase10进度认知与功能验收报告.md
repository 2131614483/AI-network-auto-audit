# Phase 10 进度认知与功能验收报告（AI 交接文档）

- 编号：AG-08
- 代号：验衡（YanHeng）
- 角色：本机验收 / 回归测试代理（只读核对 + 独立测试库冒烟）
- 汇报对象：后续接入 `audit_network` 工作区的 AI 代理
- 生成时间：2026-10-28 22:35（Asia/Shanghai）
- 工作区：`D:\pythonpro\audit_network`
- 结论状态：**Phase 10 仍在进行中**；本轮只做认知梳理与测试，未改代码、未加迁移、未升开发库、未启用新策略

---

## 一、结论先行（给 AI 的一句话摘要）

Phase 10「多源证据驱动的协作规划」核心链路（三源检索 → 审计主脑 → Planner → 硬检查 → Reviewer → 有界补检索 → plan_only 落库）已实现并通过全部回归；本轮新增本机真实 HTTP 冒烟，目录/创建/幂等/列表/详情/租户隔离/策略拒绝均符合预期，`process` 在 Debug 模式无人回填时正确 fail-closed。
**但 Electron 人工验收、替换真实模型、真实业务资料、长期后台运行仍未验收，不得把 Phase 10 标记完成。**

---

## 二、当前阶段边界（先读，避免越界）

依据 `AGENTS.md` 与 `docs/status.md`：

- 只产出 `plan_only` 方案与缺口，**不自动执行新方案**，不触达主机、外网、基础设施。
- 每次来源读取与模型调用必须经 Policy Gateway，携带租户、Trace、会话/轮次引用；写入用幂等键并保存裁决与追加历史。
- 数据库迁移必须**显式执行**，应用启动不自动升级；开发优先本机原生 PostgreSQL 16 + pgvector。
- 验证只允许独立本机测试库、合成资料、可控模型；Debug 模式逐次回填，不冒充无人值守模型服务。
- 历史记录不可硬删除；生产应用账号不得拥有 `BYPASSRLS`。

## 三、已交付能力（文件级索引）

| 层 | 位置 | 状态 |
|---|---|---|
| 闭合契约 / 预算 / 证据 / 目标 | [evidence.py](file:///d:/pythonpro/audit_network/packages/ai_planner/evidence.py) | 已实现 |
| 三源只读适配（图谱 / 历史 / 知识） | `packages/ai_planner/source_adapters.py` | 已实现，RLS + 5s statement timeout |
| 协作循环（检索→规划→硬检查→独立评估→补检索） | `packages/ai_planner/collaborative.py`、`assessment.py` | 已实现，有界停止 |
| 审计主脑交接（先出任务说明再编排） | `packages/ai_planner/collaborative_agents.py`、`test_audit_brain*.py` | 已实现 |
| 会话持久化 / 幂等 / 租约 / checkpoint | `packages/ai_planner/planning_service.py`、`session_store.py`，迁移 `0069_collaborative_planning` | 已实现 |
| 模型接入（Debug 回填 / external 外接） | `session_bridge.py`、`model_access.py`、`packages/ai/config.py` | 已实现，失败不换模 |
| HTTP 入口 | [main.py](file:///d:/pythonpro/audit_network/apps/api/main.py#L6812-L6869) | 5 个 planning 端点 |
| 后台消费者 | `apps/worker/collaborative.py` | 已实现，独立于 Outbox 串行循环 |
| 桌面入口 | 桌面「组网规划」多源协作规划表单 / 结果 / 来源 / 轮次 | 已接线，Electron 白名单已同步 |

规划会话端点：

| 方法与路径 | 用途 |
|---|---|
| `POST /api/v1/topology/planning/sessions` | 按幂等键创建会话 |
| `GET /api/v1/topology/planning/sessions` | 当前租户列表 |
| `GET /api/v1/topology/planning/sessions/{id}` | 详情 + 追加事件 |
| `POST /api/v1/topology/planning/sessions/{id}/process` | 显式运行（同步等待） |
| `GET /api/v1/topology/planning/catalog?domain=audit` | 可验证绑定目录 |

## 四、环境事实（本轮实测，务必先核对再动手）

| 项 | 实测值 |
|---|---|
| PostgreSQL 服务 | `postgresql-x64-16` Running |
| 开发库 `audit_network` | Alembic `0068_connectivity_read_policy`，**无** `topology.planning_sessions` |
| 测试库 `audit_network_test` | Alembic `0069_collaborative_planning`，有 `topology.planning_sessions` |
| 应用账号 | `audit_app:admin@localhost:5432`（受 RLS 限制，跨租户查不到行） |
| 迁移账号 | `audit_migrator:admin@localhost:5432` |
| 超级用户（仅本地核查用） | `postgres:admin@localhost:5432` |
| 策略 `local-collaborative-planning` | 迁移时为 inactive，本轮未启用 |

**含义**：直接对开发库调用规划端点会得到 `503 collaborative planning store unavailable; run explicit migrations`，这是设计行为，不是缺陷；但**未经用户明确授权，不要升级开发库**。

## 五、回归测试结果（全部实跑）

执行环境：`.venv` Python，仓库根目录，`AUDIT_NETWORK_SKIP_DOTENV=1`（单测运行时用于隔离用户模型配置）。

| 检查 | 命令 | 结果 |
|---|---|---|
| Python 单测 | `python -m pytest tests\unit -q` | **1190 passed / 4 skipped**，22.35s |
| 契约 + 数据库集成 | `python -m pytest tests\contract tests\integration -q` | **1334 passed / 9 skipped**，620.09s |
| 桌面单测 | `npm --prefix desktop test` | **100 passed**（11 个测试文件） |
| 根 TypeScript | `npm run typecheck` | 通过 |
| 桌面 TypeScript | `npm --prefix desktop run typecheck` | 通过 |
| 业务代码 Ruff | `python -m ruff check apps packages tests` | 通过 |
| 全仓 Ruff | `python -m ruff check .` | **未通过：历史展示视频脚本 I001 / F841 两项**，与既有基线一致，未改动 |
| Mypy | `python -m mypy packages` | 通过，147 文件 |

跳过项明细（不伪称已覆盖）：

- 4 个单测跳过：`E:\数据` 真实审计资料未挂载。
- 9 个集成跳过：本机 Ollama 不可用（3）、`AUDIT_NETWORK_RUN_OLLAMA_TESTS` 未设置（1）、`test_plugin_graph_recall` 两例“无 sub-0.3 匹配”（2）、隔离演练需在测试库重建后重跑（1）、`AUDIT_NETWORK_RUN_MINERU_TESTS` 未设置（2）。

## 六、真实 HTTP 功能冒烟（本轮新增证据）

方式：以 **测试库** 启动本机 `uvicorn apps.api.main:app`（127.0.0.1:8099），设置 `AUDIT_COLLABORATIVE_DEBUG_SYNTHETIC=1` 走 Debug 模式；用 `urllib` 发真实请求；结束后已停止服务。

| # | 场景 | 实测 | 判定 |
|---|---|---|---|
| 1 | `GET /health` | 200，`database_configured=true` + trace_id | 正常 |
| 2 | 目录不带 `X-Tenant-Id` | 422 | 参数校验正确 |
| 3 | 目录带合法租户（有 allow 策略） | 200，**100 条**绑定 | 正常 |
| 4 | 目录用「不存在于 iam.tenants」的租户 | 503 `run explicit migrations` | **表述误导**（见第八节观察 1） |
| 5 | 创建会话 | 201 `queued`，事件 `created` | 正常 |
| 6 | 同 `idempotency_key` 重放 | 200（不重复建会话） | 幂等生效 |
| 7 | 列表 | 200，1 条 | 正常 |
| 8 | 详情 | 200 `queued`，1 条事件 | 正常 |
| 9 | 跨租户读取他人会话（有策略） | 404 `planning session not found` | 隔离正确 |
| 10 | 跨租户列表 | 200，空数组 | 隔离正确 |
| 11 | 非法租户头 / `limit=999` | 422 / 422 | 校验正确 |
| 12 | 已存在租户但无策略 | 409 `REQUIRE_APPROVAL` + `decision_id` | 策略网关 fail-closed 且留痕 |
| 13 | `POST .../process`（Debug、无会话回填） | 约 120s 后会话 `failed`，`result_json.detail = OpenAICompatChatError: openai-compatible backend HTTP 504: {"error": "current session has not supplied a response; no fallback content"}`；事件链 `created → claimed → query_started → source_query → model_started(audit_brain) → finished`，`model_calls=1`、`rounds=0` | **符合设计**：不伪造、不换模、不降级 |

副作用声明：仅在 `audit_network_test` 写入 3 个随机测试租户与 1 个测试会话，未触碰开发库、真实业务数据、用户 `.env` 与 Key；未执行任何插件。

复现命令（PowerShell，仓库根目录）：

```powershell
$env:DATABASE_URL="postgresql://audit_app:admin@localhost:5432/audit_network_test"
$env:AUDIT_NETWORK_SKIP_DOTENV="1"
$env:AUDIT_COLLABORATIVE_DEBUG_SYNTHETIC="1"
.venv\Scripts\python.exe -m uvicorn apps.api.main:app --host 127.0.0.1 --port 8099
# 另开终端发请求时必须带 X-Tenant-Id；策略规则示例见
# tests\integration\test_collaborative_service.py 的 fixture
```

## 七、未验收项（写结论时必须如实保留）

1. **Phase 10 整体未验收**：状态文档明确“仍在进行中”，不得标完成。
2. Electron 原生窗口人工验收未完成（此前自动审批被策略阻止）。
3. 替换真实模型效果未验收：统一在线配置为 `deepseek/deepseek-v4.1-flash`，此前最小探测返回“余额不足”；未改 Key/配置。
4. 真实业务资料未接入（当前只用合成资料 / 独立测试库）。
5. 长期后台运行（`AUDIT_NETWORK_COLLABORATIVE_PLANNING=1` + Worker）未验收。
6. `accepted` 仅代表静态检查与独立评估通过，**不代表已产生真实业务结果**，也未提交执行器。

## 八、发现与建议（给后续 AI）

1. **观察（未修）**：数据库中不存在的租户请求 planning 端点返回 503 `store unavailable`，真实原因是策略裁决落库时租户外键失败，被统一映射为 psycopg2 错误。建议后续映射为 403/404 并区分“迁移缺失”与“未知租户”；本轮未改代码。
2. **体验**：`process` 同步等待，Debug 模式无人回填时要 ~120s 才返回失败。调用方需设置足够超时；若要改善可考虑快速失败提示，但不得伪造模型输出。
3. 功能验证优先用 `tests\integration\test_collaborative_service.py`（真实 PG + 注入离线模型，覆盖两轮、补查、恢复、事件顺序）。
4. 任何新策略启用、开发库迁移、后台常驻都必须先取得用户明确授权，并保留裁决与追加历史。
5. 报告与状态更新遵循 `AGENTS.md`：阶段完成才更新 `docs/status.md` 并跑全量测试；本轮未写状态文档。

## 九、可复现验证清单

```text
python -m pytest tests\unit -q                                  # 预期 1190 passed / 4 skipped
python -m pytest tests\contract tests\integration -q            # 预期 1334 passed / 9 skipped
npm --prefix desktop test                                       # 预期 100 passed
npm run typecheck ; npm --prefix desktop run typecheck          # 预期通过
python -m ruff check apps packages tests                        # 预期通过
python -m mypy packages                                         # 预期 147 文件通过
```

数据库版本自检（只读）：

```sql
SELECT version_num FROM alembic_version;                       -- 开发库 0068 / 测试库 0069
SELECT to_regclass('topology.planning_sessions');              -- 开发库 NULL / 测试库有值
```

---

报告完。后续 AI 接手本工作区，请先读第二、四、七节，再读实际代码与测试，不要假设“已验收”或“可执行方案”，也不要为了跑通而升级开发库或启用策略。
