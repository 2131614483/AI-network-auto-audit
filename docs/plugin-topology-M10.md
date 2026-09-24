# Plugin Topology M10 实施计划：图谱驱动组网规划（Graph-Driven Orchestration Planning）

> 状态：**已实施并验收**（2026-09-08）。
> 关联：`docs/plugin-topology-M9.md`、`docs/plugin-topology-orchestration.md`、`AGENTS.md`。
> M10 目标：把知识图谱变成**组网规划的语义索引**——中文业务意图（如「总账质量校验并出具量化研究结论」）经**确定性图谱匹配**（pg\_trgm 打分 + 单跳有界扩展，**零 LLM、零外网、零新增执行面**）解析为能力需求，映射回既有蓝图，再复用 M1–M6 已验收的规划器生成 **plan\_only** 路由计划；每次规划意图作为**追加式证据**落 `topology.planning_intents`。匹配失败或缺少链接蓝图时 **fail-closed 拒绝**，不落任何计划。为 M11 起的「编排意图 → 运行时」闭环预铺语义底座，但**不引入** Agent 任务、调度器、真实执行、可执行后果。

## 0. 合规边界（前置声明）

- 规划仅由**用户/桌面显式触发**（幂等键 + 单次 + fail-closed），无后台调度、无自动重试、无定时任务；规划结果恒为 **plan\_only**（只路由、不执行）。

- **零 LLM / 零外部网络**：意图匹配完全由 pg\_trgm 相似度打分（`similarity(label)` vs `similarity(alias)` 取最大）+ 图库内单跳有界遍历完成；不调用任何模型、不访问任何外部服务、不引入本地大模型后端。

- 意图文本与匹配证据作为**追加型证据**持久化：`planning_intents` 每行仅 INSERT 一次，不得 UPDATE/DELETE/硬删；同一 `Idempotency-Key` 重放返回既有证据行。

- 演练全部落在独立测试库 `audit_network_test`；不触达生产数据库与真实基础设施；桌面仅提供只读入口与证据展示，无任意子进程、无调度入口。

- 证据约束延续：`planning_intents` 采用 RLS + FORCE + 仅 INSERT+SELECT（不可 UPDATE/DELETE）；写入经 Policy 网关 + 租户 + Trace + 幂等键。

- 能力门控 fail-closed：`topology.intent.plan`（新增，种子 **inactive**）未放行即 403 零写入零子进程；`topology.intent.read` 追加进既有 `local-plugin-topology-read` 策略集（M4 起 active），只读列表/详情经其放行。

## 1. 范围

| 做                                                                                                              | 不做                                   |
| -------------------------------------------------------------------------------------------------------------- | ------------------------------------ |
| 中文业务意图 → pg\_trgm 确定性打分匹配 L2 能力 / L3 业务域图谱节点（label + 别名，下限 0.05）                                               | LLM / 向量 / 外部网络 / 人工语义角色             |
| 单跳有界图遍历：域节点经 active `capability_contract` 桥接边扩展 L2 能力节点；能力节点经内部 `depends_on` 边扩展；硬上限 8 节点/次                    | 多跳遍历；跨图自由漂移                          |
| 能力节点 → `topology.blueprint_graph_links` → 蓝图能力 token（`plugin_blueprints.capability_contract.capability`）→ 能力需求 | 未绑定蓝图的节点不产生需求（fail-closed 400）       |
| 复用既有 `TopologyService.plan()` 生成 plan\_only 计划；确定性 `plan_key` 冲突时**复用既有计划**（不重复落规划）                            | 计划执行 / canary / 自动审批 / 在线变更          |
| `planning_intents` 追加式证据（意图、匹配证据、计划引用、幂等重放）                                                                    | 计划正文/payload 大字段入证据行；证据行可改可删         |
| 门控 fail-closed：`topology.intent.plan` 默认 inactive → 403；无匹配/无链接蓝图 → 400 零落库                                    | 跨租户观测；绕过 Policy 网关（read 与 plan 均经网关） |
| 只读 GUI：「图谱规划」标签页（意图输入 / 匹配证据表 / 计划结果卡）                                                                         | 桌面暴露任何执行/删除/覆盖入口                     |

## 2. 契约先行（先于服务逻辑）

新增 1 份 Schema（`contracts/jsonschema/topology-planning-intent.schema.json`，命名沿用 `topology-` 前缀，避免与既有共享契约冲突）：

