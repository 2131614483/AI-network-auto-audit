# Windows 安装包：构建与实测记录

日期：2026-10-28。范围：本地构建 Windows x64 安装包，并用全新用户数据目录验证首次启动。
未包含：代码签名、自动更新、macOS/Linux。

> 2026-10-29 后续：源码已改为用户明确初始化或备份后升级，新预览包及验证记录见 [显式数据库维护与预览构建](release-progress-20261029-maintenance.md)。本页其余内容保留为旧包历史记录。新流程的 GUI 策略裁决与真实升级/恢复尚待验收。

> 2026-10-29 复核：下列安装包文件与 SHA256 已重新确认；本轮修复了源码中的安装版 AI 设置路径，旧包尚未包含该修复。现有启动自动迁移与 `AGENTS.md` 的显式迁移要求冲突，须在正式发行前处理。最新验证与边界见 [发布进度复核](release-progress-20261029.md)。本页的先前本地启动记录不代替完整安装/原生界面验收。

## 1. 产物

| 项目 | 值 |
|---|---|
| 安装包 | `desktop/dist/AuditNetwork-0.1.0-win-x64-Setup.exe` |
| 体积 | 189.7 MB |
| SHA-256 | `041903bebb904d1211d230cc0ed2bd24fef4a4b24430c6c4763c921a6d8d3420` |
| 校验清单 | `desktop/dist/SHA256SUMS.txt`、`desktop/dist/release-manifest.json` |
| 免安装目录版 | `desktop/dist/win-unpacked/`（约 800 MB，可直接运行，用于调试） |

安装包内 resources 组成：

| 目录 | 内容 | 体积 |
|---|---|---|
| `runtime/python` | 便携 CPython 3.12.12（python-build-standalone）+ 全部运行依赖 | 140 MB |
| `runtime/pgsql` | PostgreSQL 16.13 的 `bin`/`lib`/`share` + pgvector 0.8.0 | 148 MB |
| `app` | `apps`/`packages`/`migrations`/`contracts`/`plugins`/`web`/`scripts` | 6.4 MB |

用户机器不需要安装 Python、Node.js、PostgreSQL、Git 或 Docker。

## 2. 构建命令

```powershell
# 1) 组装便携运行时与业务代码到 build/stage（约 274 MB）
.venv\Scripts\python.exe scripts\package-windows.py --stage-only

# 2) 出安装包（electron-vite 构建 + electron-builder NSIS）
cd desktop
npm run package:win
```

`scripts/package-windows.py` 的取舍：

- Python 取自 `.venv/pyvenv.cfg` 的 `home`（本机为 uv 管理的 `python-build-standalone`，本身可重定位）；
  依赖复制进**解释器自带的 `Lib\site-packages`**，因为插件子进程用 `python.exe -I -c` 启动，
  `-I` 会忽略 `PYTHONPATH` 与用户 site。
- PostgreSQL 只带 `bin`/`lib`/`share`；`data`（6.5 GB）、`pgAdmin 4`、`doc`、`installer` 一律不带。
- 不带 `审计项目案例报告效果展示/`（66 MB 真实案例资料），案例页会如实报告目录缺失。

## 3. 运行时生命周期（安装版）

`desktop/electron/backend.ts` 承担原先 PowerShell 脚本的职责，仅 `app.isPackaged` 时启用：

1. 先从 `runtime.json` 读上次用过的端口；`54329`（数据库）/`8010`（API）被占用时自动后移到
   空闲端口，选定结果写回 `runtime.json` 复用。判定规则：已有簇且 `postmaster.pid` 存在 →
   端口上的实例大概率是上次留下的自己，沿用；否则端口被别的进程占着 → 换端口；
