# audit_network 项目长期记忆

## 环境基线（2026-09-14 实测）
- 数据库：本机原生 **PostgreSQL 16.13**（`DATABASE_URL` 指定，不依赖 Docker）。**PG16 服务常处 Stopped**：`Start-Service` 需管理员（沙箱内外都报"无法打开服务"）；`pg_ctl -D "C:\Program Files\PostgreSQL\16\data" start` 以当前用户能起，但**进程随工具会话结束被杀**（`0xC000013A`），不可当持久方案。**跑集成/全量 pytest 前先确认服务在跑。**
- 测试库：`postgresql://audit_app:admin@localhost:5432/audit_network_test`（RLS FORCE）；清理需 `audit_migrator`（该角色**无** BYPASSRLS）。
- 默认租户 `local-dev` = `613cece3-1831-4a1a-a7b0-a4129908faed`（旧记的 `a5526a1e-...` 已过期）。
- 解释器：`.venv\Scripts\python.exe`（**不要**用系统 Python 跑本项目）。脚本里的解释器/psql/扩展探测一律走 `scripts/env-common.ps1`，**不要再写死本机路径**。
- AI 通道：`AI_PROVIDER=openai_compat` → `https://api.commandcode.ai/provider/v1`，`AI_MODEL=deepseek/deepseek-v4.1-flash`；key 只从 `OPENAI_COMPAT_API_KEY` 读；该端点**必须带浏览器 UA**（否则 Cloudflare 1010）。换模型只改 `.env`。
- **已装 Ollama**（含 `qwen3_27b_iq3xxs_64k`）；**服务默认停**，停服时 3 个真实推理用例会跳过（属正常，非缺陷）。
- PG **16（服务端）** 与 **18** 并存，但 18 的 `bin/` 已卸载（只剩 `data/`）；`Resolve-PgTool` 按"PATH 优先→最高版本"解析，现落 16。边界：新客户端连旧服务端可以，反之 `pg_dump` 会以 "server version mismatch" 拒绝。

## 质量门与仓库卫生门禁
- `ruff check .` 全绿；`mypy packages` strict（117 文件）0 问题。CI = `.github/workflows/quality.yml`（pytest 全量 + ruff + mypy + web/desktop typecheck & vitest）。
- **全量 `pytest` 基线**（2026-09-14）：**2121 passed / 15 skipped / 0 failed（约 13–14 分钟）**。若在我方沙箱里出现 3 个 `test_audit_foundation_batch.py` 失败，是 WorkBuddy safe-delete 拦截 `finally: path.unlink()` 所致（沙箱旁路后 5 passed），**不是代码回归，别再追查**。
- 会话级清理已就位（`tests/conftest.py`）：`_experience_rows_do_not_accumulate`（经验表归零）+ `_policy_grants_do_not_leak` + `prune_dangling_suggestions`（悬空建议删、人工决策留）。**不要**当成"污染残留"排查。
- 改代码前先知道这些门禁存在：
  - `tests/contract/test_no_machine_local_paths.py`：入库代码禁止本机绝对路径（用户目录/ProgramData/Anaconda/pythonpro/数据盘）——注释除外，PS 块注释先剥离。
  - `tests/contract/test_source_encodings.py`：`.py` **不得**带 BOM；含非 ASCII 的 `.ps1`/`.psm1` **必须**带 BOM。
  - `tests/unit/test_render_layout.py`：`render.py` 布局契约 + 两个宿主样式表必须定义同一组渲染类。
  - `tests/contract/test_plugin_topology_contracts.py`：端口摘要必须是 schema 文件真哈希；已发布协议**不得**自报摘要。
- 遗留（低优先、当前无害）：5 处测试夹具仍用自洽 `"a"*64`；`test_plugin_runtime_gdrive_e2e.py` 硬编码 `G:/数据`（缺失即整模块跳过）。

## Windows PowerShell 硬性约束（写 .ps1 必读）
- **含中文的 `.ps1` 必须带 UTF-8 BOM**：PS 5.1 对无 BOM 脚本按 ANSI(GBK) 解码，吞引号 → **语法解析失败**（`verify-plugin-protocol-data.ps1` 曾因此完全无法运行）。
- `$ErrorActionPreference='Stop'` 下 `2>&1` 捕获原生命令 stderr → NativeCommandError 终止脚本；探测"预期失败"的命令必须走 `scripts/env-common.ps1` 的 `Invoke-NativeCommand`。
- `& $exe @("a","b")` **不是** splat；须 `$xs=@(...); & $exe @xs`。
- 函数返回空数组会展开为 `$null` → `@(f)` 得到含 `$null` 的 1 元素数组（"没缺"误判成"缺一个无名项"）。用 `@(f | Where-Object { $_ })`；**不要** `return ,@(...)`。
- 验证 .ps1 只能靠 `powershell.exe`（Bash 调被安全策略禁止）；**PowerShell 工具 stdout 不回传**，须 `| Out-File -Encoding utf8` 落盘再 Read。批量语法校验用 `[System.Management.Automation.Language.Parser]::ParseFile(...)`。
- `bootstrap.ps1 -Check` 为只读体检：能查出「库在、角色/扩展缺」的假通过（探 `pg_roles` + 目标库 `pg_extension`），未就绪 `exit 1`。`psql` 解析到 16 属正常。