| Schema                                    | 内容                                                                                                                                                              | 关键约束                                                                                                                                                                                                                                                                                                                                                                                             |
| ----------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `topology-planning-intent.schema.json`（新） | 请求根：`intent`（中文业务意图 1–512 字符）、`budget{max_matches 1–16, expand_hops 0–1}`、`idempotency_key`、`reason`、可选 `trace_id`；响应结构见 `$defs/planningIntentResult`（追加式证据行投影） | 根与子结构均 `additionalProperties: false`；`budget` 两个字段必填且范围闭合；响应：`intent_id` uuid、`matched_nodes` 1–32 项（`matchedNode`：`node_key/space_key/label/score 0–1/match_kind∈[direct,alias,expanded]/match_source∈[trigram,bridge,depends_on]`）、`capability_requirements` 1–32 项且 `uniqueItems`、`plan_key` 形如 `plan-[a-z0-9]{16}`、`mode` 恒 `plan_only`、`plan_checksum` 64 hex、`reused_plan/idempotent` bool |

`contracts.py` 增加 `validate_planning_intent`/`validate_planning_intent_result`（JSON Schema 之外强校验 + `$ref` registry 显式注册 `$id`，规避 `PointerToNowhere`）；契约正反例测试覆盖意图空/超长、预算越界、缺幂等键、score 越界、match 枚举越界、需求非小写点分、`mode=execute`、plan\_key 非法、chec\`\`ksum 非 64 hex、matched\_nodes 空、携带 payload 正文一律拒绝。

## 3. 表结构（迁移 0048）

`migrations/versions/0048_graph_driven_planning.py`，`down_revision = "0047_evidence_seq_explicit"`（revision 25 字符 < 32 上限）。全部幂等；显式应用 test/dev 库，可重放。

```sql
CREATE TABLE IF NOT EXISTS topology.blueprint_graph_links (  -- 蓝图 ↔ 图谱节点绑定（目录性质，可维护）
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES iam.tenants(id),
  blueprint_key text NOT NULL,
  graph_space_key text NOT NULL,
  node_key text NOT NULL,
  relation text NOT NULL DEFAULT 'plans',
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS topology_blueprint_graph_links_node_idx
  ON topology.blueprint_graph_links (tenant_id, graph_space_key, node_key);
-- RLS + FORCE + 租户策略；GRANT SELECT, INSERT, UPDATE（管理侧维护绑定）

CREATE TABLE IF NOT EXISTS topology.planning_intents (      -- 追加式证据：意图 → 图谱匹配合 → 计划
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES iam.tenants(id),
  intent_text text NOT NULL CHECK (char_length(intent_text) BETWEEN 1 AND 512),
  matched_nodes jsonb NOT NULL CHECK (jsonb_typeof(matched_nodes) = 'array'),
  capability_requirements jsonb NOT NULL CHECK (jsonb_typeof(capability_requirements) = 'array'),
  plan_key text NOT NULL,
  plan_checksum text NOT NULL CHECK (plan_checksum ~ '^[A-Fa-f0-9]{64}$'),
  mode text NOT NULL DEFAULT 'plan_only' CHECK (mode = 'plan_only'),
  reused_plan boolean NOT NULL DEFAULT false,
  node_count integer NOT NULL DEFAULT 0,
  edge_count integer NOT NULL DEFAULT 0,
  trace_id text NOT NULL,
  idempotency_key text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(tenant_id, idempotency_key)
);
CREATE INDEX IF NOT EXISTS topology_planning_intents_plan_idx
  ON topology.planning_intents (tenant_id, plan_key, created_at DESC);
-- RLS + FORCE + 租户策略；只 GRANT SELECT, INSERT，无 UPDATE/DELETE
```

- **确定性匹配索引**：`graph.nodes.label` 与 `graph.node_aliases.alias` 各有两类 trgm 索引（`CREATE EXTENSION IF NOT EXISTS pg_trgm`）：
  - **GiST（`gist_trgm_ops`，迁移 `0067_graph_knn_gist_indexes`）—— 实际生效的那一类。** 召回用 KNN（`ORDER BY label <-> q LIMIT k`）走它：按距离有序扫、到 k 即停，**不需要选择性估计**，因此以应用角色（受 RLS 约束）执行时也照样走索引。
  - GIN（`gin_trgm_ops`，迁移 `0048`）保留，服务 `%` / `LIKE`。**但召回不能靠它**：`similarity >= x` 写成 `a % b` 虽语义等价，计划器却需要选择性估计才肯选它；而 `graph.nodes` 受 RLS 保护，以非 superuser 角色执行时估计会塌成表行数的 1 %（50 000 节点实测：真实 7 行、估 1–509 行）→ 计划器改全表扫描。50 000 节点实测：KNN **6.0–6.6ms** vs 全表 **238–269ms**。

- **种子数据**：`capability-l2`（L2 能力空间）+ `audit-l3`（L3 业务域空间），含中文/英文别名的能力节点 `audit.ledger.validate`、`quant.research-note.draft` 与域节点；内部 `depends_on` 边（`audit.ledger.validate → audit.finding.draft`）；active `capability_contract` 桥接规则与桥边（`audit-l3 域节点 → audit.ledger.validate / quant.research-note.draft`）；`blueprint_graph_links` 三条绑定将上述三能力节点分别链接到 seed 蓝图（`ledger-quality`、`research-note`、`finding-draft` 对应能力 token）。

- **策略种子**：既有 `local-plugin-topology-read` 策略集**扩展** `topology.intent.read`（read\_only）保持 active；新增 `local-plugin-topology-graph-plan` 策略集（`topology.intent.plan`，low / write\_data）种子 **inactive**——fail-closed，未放行即 403。

## 4. 服务层

### 4.1 图谱侧 `packages/graph/graph_planning.py` —— `CapabilityGraphAdapter`

纯读适配，面向 `graph` schema 只读查询：

- `match_nodes(intent_text, max_matches=4)`：**两段式（KNN 取候选 → 候选内精排）**。候选由 label 与 alias **两个 KNN 分支**各取最近 `_candidate_budget(max_matches) = max(max_matches*4, 64)` 个节点（GiST `<->`）合并而成；随后在候选内按 `score = GREATEST(similarity(label), max(similarity(alias)))` 精排，`score < 0.05` 剔除，`ORDER BY score DESC, space_key, canonical_key` 取 `max_matches`；`match_kind` 为 `direct`（label 更优）或 `alias`（别名更优），`match_source="trigram"`。候选预算的**正确性依据**：节点若在 label 维度前 k 之外，就有 k 个节点 label 分更高、其 `GREATEST` 必然压制它（alias 维度同理），故 `k >= max_matches` 时 top-N 必落在并集内（4 倍余量覆盖并列）。**结果与全表扫描逐条一致**（`tests/integration/test_plugin_graph_recall.py` 保留全表实现作 oracle 对照）。**确定性**：同一意图、同一图谱状态 → 同一排序结果。

- `expand_to_capabilities(node_keys, expand_hops=1)`：单跳有界扩展，硬上限 `_EXPANSION_CAP = 8` 节点/次。(a) 域节点经 active `capability_contract` 桥边扩展 L2 能力节点；(b) 剩余容量内能力节点经空间内 `depends_on` 边（`valid_to IS NULL`）扩展同层能力节点；每条扩展结果携带 `source_node_key` 溯源。Graph 侧仅暴露只读接口，无任何写入口。

### 4.2 编排侧 `packages/plugin_topology/graph_planning.py` —— `GraphPlanningService`

串联「意图 → 图谱 → 规划」闭环，全部经 Policy 网关 + 幂等键 + 租户上下文：

- `plan_from_intent(request, trace_id)`：

  1. `_gate("topology.intent.plan", low/write_data)` fail-closed（未放行即 `PermissionError` → API 403）；
  2. `_intent_replay(idempotency_key)` —— 既有证据行命中直接重放（`idempotent=True`），零新写入；
  3. `match_nodes` + `expand_to_capabilities` 聚合 `_capability_nodes`（直接命中的能力节点 + 扩展出的能力节点，保留 match 证据）；
  4. `_resolve_requirements`：仅取 `node_type="capability"` 节点，经 `topology.blueprint_graph_links` 反查 `plugin_blueprints.capability_contract.capability` 能力 token 去重；**无任何能力节点或零需求 →** **`ValueError`** **fail-closed（API 400，零落库）**；
  5. `_plan_or_reuse`：`plan_key = "plan-"+sha256("m10-graph-plan:" + 排序后需求列表)[:16]`（确定性 key），委托 `TopologyService.plan()` 生成 plan\_only 计划；既有同 key 计划命中时复用并标记 `reused_plan`；
  6. `_record_intent`：单事务追加证据行（意图、匹配节点 JSON 证据、需求、计划引用、计数、trace），返回投影。

- `list_intents(limit)` / `get_intent(intent_id)`：经 `topology.intent.read`（read\_only）只读返回证据行投影（含复算 `reused_plan` 与 `plan_summary`）。

### 4.3 计划引用契约

M10 只**引用**既有 `TopologyService.plan()`（M1 已验收、`plan_only` 强制、无环 DAG 校验、计划版本锁定），不新增任何执行/调度面；规划结果后续可经既有 M3/M4/M6 链路（调用链物化 → 审批 → 受限演练运行）继续，但 M10 自身不触发这些环节。

## 5. 注册发布（registration.py）

`publish_topology_graph_planning_allow_policy(database_url, tenant_slug)`：把迁移 0048 种子为 **inactive** 的 `local-plugin-topology-graph-plan` 策略集置 `active`（仅 `topology.intent.plan` low/write\_data），并新增 CLI 开关 `--enable-graph-planning-policy`；不开启 AUTO、任意执行、网络访问或实时执行。

## 6. API 与桌面接入

- `POST /api/v1/topology/planning/intents`（先裁决 `topology.intent.plan`；`PermissionError`→403、无匹配/无链接蓝图 `ValueError`→400、幂等键重放→200 且 `idempotent=true`）；`GET /api/v1/topology/planning/intents?limit=`（裁决 `topology.intent.read`，最新在前）；`GET /api/v1/topology/planning/intents/{intent_id}`（裁决 `topology.intent.read`，详情含匹配证据）。三条完整路径（`intent.read` 与 `intent.plan`）全部 require\_policy 显式裁决。

- Electron IPC 白名单声明 `POST/GET /api/v1/topology/planning/intents` 与 `GET /api/v1/topology/planning/intents/{intent_id}`（uuid 正则），桌面经 `auditControl.request` 访问，不暴露任意子进程/调度入口。

## 7. 桌面只读 GUI（「图谱规划」标签页）

「拓扑目录」卡片新增「图谱规划」标签页：

- 顶部横幅明示「把中文业务意图经确定性图谱语义索引（pg\_trgm 打分 + 单跳有界扩展，零 LLM、零外网）解析为能力需求，复用既有规划器生成 plan\_only 计划。只规划、不执行；匹配失败或缺少链接蓝图时 fail-closed 拒绝，不落任何计划」。

- 意图输入（中文长文本，1–512 字符）+「生成不可执行计划」按钮（`planningIntentCanGenerate` 校验长度、后端经网关带幂等键）。

- 结果卡：`planningIntentEvidenceNote` 摘要（匹配 N 节点 · M 项能力需求 · 计划 key · 校验和前缀）+「匹配证据（图谱语义索引）」表——节点的类型（能力/域）、匹配方式（直接/别名/图遍历扩展）、来源（Trigram 打分/桥接/依赖），支持展开细化；历史意图列表加载失败时提示 `topology.intent.read` 尚未放行（fail-closed 403）。

- 模型层新增 `PlanningIntent`/`PlanningIntentListItem`/`PlanningIntentMatchedNode` 类型与 `planningIntentCanGenerate`/`planningIntentLengthHint`/`planningIntentMatchKindLabel`/`planningIntentMatchSourceLabel`/`planningIntentNodeTypeLabel`/`planningIntentEvidenceNote` 纯函数，配套 Vitest 单测。

## 8. 测试

- 契约 `tests/contract/test_plugin_topology_planning_intent_contract.py`：正例 + 上述反例（越界/枚举/伪造/非法 checksum/fail-closed）。

- 单元 `tests/unit/test_plugin_topology_graph_planning_unit.py`：scripted fake cursor 覆盖意图匹配阈值剔除、扩展拼接与预算封顶、需求解析去重、确定性 plan\_key 推导、幂等重放、fail-closed 门控。

- 集成 `tests/integration/test_plugin_topology_graph_planning_integration.py`：种子校验 → 中文意图 `总账质量校验并出具量化研究结论` 确定性匹配 2 节点并经 `depends_on` 扩展第 3 节点（证据含 `expanded/bridge`）→ plan\_only 计划落库且 checksum 与服务公式一致 → 同需求 `plan_key` 冲突复用既有计划 → 幂等键重放返回同一 `intent_id` → 计划经既有 M6 链物化可运行 → 受限只读演练真实子进程 → 扩展链在无受治理输入源的节点 fail-closed 停止 → RLS 跨租户不可见 → `planning_intents` 无 UPDATE/DELETE（psql 验证）→ API 403/422/404/400/幂等各分支。模块级 autouse fixture 保证 `local-plugin-topology-read` 策略集在每个用例前回到契约状态（避免跨用例策略干扰）。

- 回归压力修正：执行账本随 M6–M9 累积增长，M10 期间把 `list_executions` 读回上限与 API `limit`（`/chains/{chain_key}/executions`）放宽至 1000 / 2000，避免种子行被截断导致 flaky。

## 9. 验收对照

| # | 验收准则                                                                                             | 达成   |
| - | ------------------------------------------------------------------------------------------------ | ---- |
| 1 | 中文业务意图经确定性图谱匹配（pg\_trgm + depends\_on/桥接单跳，零 LLM/零外网）解析为能力需求                                     | ✅    |
| 2 | 能力需求映射回蓝图并复用既有规划器生成 plan\_only 计划；确定性 plan\_key 冲突复用既有计划                                         | ✅    |
| 3 | 每次规划意图落追加式 `planning_intents` 证据（匹配证据 + 计划引用 + 幂等重放）                                             | ✅    |
| 4 | `topology.intent.plan` 默认 inactive fail-closed；无匹配/无链接蓝图 400 零落库；`topology.intent.read` 经既有读策略放行 | ✅    |
| 5 | API 三端点 + Electron IPC 白名单；桌面只读「图谱规划」标签页                                                         | ✅    |
| 6 | 全量回归（Python / Ruff / Mypy / typecheck / Vitest / build）通过                                        | ✅ 见下 |

## 10. 全量回归（2026-09-08）

- 全量 Python **773 passed, 2 skipped**（两个默认关闭的真实 MinerU 集成）；

- Ruff（项目收敛规则集 `E4/E7/E9/F/I`）全仓通过（自动修复 4 处 I001 import 排序 + F401 未使用导入）；

- Mypy（packages 49 源）通过（修复 `graph_planning.py` 三处 nullable fetchone 索引与 dict.get 参数类型）；

- 桌面 TypeScript typecheck、Vitest（25/25）与生产构建均通过；

- 主库/测试库迁移头 `0048_graph_driven_planning`；实施计划与验收对照见本文档；M1–M10 全部完成，待下一阶段指示。

## 11. 后续演进（2026-09-22）：图谱被填满、多级展开、召回按需取用

M10 交付的是**骨架**：图谱里只有 3 个手工 capability 节点，召回打分全表扫描，而 AI 规划端点走的是全量目录。以下四项把它接到真实规模上。

| 步 | 内容 | 关键结论 |
| - | -- | ---- |
| S1 | 插件目录 → 图谱（域/族/能力三层 + `contains`/`depends_on` + 别名 + 幂等 upsert） | 四域灌图：L2 能力 **123**、族 **24**、`contains` 123、`depends_on` 80。`packages/graph/plugin_indexer.py` + `scripts/index-plugin-graph.py` |
| S3 | 多级展开（**多级 ≠ 多跳**） | `expand_hops` 保持 0/1（契约如此），改为**入口粒度**可切换：域→桥→能力；**族→`contains`→其下能力**；能力→`depends_on`→邻居。概略词「证据与底稿」命中族（0.556）后展开 8 个插件 |
| S2 | 召回可扩展性 | 改用 **GiST KNN**（`ORDER BY label <-> q LIMIT k`，迁移 `0067_graph_knn_gist_indexes`）：50 000 节点下 238–269 ms → **6.0–6.6 ms**，结果与全表逐条一致。**不能用 `%`**：RLS 下选择性估计塌成 1%，计划器弃用 GIN 索引（详见迁移 0067 与 `_candidate_budget` 注释） |
| S4 | 召回目录合流 | AI 规划端点改为**先图谱召回、再按需取契约**。audit 域提示词 **82 063 → 6 279–7 581 字符（约 11–13x）**；召回为空时回退全量，legacy 槽位永不裁剪（CW5 demo 与所有模板依赖它） |

S4 的落点：`workbench.recall_planning_directory()`（catalog 与 contracts 从**同一次筛选**派生，避免端口有目录无名契约）+ `apps/api/main.py::_planning_directory(intent_text=...)`。图谱不可达时降级为全量目录 —— 召回是优化，不是前置条件。

**仍未合并的一点**：`/topology/planning/ai` 与 `/canvas/chat*` 现在按召回收缩提示词，但仍走 `AiPlanner`；`GraphPlanningService`（`/topology/intent/plan`）是另一条独立路径。两条路的**目录来源已同源**，但编排入口尚未统一 —— 那是更大的重构，未做。