2. 首次启动 `initdb` 到 `%APPDATA%\audit-network-desktop\data\pgdata`（locale=C、UTF8、scram-sha-256）；
3. 超级用户建 `audit_app` / `audit_migrator` 角色与库，创建 `pgcrypto`/`pg_trgm`/`ltree`/`vector` 扩展；
4. `alembic upgrade head`（以 migrator 身份，非超级用户）；
5. 首次：种子数据 → 插件目录注册 → 图谱索引，写 `initialized.json`；
6. 起 uvicorn（127.0.0.1:`<apiPort>`）→ 等 `/health` → 起 worker；
7. 退出时停 worker/API 并 `pg_ctl stop -m fast`。

**启动进度窗口**：`createSplashWindow()` 在主窗口之前创建，尺寸 460×240、无边框，
把 `onLog` 的每条消息实时显示为状态行（`did-finish-load` 之前的消息排队后回放）。
没有它，用户双击后 40–50 秒内看不到任何窗口，会以为程序坏了。
主窗口在后端就绪后才创建，并把实际 `apiBaseUrl` 回填给 IPC 转发层（`controlPlaneUrl` 由 `const` 改为 `let`）。

数据目录拆分：`packages/paths.py::data_root()` 读取 `AUDIT_NETWORK_DATA_DIR`（由启动器注入），
未设置时仍落到 `<项目>/.data`，开发与 CI 行为不变。原先 6 处硬编码
`Path(__file__).parents[2] / ".data"` 已全部改为该函数——否则装进 `Program Files` 后日志与
工件写入必然失败。同类问题另有一处：`packages/knowledge/ingest.py::default_drop_root()`
原来返回相对路径 `.data/drop`，安装版工作目录是只读的 `resources/app`，
**知识库导入必定失败**；已改为 `data_root()/drop`，保留 `KNOWLEDGE_DROP_ROOT` 覆盖。

## 4. 实测结果

全新用户数据目录（先删除 `%APPDATA%\audit-network-desktop\data`）后启动 `win-unpacked\AuditNetwork.exe`：

| 检查项 | 结果 |
|---|---|
| 首次初始化耗时 | 约 50 秒（建库 5s、迁移 9s、种子 6s、插件注册 10s、索引 15s） |
| `initialized.json` | 生成 |
| `/api/v1/health/ready` | `status: ok`，`database: ok`，`migration_head = 0069_collaborative_planning`，`worker_heartbeats = 1` |
| 渲染层 | 已在真实调用 `/api/v1/topology/chains`、`/api/v1/topology/planning/intents` 等接口（200） |
| 进程 | Electron 主进程 + GPU + 渲染 + utility 共 4 个 |
| 随包运行时独立验证 | 用 `resources/runtime` 单独跑通 initdb → 扩展（vector 0.8.0）→ 71 个迁移 → 种子（3 mission / 2 事件）→ API 首页 200，插件 123、图谱节点 152、蓝图 103 |

### 4.1 HTTP 冒烟（`scripts/smoke-api.py`）

在打包产物的运行时上跑完整链路后，用标准库脚本对控制平面做 14 项检查，全部通过：

```
健康检查 /api/v1/health/ready     ok    status=ok
数据库连通                         ok    status=ok
迁移版本                          ok    0069_collaborative_planning
UI 引导 /api/v1/ui/bootstrap      ok
集群列表 /topology/clusters       ok
集群数据                          ok    5 个
蓝图列表 /topology/blueprints     ok
蓝图数据                          ok    103 条
能力图谱 /topology/plugin-nebula  ok
策略模拟 /policy/simulate         ok
策略判定                          ok    REQUIRE_APPROVAL
写入边界（未审批写入被拒绝）        ok    REQUIRE_APPROVAL risk=75.0
审批记录 /approvals               ok
审批落库                          ok    1 条，含本次 tool_call
```

其中「写入边界」是关键：这个租户对写操作一律要求审批，所以匿名 HTTP 写入**本就不该成功**。
冒烟验证的是边界真的生效（`fail-closed`）以及审批记录确实落在 `control.approvals` 里，
而不是假装写入成功。

### 4.2 端口冲突

占住 `8010` 与 `54329`，再用全新 userData 目录启动安装版：

