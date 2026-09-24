# audit_network 项目长期记忆

> 只放**仍在生效的硬约束与结论**。逐日过程、实测原始输出、完整论证见 `.workbuddy/memory/YYYY-MM-DD.md`。

## 环境基线
- 数据库：本机原生 **PostgreSQL 16.13**（`DATABASE_URL` 指定，不依赖 Docker）。**PG16 服务常处 Stopped**：`Start-Service` 需管理员（沙箱内外都报"无法打开服务"）；`pg_ctl -D "C:\Program Files\PostgreSQL\16\data" start` 以当前用户能起，但**进程随工具会话结束被杀**（`0xC000013A`）——**`run_in_background` bash 拉的 uvicorn 同样会被硬杀**（2026-09-23 实测：日志戛然止于 200 OK，之后 Electron 全部 ECONNREFUSED）；要进程存活必须让它当**当前脚本的子进程**。**跑集成/全量 pytest 前先确认服务在跑。**
- **⚠️ DSN 四处必须一致（2026-09-23 踩坑）**：`main.py` 的 `Settings` fallback、`.env`、`scripts/start-brain.ps1`、CI workflow 全用 `postgresql://audit_app:admin@localhost:5432/audit_network`。fallback 曾写 `audit_app:audit_app`（全仓唯一错值），`.env` 又没有 DATABASE_URL → 绕过 start-brain.ps1 启动 = 认证失败 = **49/70 个 GET 端点 500**。
- **⚠️ 本机 PG `lc_messages=Chinese (Simplified)_China.936`**：认证失败时 libpq 返回**中文错误消息**，psycopg2 用 UTF-8 解码 → 抛 `UnicodeDecodeError: byte 0xd6`，**不是** `psycopg2.Error` → 所有 `except psycopg2.Error` 兜底失效、真实原因被吞（表现为莫名 500）。`PGOPTIONS=-c lc_messages=C` 无效（认证阶段不生效）。诊断 DB 连接问题先用**正确密码**直连排除这一层。
- 测试库 `postgresql://audit_app:admin@localhost:5432/audit_network_test`（RLS FORCE）；**migrator 凭据 `audit_migrator:admin`**（该角色**无** BYPASSRLS，故测试库清理只能软删）。迁移一律 `alembic upgrade head`，**测试库要另跑**：`DATABASE_URL=postgresql://audit_migrator:admin@.../audit_network_test python -m alembic upgrade head`（`migrations/env.py` 优先读 `DATABASE_URL`）。
- 默认租户 `local-dev` = `613cece3-1831-4a1a-a7b0-a4129908faed`。
- 解释器：`.venv\Scripts\python.exe`（**不要**用系统 Python 跑本项目）。脚本里的解释器/psql/扩展探测一律走 `scripts/env-common.ps1`，**不要再写死本机路径**。
- AI 通道：`AI_PROVIDER=openai_compat` → `https://api.commandcode.ai/provider/v1`，`AI_MODEL=deepseek/deepseek-v4.1-flash`；key 只从 `OPENAI_COMPAT_API_KEY` 读；该端点**必须带浏览器 UA**（否则 Cloudflare 1010）。换模型只改 `.env`。
- **已装 Ollama**（含 `qwen3_27b_iq3xxs_64k`、embedding 通道 `qwen3-embedding:0.6b`）；**服务默认停**，停服时 3 个真实推理用例会跳过（属正常，非缺陷）。
- PG **16（服务端）** 与 **18** 并存，但 18 的 `bin/` 已卸载；`Resolve-PgTool` 按"PATH 优先→最高版本"解析，现落 16。新客户端连旧服务端可以，反之 `pg_dump` 报 "server version mismatch"。
- **本机 PG 连接开销 ~55ms**（`psycopg2.connect` 稳定 54.6–56.8ms；`localhost`/`127.0.0.1`/kwarg 三种写法一样，**不是 IPv6 回退**，疑似 scram PBKDF2）。项目里多处"每次调用新建连接"（`CapabilityGraphAdapter.match_nodes`、`GraphService.upsert_node`）→ 端到端耗时被连接开销主导（如 `match_nodes` 63ms 中 55ms 是连接）。**修法是连接池**，未做。