## 组网执行链关键点
- 入口/闸门/编译器：`ai_planner/planner.py`（`AiPlanner.plan`）、`ai_planner/draft.py`（封闭 schema + 能力白名单 + 端口契约逐字段一致 + 数据边界）、`plugin_topology/compiler.py`（`compile_plan`）。
- 执行：`ports_executor.py` 的 `PortBoundExecutor`；产物索引与**种子注入**键都是 `(node_instance_id, port_id)`——计划里的节点名是什么，`seed_inputs` 就得用什么。
- **经验读路径仍未接入组网**：`ai_planner/` grep `experience|edge_stats|node_stats|read_overlay` 零命中。**写路径已接**（`start_plan_run` → `project_run_best_effort`，成功+异常两路径）。经验态图层目前只喂可视化与人工决策（L0：只改边粗细/颜色/排序，不改关系集合、不授权、不执行）。**把经验回流组网仍是未实现的下一步，不要写成已完成。**
- 通用约束：**经验只改变概率分布，不改变权限集合**——不得新增能力/端口、不得放宽数据边界、不得绕过闸门与策略网关。
- 组网效果验收脚手架：`tests/evaluation/test_ai_planning_intents.py` 有 ≥20 个固定意图（含 12 个负向、3 个显式恶意），用**注入式脚本模型**（设计如此，不做真实推理），可做开/关对照。
- **API 装配位缺陷（2026-09-14 实测，仍**未**修）**：`apps/api/main.py` 三处（~5817 `/topology/planning/ai`、~5867 `/canvas/chat`、~5946）用 `capability_catalog_from_db()` 且**未传 `port_contracts`** → 落到手写的 7 条 `PORT_CONTRACTS`（真实审计端口全报 `contract_mismatch`）。能力面仅 **2** 条 `RUNTIME_CAPABILITIES`。修法：改用 `workbench.domain_catalog(domain)` + `build_port_contracts(_specs_for(domain))`。
- **端口 `schema_sha256` 一律从 schema 文件派生，不得手写**（2026-09-14 定稿）：`catalog.PORT_CONTRACTS` 在**导入时**由 `_PORT_SCHEMA_REFS` + `composer.schema_sha256()` 生成；`_port_object()` 同此。被 `draft.py`/`planner.py`/`templates.py` 直接读取，必须自带真值——**别再引入"解析一次"的旁路函数**。
- 端口契约字段有重载：协议里是**带后缀文件名**（`document-content.schema.json`），`catalog` 注册表里是**裸名**（`document-content`）；`schema_sha256()` 已处理两种形式。
- `render.py`：`build_view` 的 `columns` 必须包含**域包不认识的阶段**（否则 `svg_fragment` KeyError）；`table_rows` 按 `view["columns"]` 排序而非 stage 键字典序（否则 audit 会把 base/gov 排到 s1 前）。
- `plan_key`/`node_instance_id` 曾可路径穿越，已由 `compile` 的标识符校验 + `_node_dir` 的 `resolve()/is_relative_to` 双层兜底修掉（有 46 个单测）。
- 域包：`contracts/domains/{audit,aiops,quant,knowledge}/pack.json` 四域齐，123 插件**0 多属、0 未覆盖**，`domain` 已从 `discover_plugins` → `nebula_graph` → API 端点全链路连通。

## 仓库分发状态
- GitHub：`github` = `2131614483/audit_network0913`；旧 `origin` = `audit-network0912`。**本机 git 直连 GitHub HTTPS 会失败**（schannel `CRYPT_E_NO_REVOCATION_CHECK`；`api.github.com` curl 返回 000），`-c http.schannelCheckRevoke=false` 也无效——看远端内容走 WebFetch。
- 相关文档：`docs/仓库可迁移性检查报告-20260913.md`（体检+整改）、`docs/独立化可迁移改造方案-20260912.md`、`docs/deepseek-harness-对比与接入评估-20260914.md`。

## 交付习惯
- 验证报告一律带：真实 run_id / trace_id / sha256、DB 直查证据、可复现命令、**未运行项**明确标注。
- 既有文档可能滞后（插件数、模型可用性、"命中 0 行"等），引用前先复核。