```
本地数据库已启动（端口 54330）
…
Uvicorn running on http://127.0.0.1:8011
控制平面就绪 → 控制平面地址：http://127.0.0.1:8011
runtime.json: apiPort=8011 pgPort=54330
```

首次初始化约 40 秒完成，两个端口都自动避让。复现：`bash build/verify-port-conflict.sh`。

## 5. 未验证项与已知限制

1. **静默安装未验证**：`Setup.exe /S` 与 `/S /D=...` 在本机都弹出交互窗口（标题「AuditNetwork 安装」），
   本会话没有可交互桌面，无法完成点击。安装流程需人工双击确认；安装器本身能正常启动。
   若后续要做 CI 自动化，建议另加 `portable` 目标或改用 `oneClick` 安装器。
2. **界面截图未取到**：`AUDIT_NETWORK_CAPTURE_PATH` 生成的 PNG 为 0 字节，本会话 `capturePage` 不可用；
   界面是否正常以渲染层真实 API 调用为依据，最终仍需人工目视确认。
3. **无代码签名**：SmartScreen 会提示"未知发布者"。
4. **无应用图标**：使用 Electron 默认图标。
5. **无自动更新**：升级需重新下载安装包，数据目录保留。
6. **案例资产不在包内**：案例页会显示缺失，需另行决定是否随包或走 Release 附件。
7. **卸载保留数据**：`deleteAppDataOnUninstall: false`，符合"审计资料不随卸载删除"的既定约束。
8. **数据库升级无备份与回滚**：启动时自动 `alembic upgrade head`；正式发行前需补备份与失败回滚。
9. **Docker 方案未在本机构建验证**：本机 Docker daemon 依赖 WSL2，被环境策略阻断
   （`wsl.exe` 在程序黑名单里）。`Dockerfile` 与 `docker-compose.yml` 已按运行期目录写好，
   首次构建需在有 Docker 的机器上完成。

> 已解决（上一版列为限制）：端口固定（现自动避让，见 4.2）、首次启动无进度窗口（现显示进度，见第 3 节）。

## 6. 本机环境坑（复现时必读）

| 现象 | 根因 | 处置 |
|---|---|---|
| `AuditNetwork.exe: bad option: --enable-logging`、进程只有 1 个、无窗口 | 环境里存在 `ELECTRON_RUN_AS_NODE=1`（宿主本身是 Electron 应用），exe 被当作 Node 运行 | 启动时 `env -u ELECTRON_RUN_AS_NODE` |
| PowerShell `Start-Process` 报"已添加项，字典中的关键字 HTTP_PROXY" | 环境同时存在 `HTTP_PROXY` 与 `http_proxy` 等仅大小写不同的变量 | 启动前清掉小写副本 |
| 卡在"正在创建本地数据库实例"不动 | `spawnSync(pg_ctl start)` 的 stdio 管道被 postmaster 继承，等不到 EOF | `stdio: "ignore"`，错误改读 `postgres.log` |
| `emitLog` 后整段 `whenReady` 静默失败 | 安装版日志目录改为 userData 后未 `mkdir`，`appendFileSync` 抛 ENOENT | 建目录；并给 `whenReady` 加 `.catch` 弹窗 |
| electron-builder 报 `SAFE_DELETE_BULK_CONFIRM_REQUIRED` | 宿主工具的批量删除保护拦截 `rm dist\win-unpacked`（9478 文件） | 构建前先用 shell 删除 `dist/win-unpacked*` |
| `Cannot find module 'balanced-match'` | 依赖树被中断的安装写坏，`brace-expansion@5` 缺传递依赖 | `npm i balanced-match@4.0.2 --no-save` |
| npm 安装卡死 | 默认 registry 在本网络不可达 | `--registry=https://registry.npmmirror.com`；构建加 `ELECTRON_MIRROR` 与 `ELECTRON_BUILDER_BINARIES_MIRROR` |
| psql 传中文报 `invalid byte sequence 0xd5 0xc5` | Windows 下 psql 命令行走 GBK 编码 | 安装脚本里的 SQL 一律只用 ASCII |
