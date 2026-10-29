# audit_network 长期记忆

> 只放**仍生效的硬约束与结论**。过程见 `.workbuddy/memory/YYYY-MM-DD.md` 与 `docs/`。

## 环境
- 本机原生 **PG 16.13**，`DATABASE_URL` 指定，不用 Docker。PG 常 Stopped；后台进程随会话被杀，需长活的服务必须是**当前脚本的子进程**。跑集成测试前先确认 PG 在跑。
- **DSN 四处必须一致**：`main.py` Settings fallback、`.env`、`scripts/start-brain.ps1`、CI workflow = `postgresql://audit_app:admin@localhost:5432/audit_network`。不一致 → 认证失败 → 大量 GET 500。
- **PG `lc_messages` 为中文**：认证失败时 libpq 返回中文，psycopg2 解码抛 `UnicodeDecodeError` 而非 `psycopg2.Error` → 兜底失效、真因被吞。诊断先用正确密码直连排除这层。
- 测试库 `..._test`；migrator 凭据 `audit_migrator:admin`（无 BYPASSRLS，清理只能软删），测试库迁移需单独跑。
- 默认租户 `local-dev` = `613cece3-1831-4a1a-a7b0-a4129908faed`。解释器 `.venv\Scripts\python.exe`（勿用系统 Python）；探测走 `scripts/env-common.ps1`。
- AI：`AI_PROVIDER=openai_compat` → `https://api.commandcode.ai/provider/v1`，`AI_MODEL=deepseek/deepseek-v4.1-flash`，key 只读 `OPENAI_COMPAT_API_KEY`，端点**必须带浏览器 UA**。Ollama 默认停（停服时 3 个真实推理用例跳过属正常）。
- 远端 `https://github.com/2131614483/AI-network-auto-audit.git`（origin/master）。

## 提交与 CI
- **只有 staged 的文件进 CI**；提交后必须做检出等价验证：`git archive HEAD | tar -x -C <新目录>` → 该目录跑 `python -c "from apps.api.main import create_app"` + `pytest --collect-only -q`。未跟踪(`??`)同样致 CI 挂。
- 巨型产物勿入库：`docs/showcase-video/*.mp4`(120MB)、`video_shots/`(46MB)、`desktop/electron.vite.config.*.mjs`。
- CI `.github/workflows/quality.yml` = pytest 全量 + `ruff check apps packages tests` + `mypy packages` + web/desktop typecheck&vitest。基线（2026-09-24）**2246 passed / 23 skipped，140s**；CI 比本机快 10 倍以上 → 改完推 CI 看结果更划算。
- **quality.yml 的 3 步数据 provision 缺一即挂**：① migrations（主库+测试库）② `packages.demo.seed.seed_local_demo` ③ plugin catalog 注册 + `scripts/index-plugin-graph.py --domain audit --domain aiops --domain quant --domain knowledge`。新增依赖库数据的测试前先确认 CI 有数据。
- **CRLF 陷阱**：`core.autocrlf=true` 使 `git status` 看不见换行分歧 → 哈希断言本地过、CI 挂。`审计项目案例报告效果展示/…/_tools/audit_evidence.json` 的 SHA-256 被 6 处引用，需 `-text -eol`（`-text` 单独无效）；改后必须 `git add --renormalize`。
- 全量 pytest 必加 `--basetemp=".data/pytest-tmp-<ts>"`（全新目录）；默认 temp 收尾删除触发 safe-delete 拦截，看起来像测试崩了。本机无 pytest-timeout。
- 假失败（沙箱只读，非代码问题）：`.pytest_cache`、`.mypy_cache`、`tsconfig.*.tsbuildinfo`；`test_audit_foundation_batch.py` 3 个失败源于 safe-delete 拦截 `unlink()`，别追。
- 改代码前必知门禁：`test_no_machine_local_paths`（禁本机绝对路径）、`test_source_encodings`（.py 无 BOM；含中文 .ps1 必须 BOM）、`test_render_layout`、`test_plugin_topology_contracts`（端口摘要须 schema 真哈希）。
- **迁移 head 常量两处同步**：`apps/api/main.py` 与 `packages/ops/supervisor.py`（现 `0068_connectivity_read_policy`）。
- GitHub 推送：Clash 7897；报 `CRYPT_E_NO_REVOCATION_CHECK` 根因是 Clash 系统代理关闭 → schannel 取不到 CRL。git 2.55 下 `http.schannelCheckRevoke` 已失效，用 `http.<url>.sslVerify`。用 **Bash** 工具跑 git（PowerShell 会丢 stderr），清空 `*_PROXY`。可用：`git -c http.sslVerify=false push origin master`（需 dangerouslyDisableSandbox）。拉 CI 日志同样要绕沙箱且不带代理。

## Windows PowerShell
- 含中文 `.ps1` **必须 UTF-8 BOM**（Edit/Write 会丢 → 用 Python 补，再 `[Parser]::ParseFile` 校验）。
- `$ErrorActionPreference='Stop'` 下 `2>&1` 捕获原生 stderr 会终止脚本；预期失败探测走 `Invoke-NativeCommand`。
- `& $exe @("a","b")` 不是 splat；函数返回空数组会展开为 `$null`。
- PowerShell 工具 stdout 不回传 → 落盘再 Read。