## 「财报编制」能力地图（做审计报告自动化必读）
- 插件总数 **123**：audit 107 / aiops 7 / quant 5 / knowledge 4；生命周期 100 verified / 23 contract_only。
- **现有节点覆盖"审计作业"（立项→风险→计划→实施→证据→底稿→发现→报告→整改），几乎不覆盖"财务报表编制"**。直接相关的约 30 个：multi-source-collect / finance-clean / biz-standardize / master-mapping / metric-compute / quality-check / rule-engine / report-frame-build / report-draft（260 行真 runtime）/ report-data-check / workpaper-reconcile / workpaper-review3 / e-signature / ocr-extract / evidence-archive / risk.* 等。
- **11 个缺口插件（按优先级）**：① `consolidation-workpaper` 合并工作底稿（最高优先，其余合并类产物的公共前置）② `note-schedule-build` 附注明细表 ③ `related-party-aggregate` ④ `trial-balance-map` ⑤ `cashflow-build` ⑥ `equity-statement-build` ⑦ `aging-analysis` ⑧ `regulatory-indicators` ⑨ `policy-library-match` ⑩ `disclosure-checklist` ⑪ `layout-render`。
- 上市公司年报结构（118 页实测）：目录 1 + 审计报告 5 + 报表 8 + 附注 98 + 资质附件 6；**附注占 83%**，其中「合并报表项目注释」46 页 +「关联方」15 页 = 52%。可按 **A 数据派生 / B 模板变量 / C 专业判断 / D 静态归档** 四型切分，机器可生成上限约 **90%**。
- **核心洞见**：**附注不是第二套数据源，而是主表数据按维度展开**。分录只要带 `产品/渠道/地区/客户/供应商/关联方/税种/资产类别/账龄/现金流项目` 维度标签，附注与主表就能 100% 自动勾稽。瓶颈不在数据可行性，**在从余额到披露表的中间层插件**。
- 参考实现（只读复算，未经 Policy Gateway）：`docs/茅台审计报告分析/sim/engine.py`（科目表+报表行映射+凭证+合并抵消）+ `report.py`（报表+附注+8 项勾稽）。

## 写代码时的硬约束与既有模式（2026-09-15 实测沉淀）
- **迁移 revision id 必须 ≤ 32 字符**：`alembic_version.version_num` 是 `VARCHAR(32)`。超长时 **DDL 会回滚、只在写版本号那步失败**（库保持干净），报 `StringDataRightTruncation`。现网最长 32（`0043_chain_run_exclusive_running`）。迁移一律 `alembic upgrade head`；**测试库要另跑** `DATABASE_URL=<migrator@test> python -m alembic upgrade head`（`migrations/env.py` 优先读 `DATABASE_URL`）。
- **`catalog.plugin_versions.plugin_id` 是 UUID 外键**（指向 `catalog.plugins.id`）。要按点号插件名取版本必须 `JOIN catalog.plugins ON plugins.id = plugin_versions.plugin_id` 并用 **`plugins.key`**；直接拿 UUID 当 plugin_id 会**静默得到 0 行**。
- **新增只读端点也要授权**：`require_policy("<能力>", tenant_id, trace_id, risk_class="read_only", side_effects="read_only")`，能力须由迁移种子策略集放行（照 `0028_local_knowledge_policy` 的 `ON CONFLICT (tenant_id,name,version) DO UPDATE` 写法），否则 fail-closed 403。
- **`discover_plugins(globs=..., domain=...)` 同时传会让 `domain` 静默失效**（globs 优先）。要按域筛，请在自己视图里按 `spec.domains` 过滤。
- **`build_port_contracts` 在 `packages/ai_planner/catalog.py`**（不在 composer）。
- **日志是旁路，不得阻塞启动**：`_configure_file_logging()` 原先无降级，`.data/api.log` 被占用时 `RotatingFileHandler` 抛 `PermissionError` → **`apps.api.main` 不可导入**（测试收集期即挂）。已改为降级 stderr + warning。
- **沙箱只读造成的三类假失败（非代码问题）**：`.pytest_cache`（加 `-p no:cacheprovider`）、`.mypy_cache`（加 `--cache-dir=.data/mypy_cache`）、`tsconfig.*.tsbuildinfo`（加 `--tsBuildInfoFile`）；`.data/api.log` 可能被其他进程占用。
- **⚠️ 已知阻塞（2026-09-15，非我方引入）**：`plugins/builtin/audit_network_skill_output/`（**未跟踪**，另一会话 2026-09-14 21:14 建）引用 **2 个不存在的 schema**（`network-result-set.schema.json`、`skill-output@1.0.0.schema.json`）。因 `schema_sha256()` fail-closed，`build_port_contracts(全部插件)` 抛 `ValueError` → **一个缺陷造成 46 个 unit + 9 个 contract 失败**。修好前不要把这类红灯归因到别处。
- **知识库向量口径已修为"按当前 embedder 模型"**（`stats()` 的 `embedding_chunks`/`embedding_model`、`list_documents()` 的 `embedding_chunks`）。`chunk_embeddings` 主键是 `chunk_id`（每块至多一条，`model_key` 只是"谁嵌的"标签），所以换模型重嵌是**替换**而非新增行。