## 质量门与门禁
- **ruff 两个口径已统一、都全绿（2026-09-22）**：`ruff check .`（README/AGENTS.md 口径）与 CI 的 `ruff check apps packages tests` 结果一致。修法：把 `审计项目案例报告效果展示`、`desktop/src/public/flow-canvas-audit-pro` 加进 `pyproject.toml` 的 `exclude`（照 `cankao/` 惯例附理由），并 `--fix` 修掉 `docs/showcase-video/build_showcase.py` 的导入排序（它仍受 lint，是项目自己的构建脚本）。
- `mypy packages` strict **130 文件 0 问题**。CI = `.github/workflows/quality.yml`（pytest 全量 + ruff + mypy + web/desktop typecheck & vitest）。
- **✅ 全量 `pytest` 基线（2026-09-23，本轮修复后）：2218 passed / 12 skipped / **0 failed**，耗时约 25 分钟**。此前基线是 2199/12/**18 failed** —— 那 18 个已全部修完（详见 2026-09-23 日志），**不要再当"已知失败"放过**。桌面端同步：**vitest 9 文件 75 用例全通过 + `npm run typecheck` 0 错误**；`ruff check .` 全绿；`mypy packages --strict` 130 文件 0 问题。
- **⚠️ 跑全量 pytest 必须加 `--basetemp`（否则拿不到汇总）**：默认把 tmp 建在系统 Temp，收尾时 pytest 会把旧编号目录搬到 `pytest-of-<user>/garbage-<uuid>` 再集中删除，**684 个文件触发 WorkBuddy safe-delete 批量拦截**（`SAFE_DELETE_BULK_CONFIRM_REQUIRED`），进程在打印 `passed/failed` 汇总前就被终止，看起来像"测试崩了"。用 `--basetemp=".data/pytest-tmp-$(date +%s)"`（全新目录，pytest 不会在会话结束时删；**不要复用同名目录**，否则开头那次 `rmtree` 同样会被拦）。另：本机**没装 pytest-timeout**，别传 `--timeout=`。
- **A/B 对照标准手法**：`git stash push -- <具体文件列表>`（**不要**全量 stash：仓库有 1709 条归档删除记录，极慢），跑测试，再 `git stash pop`。`.gitattributes` 对 `.gitignore`/`apps/api/main.py` 提示 "CRLF will be replaced by LF" 属正常。
- **沙箱只读造成的假失败（非代码问题）**：`.pytest_cache`（加 `-p no:cacheprovider`）、`.mypy_cache`（`--cache-dir=.data/mypy_cache`）、`tsconfig.*.tsbuildinfo`（`--tsBuildInfoFile`）；`.data/api.log` 可能被占用。另有 3 个 `test_audit_foundation_batch.py` 失败源于 WorkBuddy safe-delete 拦截 `finally: path.unlink()`（沙箱旁路后 5 passed），**别再追查**。
- 会话级清理已就位（`tests/conftest.py`）：`_experience_rows_do_not_accumulate` + `_policy_grants_do_not_leak` + `prune_dangling_suggestions`。**不要**当成"污染残留"排查。
- 改代码前先知道这些门禁：`test_no_machine_local_paths.py`（入库代码禁本机绝对路径，注释除外，PS 块注释先剥离）；`test_source_encodings.py`（`.py` 不得带 BOM；含非 ASCII 的 `.ps1`/`.psm1` 必须带 BOM）；`test_render_layout.py`（`render.py` 布局契约 + 两宿主样式表同一组渲染类）；`test_plugin_topology_contracts.py`（端口摘要必须是 schema 文件真哈希，已发布协议不得自报摘要）。
- **⚠️ 资产根目录改名是"界面显空"的头号根因，改目录必查这四处（2026-09-23 踩坑）**：案例资产从 `审计项目案例/` 改名为 **`审计项目案例报告效果展示/`** 后，三个生产模块仍写死旧名 → **案例矩阵 / 文档资产索引 / 案例库页面全部静默为空**（不是服务没起、也不是权限）。已同步到：`packages/cases/matrix.py::CASES_ROOT`（并新增 `LEGACY_CASES_ROOTS` + `resolve_cases_root()` 双候选探测）、`packages/catalog/rules_registry.py::PROJECT_ROOT_DIR`、`packages/library/index.py::ROOTS` + `under_cases` 前缀判断、`apps/api/main.py` 文档字符串。**下次动这棵目录树，先 grep `审计项目案例`**。
  - **`case_matrix` 静默空列表是缺陷，已改为显式上报**：目录缺失时原来返回 `{"cases": []}`，UI 无从区分"没数据"与"路径错了"；现返回 `case_root` + `missing_root` 字段（对齐 library index 的 `missing_roots` 原则）。**只认带 `NN_` 阶段子目录的目录为案例** —— 该树下还混着运行日志归档 / Demo 原始数据 / 渲染构建器共 5 个非案例目录，不滤掉会伪装成 9 列全 0 的空案例行。
- **⚠️ 迁移 head 常量是周期性漂移点，四处必须一起改**（历史已多次：0051→0053→0062…）：`apps/api/main.py::EXPECTED_MIGRATION_HEAD`、`packages/ops/supervisor.py::EXPECTED_MIGRATION_HEAD`、`tests/integration/test_ops_supervisor.py`（已从 supervisor 导入而非硬编码）。过期后果是 `/api/v1/health/ready` 与运维检查把**已升级**的库报成不一致。**根治办法已经做掉一半**：`tests/migration/test_migration_chain.py` 改用 `alembic.script.ScriptDirectory.from_config(Config("alembic.ini")).get_current_head()` 动态推导，从此不再手改；产品侧两处仍是常量，靠 `test_health_ready`（比对象探针返回值与该常量）兜底漂移。
- **遗留（低优先无害）**：5 处测试夹具仍用自洽 `"a"*64`；`test_plugin_runtime_gdrive_e2e.py` 硬编码 `G:/数据`（缺失即整模块跳过）。

## Windows PowerShell 硬性约束（写 .ps1 必读）
- **含中文的 `.ps1` 必须带 UTF-8 BOM**：PS 5.1 对无 BOM 脚本按 ANSI(GBK) 解码，吞引号 → **语法解析失败**。**用 Edit/Write 工具写或改含中文的 `.ps1` 会丢 BOM**（2026-09-23 把 capture-all.ps1 改坏过）——改完必须用 Python 补 BOM，再用 `Parser::ParseFile` 校验。
- `$ErrorActionPreference='Stop'` 下 `2>&1` 捕获原生命令 stderr → NativeCommandError 终止脚本；探测"预期失败"的命令必须走 `Invoke-NativeCommand`（`scripts/env-common.ps1`）。
- `& $exe @("a","b")` **不是** splat；须 `$xs=@(...); & $exe @xs`。
- 函数返回空数组会展开为 `$null` → `@(f)` 得到含 `$null` 的 1 元素数组（"没缺"误判成"缺一个无名项"）。用 `@(f | Where-Object { $_ })`；**不要** `return ,@(...)`。
- 验证 .ps1 只能靠 `powershell.exe`（Bash 调被安全策略禁止）；**PowerShell 工具 stdout 不回传**，须 `| Out-File -Encoding utf8` 落盘再 Read。批量语法校验用 `[System.Management.Automation.Language.Parser]::ParseFile(...)`。
- `bootstrap.ps1 -Check` 是只读体检：能查出「库在、角色/扩展缺」的假通过（探 `pg_roles` + 目标库 `pg_extension`），未就绪 `exit 1`。

## 界面架构（2026-09-22 统一后：单窗口）
- **唯一入口 = 主控台**（`desktop/`，Electron + React，31 页 + 新增第 9 组「可视化与演示」5 项）。启动链：`启动审计智能中枢.bat` → `scripts/start-desktop.ps1` → Electron。
- **主控台内嵌的可视化视图用 iframe 承载**：`App.tsx` 的 `SHOWCASE_VIEWS`（view key → **相对**路径 `./showcase/xxx.html`）+ `components/ShowcaseFrame.tsx`。资源在 `desktop/src/public/`：`knowledge-nebula.html`（实时功能，留在根）与 `showcase/`（演示类）。
- **iframe 里没有 preload**（`window.auditControl` 不存在）→ 需要后端数据的视图经 **postMessage 桥**借用父窗口（父侧在 `App.tsx` 的 `audit-network-embed`，子侧 shim 在 `knowledge-nebula.html`）。**不要**为此打开 `nodeIntegrationInSubFrames` —— 那会把 preload 注入每一个子框架，违背最小权限基调。
- **`electron/main.ts` 的独立窗口函数与 `AUDIT_NETWORK_OPEN_*` / `AUDIT_NETWORK_CAPTURE_*` 全部保留**：它们不只是界面，还是自动化截图工具（FLOW 还支持 seek 截两帧）。收编只改了前端的默认入口。
- **`renderer/index.html` 不得加 `<base>`**（会破坏 iframe 相对路径约定）；`src/public/**` 由 electron-vite 复制到 `out/renderer/**`，改完记得 build 后核验复制结果。
- **⚠️ `web/` 必须留在原位，不要归档（2026-09-22 踩过，且被真实启动暴露）**：它**不是零引用** —— `apps/api/main.py:7752` 用 `web_root = Path(__file__).resolve().parents[2] / "web"` 挂载为静态目录 `/web`，而且**根路径 `/` 返回 `web/index.html`**；`tests/unit/test_gui_shell.py` 断言 `src="/web/app.js"` 与 `GET /web/app.js`。把它移走会让 `create_app()` 直接抛 `RuntimeError: Directory ... does not exist` → **API 完全起不来、全量测试大面积失败**。**教训：查"是否零引用"要搜目录名与变量名，不能只搜字面路径**（我当初搜 `web/index.html` 得 0 命中，漏掉了 `web_root`）。
- **归档位与必读 README**：`prototypes/legacy-pages/`（演示页历史版本 + 与产品逐字节重复的 `audit-brain` 源；`audit-brain`/`plugin-flow-showcase` 已用目录名复核确认**确实零引用**）、`desktop/src/public/showcase/README.md`。**改界面前先读这两处**。
- `pyproject.toml` 的 ruff `exclude` 含 `desktop/src/public/showcase` —— **目录移动后必须同步**，否则里面的一次性 py 脚本会重新触发 lint。
- **⚠️ Electron 控制平面白名单 `allowedPaths`（`desktop/electron/main.ts`）必须与渲染层所有 `path:` 声明保持同步（2026-09-23 踩坑，空界面根因）**：主进程 `controlPlaneRequest` 走 allow-list 模式（`allowedPaths` 显式 `Set` + `isAllowedControlPlanePath()` 内一组段式正则），**未声明的路径在到达 API 前就被拒**，渲染层 catch 后统一显空（表盘 "—" / 整块空白），外观与"服务没起"一致。API 新增端点（observability/experience/topology-evidence/search/cases/library/rules/connectivity/health·ready/plugins·lifecycle·versions…）后必须**同步加白名单**，否则该页显空。护栏测试 `desktop/src/model/controlPlaneAllowList.test.ts` 遍历 `src/` + `main.ts` 所有静态 `path:` 断言可达，防漂移。诊断错误带 `${request.path}`（"桌面端拒绝未声明的控制平面接口：<path>"）。
- **截图验证三要点（2026-09-23 固化，复用时照做）**：① **API 必须作为 Electron 启动命令的子进程存活**（`run_in_background` bash 起的 uvicorn 会话结束即被硬杀 → 全 ECONNREFUSED）；② 视图路由用 `AUDIT_NETWORK_CAPTURE_VIEW="view=xxx"`（带 `view=` 前缀），`captureView="audit"` 会被 `viewQuery()` 切错回落 Hub；③ `AUDIT_NETWORK_CAPTURE_SETTLE_MS` 用 **6000–9000ms**（默认 1.2s 时子页面在 connection=online 后才 fetch，指标仍 "—"）。

## 组网执行链
- 入口/闸门/编译器：`ai_planner/planner.py`（`AiPlanner.plan`）、`ai_planner/draft.py`（封闭 schema + 能力白名单 + 端口契约逐字段一致 + 数据边界）、`plugin_topology/compiler.py`（`compile_plan`）。
- 执行：`ports_executor.py::PortBoundExecutor`；产物索引与**种子注入**键都是 `(node_instance_id, port_id)` —— 计划里节点名是什么，`seed_inputs` 就得用什么。
- **经验读路径仍未接入组网**：`ai_planner/` grep `experience|edge_stats|node_stats|read_overlay` 零命中。**写路径已接**（`start_plan_run` → `project_run_best_effort`）。经验态图层目前只喂可视化与人工决策（L0：只改边粗细/颜色/排序）。**把经验回流组网仍是未实现的下一步，不要写成已完成。**
- 通用约束：**经验只改变概率分布，不改变权限集合** —— 不得新增能力/端口、不得放宽数据边界、不得绕过闸门与策略网关。
- 验收脚手架：`tests/evaluation/test_ai_planning_intents.py` 有 ≥20 个固定意图（12 负向 + 3 显式恶意），用**注入式脚本模型**（设计如此，不做真实推理），可做开/关对照。
- **✅ API 端口契约缺陷已修（2026-09-22）—— 当时是「三层断线」**：`apps/api/main.py` 三处端点（`/topology/planning/ai`、`/topology/canvas/chat`、`/chat/stream`）原先调 `capability_catalog_from_db()` 不传 `port_contracts`；`planner.py::_build_prompt` 不接受它、内部 `recall_snapshot` 硬引用模块级 7 条 `PORT_CONTRACTS`；`chat.py::CanvasChatPlanner.chat` 也不透传。现已改为共用的 `_planning_directory()`（**并集**两代目录，catalog 与 registry **成对返回**）。实测 audit 域提示词端口契约 **2 → 231 条**，aiops **0 → 17 条**。
- **关键约束**：`_validate_port_contract` 只比较 registry 里**实际登记**的字段（遍历 `registered.items()`），所以目录派生的 6 字段契约与手写 8 字段契约**可共存**；但**提示词与校验必须同一个 registry**。
- **两代端口命名并存（迁移未完成，改代码必知）**：同一插件 `audit.ledger-quality` —— legacy 手写目录里端口是 `ledger`→`candidates`，插件目录里是 `ledger-artifact-ref`→`audit-quality-candidates`；`schema_ref` 也有两种拼法（协议带后缀 `xxx.schema.json`，catalog 注册表用裸名），`composer.schema_sha256()` 两种都认。**直接"改用 domain_catalog"会打断 legacy**（TEMPLATES/既有草稿/证据包依赖旧端口名）→ 端点采用**并集**而非替换。
- **端口 `schema_sha256` 一律从 schema 文件派生，不得手写**：`catalog.PORT_CONTRACTS` 在**导入时**由 `_PORT_SCHEMA_REFS` + `composer.schema_sha256()` 生成，被 `draft.py`/`planner.py`/`templates.py` 直接读取 —— **别再引入"解析一次"的旁路函数**。
- `render.py`：`build_view` 的 `columns` 必须包含**域包不认识的阶段**（否则 `svg_fragment` KeyError）；`table_rows` 按 `view["columns"]` 排序而非 stage 键字典序。
- `plan_key`/`node_instance_id` 曾可路径穿越，已由 `compile` 标识符校验 + `_node_dir` 的 `resolve()/is_relative_to` 双层兜底修掉（46 个单测）。
- 域包：`contracts/domains/{audit,aiops,quant,knowledge}/pack.json` 四域齐，123 插件 0 多属 0 未覆盖，`domain` 全链路连通。

## 知识图谱召回与组网（M10 + S1/S2/S3）
- **用户目标（务必按此方向）**：插件组网走**知识图谱多级展开**，为 **100 000+ 插件**设计。**不是**全量塞提示词 —— 107 个插件的能力+契约全量入提示词实测 **8.1 万字符（约 2–3 万 token）**，10 万级不可行。
- **已有骨架（别推倒重来）**：`graph_planning.py::CapabilityGraphAdapter` 用 pg_trgm 对 `label` + `node_aliases` 打分（**无 LLM、无网络、确定性排序**），再 `expand_to_capabilities` **有界一跳**（`_EXPANSION_CAP=8`，每条带 `source_node_key`）；`topology.blueprint_graph_links` 做图↔蓝图回连；`CapabilityGraphPort` 是 Protocol，可换 embedding matcher。
- **推进状态：S1 ✅ + S2 ✅ + S3 ✅ + 蓝图闭环 ✅ + S4 ✅（2026-09-22）**。剩余的是两个独立优化项，见本节末。
- **S4 召回目录合流（已完成）**：`workbench.recall_planning_directory(db, domain, intent)` 只把召回到的能力与其端口契约交给模型；`_planning_directory(..., intent_text=...)` 三端点（`/topology/planning/ai`、`/canvas/chat`、`/chat/stream`）全部接上。实测 audit 域提示词 **82 063 → 6 279–7 581 字符（11–13x）**。三条硬规则：① **catalog 与 contracts 必须从同一次筛选派生** —— 提示词由 contracts 生成、而校验要求复制 catalog 里每个端口的契约，两次独立筛选会造出「目录有端口、契约没有」，模型看不到却被要求复制 → 必然 `contract_mismatch`；② **召回为空回退全量**（空目录只能规划失败；空是可识别返回值，不静默）；③ **legacy 半边永不裁剪**（CW5 demo 与所有 TEMPLATES 依赖 legacy 端口名；实测主库 legacy 仅 **2 条** —— `capability_catalog_from_db` 只收 `capability_contract.capability` **非空**的蓝图，S1 注册的 100 个插件蓝图没这个键）。图谱不可达时降级全量（**召回是优化，不是前置条件**）。测试 `tests/integration/test_plugin_graph_recall_directory.py`（8 个）。
- **仍未做（三个独立项）**：① **编排入口统一** —— `/topology/planning/ai`+`/canvas/chat*` 走 `AiPlanner`、`/topology/intent/plan` 走 `GraphPlanningService`；**目录来源已同源，编排入口还没合并**；② **连接池**（本机连接开销 55ms，见环境基线）；③ **长意图召回偏窄**（S4 实测暴露）：用户 goal 是长自然语言句（13 字），图谱标签是短词组（6 字），而 pg_trgm 的 `similarity` 分母含查询长度 → 长意图最高分只有 **0.105**、只召回 2 条。改用 **`word_similarity`** 实测明显更好（0.105→0.286 / 0.167→0.375 / 0.188→0.429，top-1 一致），**但属破坏性变更**：阈值要重新校准（`>=0.30` 当前命中 0 个，需降到 ~0.2）、M10/S3 写死分数的断言（如族 0.556）要全改、还需验证 `<->>` 的 GiST KNN 是否同样走索引。宜独立一轮。
  - **S4 = 让 `/topology/planning/ai`、`/canvas/chat*` 先图谱召回、再按需取契约**，替代当前的 `domain_catalog` 全量（8.1 万字符）。两条召回路径目前仍未合流：AI 规划端点走 `AiPlanner`+全量目录；而 `apps/api/main.py`（`GraphPlanningService` + `CapabilityGraphAdapter`）才是可扩展的那条。
- **S1 建图**：`packages/graph/plugin_indexer.py::index_domain()` + `scripts/index-plugin-graph.py`（幂等 CLI，`--domain` 可重复）+ `tests/integration/test_plugin_graph_index.py`。四域灌图后：**L2 capability 123 / capability_family 24 / L3 domain 5 / contains 123 / depends_on 80 / active 桥规则 4**，单域 ≤0.34s。分层：`<domain>-l3`(L3) 放域节点；`capability-l2`(L2) 放族与能力；族由 **`pack.classify(plugin_id)` → (layer, stage)** 决定（`biz`→s1–s8、`base`→`support_segment`、`gov`→`governance_segment`）。域→能力复用 `capability_contract` 桥（未新增 relation_type）。
- **S1 的 4 个必知坑（都已修）**：① `execute_values(fetch=True)` 的 `RETURNING` **顺序不可靠** —— 按位置 zip 会让整张图的边接错节点，必须插入后单独 `SELECT` 回查；② `audit_app` 无 DELETE 权限，收敛只能靠 `valid_to` 软失效；③ **`contains` 与 `depends_on` 同步语义必须分开** —— 对 `depends_on` 做"软失效非推导边"会清掉 0048 人工建的 `ledger.validate→finding.draft`（治理声明），故 `depends_on` 只增不删；④ **node `label` 只填空不覆盖**，否则盖掉人工中文名并使 `match_kind` 从 `direct` 退化 `alias`。
- **S3 多级展开：多级 ≠ 多跳**。`expand_hops` **保持 0/1**（单测要求 `=2` 抛 `ValueError`），改为**入口粒度**可切换：L3 域 --`capability_contract`桥--> 能力；**L2 族 --`contains`--> 其下能力**；L2 能力 --`depends_on`--> 相邻能力。改动：`_MATCH_NODE_TYPES` 加 `capability_family`、`_EXPANSION_RELATIONS = ("depends_on","contains")`、把 `e.relation_type` 写进 `match_source`。实测概略词「证据与底稿」→族 0.556→展开 8 插件；精确词「底稿三级复核」→1.000 直达插件。**改匹配层安全**：`_resolve_requirements` 显式过滤 `node_type=='capability'`，族只作 evidence。
- **S2 召回可扩展性 —— 结论反直觉，改这条路径前必读**：
  - **`%` 操作符方案不可用**：`similarity(a,b) >= x` 可写成 `a % b`（GIN，0048 已建索引），但**计划器需要选择性估计才肯选它**。`graph.nodes` 受 RLS 保护，以**非 superuser 应用角色**执行时估计**塌成表行数的 1 %**（50 000 节点实测：真实 7 行、估 1–509 行）→ **放弃索引改全表扫描**。`SET ROLE audit_app` 可复现；`GRANT SELECT ON pg_statistic` **无效**（已排除权限）；`VACUUM` 也无关。**别再试图用 `%` 修这个。**
  - **正解 = GiST + KNN**：`ORDER BY label <-> q LIMIT k` 走 `gist_trgm_ops`，按距离有序扫、到 k 即停，**不需要选择性估计**，故 RLS 下照样走索引。迁移 `0067_graph_knn_gist_indexes` 新增 `graph_nodes_label_gist_trgm_idx` / `graph_node_aliases_alias_gist_trgm_idx`（**GIN 保留**，M10 契约测试断言其存在）。实测 50 000 节点、`audit_app`+RLS：**238–269ms → 6.0–6.6ms（约 40x）**，与 oracle 逐条一致。
  - **两条陷阱**：① **有界 KNN 会失效** —— `WHERE label <-> q <= 0.95 ORDER BY label <-> q` 语义**精确等价** `similarity >= 0.05`（实测零差异），但计划器又会因有可估的过滤条件而放弃 GiST。**必须用无界 `ORDER BY <-> LIMIT k`，外层再复核阈值**。② **候选来源必须两支**：label 与 alias 各走一次 KNN；只剪 label 会**静默丢掉全部别名匹配**。
  - **正确性论证**：`k >= max_matches` 即可 —— 节点若在 label 维度前 k 之外，就有 k 个节点 label 分更高、`GREATEST` 必然压制它；alias 同理。实现取 `max(max_matches*4, 64)`，**4 倍余量是为了并列（tie）**。该不变式有专门测试。
  - 交付物：`_CANDIDATE_SQL`（KNN 两段式）+ `_candidate_budget`；**`_bind_similarity_threshold` 已删除**（KNN 不依赖 `pg_trgm.similarity_threshold` GUC，"默认 0.3 vs 需要 0.05"的静默漏召回陷阱随之消失）；`tests/integration/test_plugin_graph_recall.py`（27 契约测试，**保留优化前实现作 oracle** 逐条对比）。
  - **⚠️ 在测试库跑建图会影响 M10 既有集成测试**（它隐含假设图谱只有自己 seed 的 3 个 legacy 节点）。
- **蓝图注册（`packages/plugin_topology/blueprint_indexer.py::index_blueprints()`，CLI 现在同时做图谱+蓝图）**：
  - **⚠️ 铁律：只注册 `verified` 插件**。第一版注册全量导致 planner 按字典序选了 `audit.ledger-quality`（`contract_only`，无实现）而放弃可执行的 `ledger-quality-slot` → 隔离执行失败。**图谱收全量契约（100+23），蓝图只收可执行子集（audit 100/107）**。误注册用 `status='archived'` 软下架。
  - 另 3 条硬约束：① `status` CHECK 只有 `planned/released/archived`（**没有 `active`**）；② **每个 blueprint 必须至少属于一个集群**（否则 `TopologyPlanner.plan` 直接跳过）；③ `blueprint_graph_links` 外键指向 `plugin_blueprints(tenant_id,key)` → **先建 blueprint 再建 link**。
  - 写入 `plugin_clusters` / `plugin_blueprints` / `cluster_memberships` / `topology_edges` / `blueprint_graph_links`，全幂等。**端到端闭环已通**：主库「底稿三级复核」→ `plan_only` 4 节点；「证据与底稿」→ 6 节点 + 2 条 DAG 边（此前都 fail-closed）。
  - **注册会改变 M10 既有测试预期**（能力目录 3 → 100+）：已适配 8 处（`==`→`<=` 包含语义、显式构造 plans、不变式断言、`_SEED_CHAIN_KEY` 改 `startswith("chain-")`）。

## 写代码时的硬约束与既有模式
- **迁移 revision id 必须 ≤ 32 字符**（`alembic_version.version_num` 是 `VARCHAR(32)`）；超长时 DDL 回滚、只在写版本号那步失败（报 `StringDataRightTruncation`）。
- **`catalog.plugin_versions.plugin_id` 是 UUID 外键**（指向 `catalog.plugins.id`）。要按点号插件名取版本必须 `JOIN catalog.plugins ON plugins.id = plugin_versions.plugin_id` 并用 **`plugins.key`**；直接拿 UUID 当 plugin_id 会**静默得到 0 行**。
- **新增只读端点也要授权**：`require_policy("<能力>", tenant_id, trace_id, risk_class="read_only", side_effects="read_only")`，能力须由迁移种子策略集放行（照 `0028_local_knowledge_policy` 的 `ON CONFLICT (tenant_id,name,version) DO UPDATE` 写法），否则 fail-closed 403。
- **`discover_plugins(globs=..., domain=...)` 同时传会让 `domain` 静默失效**（globs 优先）→ 按域筛请自己按 `spec.domains` 过滤。
- **`build_port_contracts` 在 `packages/ai_planner/catalog.py`**（不在 composer）。
- **日志是旁路，不得阻塞启动**：`_configure_file_logging()` 原先无降级，`.data/api.log` 被占用时 `RotatingFileHandler` 抛 `PermissionError` → `apps.api.main` 不可导入（测试收集期即挂）。已改为降级 stderr + warning。
- **✅ 野插件已隔离（用户选定，2026-09-22）**：`audit_network_skill_output/` 由 `git mv` 移到 **`prototypes/pending-plugins/audit_network_skill_output/`**（附 README 说明来源与恢复步骤），磁盘内容一字未改。原因：它不在正式注册清单（`test_plugin_layout.py` 报 `on disk but not allow-listed`），且声明却不存在的 2 个 schema 经 `schema_sha256()` fail-closed，让 `build_port_contracts(_specs_for("audit"))` 整体抛 `ValueError`（曾造成 5 unit + 1 contract 红灯；移走后 **387 passed**，audit 域恢复 **107 插件 / 161 端口 / 0 冲突**）。它声明的 2 个测试**从未存在**；要正式接纳需补 schema + 补测试 + 加 IDS 清单。

## 「财报编制」能力地图（做审计报告自动化必读）
- 插件总数 **123**：audit 107 / aiops 7 / quant 5 / knowledge 4；生命周期 100 verified / 23 contract_only。
- **现有节点覆盖"审计作业"（立项→风险→计划→实施→证据→底稿→发现→报告→整改），几乎不覆盖"财务报表编制"**。直接相关约 30 个：multi-source-collect / finance-clean / biz-standardize / master-mapping / metric-compute / quality-check / rule-engine / report-frame-build / report-draft / report-data-check / workpaper-reconcile / workpaper-review3 / e-signature / ocr-extract / evidence-archive / risk.* 等。
- **11 个缺口插件（按优先级）**：① `consolidation-workpaper` ② `note-schedule-build` ③ `related-party-aggregate` ④ `trial-balance-map` ⑤ `cashflow-build` ⑥ `equity-statement-build` ⑦ `aging-analysis` ⑧ `regulatory-indicators` ⑨ `policy-library-match` ⑩ `disclosure-checklist` ⑪ `layout-render`。
- 上市公司年报结构（118 页实测）：目录 1 + 审计报告 5 + 报表 8 + 附注 98 + 资质附件 6；**附注占 83%**（「合并报表项目注释」46 页 +「关联方」15 页 = 52%）。可按 A 数据派生 / B 模板变量 / C 专业判断 / D 静态归档 四型切分，机器可生成上限约 **90%**。
- **核心洞见**：**附注不是第二套数据源，而是主表数据按维度展开**。分录只要带 `产品/渠道/地区/客户/供应商/关联方/税种/资产类别/账龄/现金流项目` 维度标签，附注与主表就能 100% 自动勾稽。瓶颈不在数据可行性，**在从余额到披露表的中间层插件**。
- 参考实现（只读复算，未经 Policy Gateway）：`docs/茅台审计报告分析/sim/engine.py` + `report.py`。

## 仓库分发状态
- **✅ 重复归档已处置（2026-09-22）**：`.gitignore` 规则改为实际路径 `审计项目案例报告效果展示/全流程运行日志-20260912-完整归档/`（原写 `docs/...`，从未匹配），并 `git rm -r --cached` → **索引 −1709 文件**（5153 → 3444），**磁盘 1712 文件原样保留**；主归档未受影响。判据：副本目录 mtime `2026-09-12 14:47` 与原注释吻合，且**多套了一层同名目录**。
- 复查 `git check-ignore` 必须加 **`--no-index`**（默认跳过已跟踪文件，会误判"规则没生效"）。
- GitHub：`github` = `2131614483/audit_network0913`；旧 `origin` = `audit-network0912`。**本机 git 直连 GitHub HTTPS 会失败**（schannel `CRYPT_E_NO_REVOCATION_CHECK`），看远端内容走 WebFetch。
- 相关文档：`docs/仓库可迁移性检查报告-20260913.md`、`docs/独立化可迁移改造方案-20260912.md`、`docs/deepseek-harness-对比与接入评估-20260914.md`。

## 交付习惯
- 验证报告一律带：真实 run_id / trace_id / sha256、DB 直查证据、可复现命令、**未运行项**明确标注。
- 既有文档可能滞后（插件数、模型可用性、迁移 head、"命中 0 行"等），引用前先复核。
- 性能与正确性结论一律以**实测**为准：本轮 S2 就是因为"照直觉改 `%`"被 PG 的计划器行为推翻两次，最终靠造 5 万节点数据 + `EXPLAIN` 逐项定位。