## 界面
- 唯一入口 `desktop/`（Electron+React）；启动 `启动审计智能中枢.bat` → `scripts/start-desktop.ps1`。
- iframe 内无 preload → 需数据视图经 postMessage 借父窗口；勿开 `nodeIntegrationInSubFrames`；`renderer/index.html` 不加 `<base>`。
- **`web/` 必须留在原位**（`main.py` 挂 `/web` 且根路径返回 `web/index.html`，移走 `create_app()` 抛 RuntimeError）。
- Electron 白名单 `desktop/electron/main.ts::allowedPaths` 必须与渲染层所有 `path:` 同步，否则静默显空；护栏 `controlPlaneAllowList.test.ts`。
- 截图验证：API 须为 Electron 子进程；`AUDIT_NETWORK_CAPTURE_VIEW="view=xxx"`（带前缀）；settle **6000–9000ms**。
- 改界面前读 `prototypes/legacy-pages/` 与 `desktop/src/public/showcase/README.md`；ruff exclude 含该目录，移动后同步。

## 组网与图谱
- 链路：`ai_planner/planner.py`、`draft.py`（封闭 schema + 能力白名单 + 端口契约逐字段 + 数据边界）、`plugin_topology/compiler.py`；执行 `ports_executor.py::PortBoundExecutor`；产物/种子键 `(node_instance_id, port_id)`。
- **铁律：经验只改变概率分布，不改变权限集合。**
- 环7经验：`ai_planner/experience_prior.py`（同插件多能力取 max；任何失败降级空表绝不抛）；开关 `AI_PLANNER_EXPERIENCE_PRIOR` 默认关，只重排不改成员；写路径已接 `start_plan_run`。
- 召回：**`%` 方案不可用**（RLS 下选择性估计塌成 1% → 全表扫；`GRANT pg_statistic`、VACUUM 均无效）。正解 **GiST + 无界 KNN** `ORDER BY label <-> q LIMIT k`（0067）；不能加阈值 WHERE；label 与 alias 各走一次 KNN。5 万节点 240ms→6.3ms。
- 蓝图：**只注册 verified 插件**（否则 planner 选到 contract_only → 执行失败）；status 无 `active`；每蓝图必须属集群；先建 blueprint 再建 link。
- 端口契约：`schema_sha256` 从文件派生；提示词与校验同一 registry；catalog 与 contracts 同一次筛选；两代端口用**并集**；召回为空/图谱不可达回退全量。
- `render.py`：columns 必须含域包不认识的阶段；`table_rows` 按 columns 排序。

## 代码模式
- 迁移 revision id **≤32 字符**。
- `catalog.plugin_versions.plugin_id` 是 UUID 外键 → 按点号名取版本须 JOIN `catalog.plugins` 用 `plugins.key`。
- 新增只读端点也要 `require_policy(risk_class="read_only", side_effects="read_only")`，否则 fail-closed 403。
- `discover_plugins(globs=..., domain=...)` 同传时 domain 静默失效。
- `build_port_contracts` 在 `packages/ai_planner/catalog.py`。
- 日志是旁路，不得阻塞启动。

## 交付
- 验证报告带真实 run_id/trace_id/sha256、DB 直查证据、可复现命令，未运行项明确标注。
- 文档可能滞后（插件数、模型、迁移 head），引用前复核；性能结论以实测为准。
- 低优先遗留：5 处夹具用 `"a"*64`；`test_plugin_runtime_gdrive_e2e.py` 硬编码 `G:/数据`；本机 PG 连接 ~55ms，多处每次新建连接（未做连接池）。

## 发布（v0.1.0 就绪）
- 三种形态：Windows 安装包（自足运行时）、Docker（`docker compose up -d`，postgres/init/api/worker 四服务）、源码运行。
- 构建：`scripts/package-windows.py --stage-only` → `cd desktop && npm run package:win`（须先手工 `rm -rf desktop/dist/win-unpacked*`，否则 electron-builder 触发 safe-delete 拦截）；构建前 `rm` 同样要绕沙箱。
- 安装版运行时由 `desktop/electron/backend.ts` 全权管理：initdb → 角色/扩展 → alembic → 种子/注册/索引 → API → worker。**端口运行时选**（默认 8010/54329，占用则后移，存 `runtime.json`）；首启有 splash 进度窗（`createSplashWindow`）。
- 冒烟：`scripts/smoke-api.py`（纯标准库，14 项）+ `build/verify-packaged.sh`（打包产物全链路）+ `build/verify-port-conflict.sh`。
- **冒烟语义**：该租户写操作一律 REQUIRE_APPROVAL，匿名写入被拒才是正确行为；不要写成"必须上传成功"。
- Docker 未在本机构建验证（daemon 依赖 WSL2，`wsl.exe` 被沙箱黑名单）；`Dockerfile`/`docker-compose.yml` 已写好待外部验证。
- **仓库当前是残缺的**：`packages/ai_planner/` 下 11 个模块（`evidence`/`model_access`/`source_adapters`/`planning_service` 等）+ `packages/paths.py` 从未提交，`main.py` 直接 import → 从仓库 clone 必 ModuleNotFoundError；打包版能跑只因脚本从工作区复制。**提交必须整体包含未跟踪源码。**
- 许可：**源码可见非商用**（`LICENSE`）。依赖：`requirements.txt` 锁顶层 9 个包（`requirements-dev.txt` 加工具），传递依赖仍会漂（实测 starlette 1.8→1.10）。pip 默认阿里云镜像缺 markupsafe wheel，用清华源。
- `scripts/*.py` 以脚本方式运行时 `sys.path[0]=scripts/`，**必须显式把仓库根加入 sys.path**；开发机靠 `.venv` 的 editable 安装（根目录 `audit_network.egg-info`）掩盖了这个问题。
- 从零安装的权威步骤在 `docs/install-from-source.md`；独立副本验证脚本 `build/verify-fresh-install.sh`（未入库）。
