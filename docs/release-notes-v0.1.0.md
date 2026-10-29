# v0.1.0 发布说明

日期：2026-10-28。首个可下载运行的发行版。

## 这一版能做什么

三种交付形态，业务内核只有一套：

| 形态 | 别人怎么用 | 数据在哪 | 状态 |
|---|---|---|---|
| Windows 安装版 | 下载 `Setup.exe`，双击安装后启动 | 用户本机 `%APPDATA%` | **已构建、已实测** |
| Docker（网页入口） | `docker compose up -d`，浏览器打开 `:8010` | 容器卷 | **文件已就绪，未在本机构建验证** |
| 源码运行 | 装 Python 3.12 + PostgreSQL 16 + pgvector，`pip install -r requirements.txt` | 本机数据库 | **已独立验证（空库、全新 venv）** |

安装包自带 CPython 3.12.12、PostgreSQL 16.13 + pgvector 0.8.0 与全部业务代码，
**用户机器不需要安装任何运行时**。源码安装步骤见 `docs/install-from-source.md`。

## 下载

| 文件 | 体积 | SHA-256 |
|---|---|---|
| `AuditNetwork-0.1.0-win-x64-Setup.exe` | 189.7 MB | `041903bebb904d1211d230cc0ed2bd24fef4a4b24430c6c4763c921a6d8d3420` |

校验：

```powershell
certutil -hashfile AuditNetwork-0.1.0-win-x64-Setup.exe SHA256
```

同目录另附 `SHA256SUMS.txt` 与 `release-manifest.json`（含构建提交、迁移 head、文件哈希）。

## 首次启动会发生什么

双击后先出现进度窗口，依次显示：

```
正在创建本地数据库实例（首次启动约需 10 秒）…
本地数据库已启动（端口 54329）
正在初始化工作区角色与扩展…
正在升级数据库结构…
正在写入示例数据…
正在注册插件目录…
正在建立能力图谱索引…
正在启动控制平面…
控制平面已就绪
```

实测在全新用户目录下约 **40–50 秒**完成，随后打开工作台。再次启动只需几秒。

初始化内容：`initdb` → 建 `audit_app`/`audit_migrator` 角色与库 → 4 个扩展（含 `vector 0.8.0`）
→ 71 个迁移到 `0069_collaborative_planning` → 示例数据（5 集群 / 103 蓝图）→ 插件注册（123 个）
→ 图谱索引（152 节点）→ 起 API 与 worker。

## 本版可靠性改动

- **端口自动避让**：API 与数据库默认 8010/54329，被占用时自动后移。实测占住两个默认端口后，
  程序自动改用 `8011`/`54330` 并正常就绪。
- **首次启动进度窗口**：此前初始化期间没有窗口，用户会以为程序坏了。现在立刻显示进度。
- **可写数据根与只读资源根分离**：`packages/paths.py::data_root()` 读取 `AUDIT_NETWORK_DATA_DIR`，
  替换全部硬编码 `<项目>/.data`（13 处），否则装进 `Program Files` 后日志与工件必然写失败。
- **知识库落盘根修正**：上传暂存目录原为相对路径 `.data/drop`，安装版工作目录是只读的
  `resources/app`，导入知识一定失败；改为 `data_root()/drop` 绝对路径。

## 已知限制

1. **未做代码签名**：SmartScreen 会提示"未知发布者"。
2. **无应用图标**，使用 Electron 默认图标。
3. **未做自动更新**：升级需重新下载安装包；数据目录保留。
4. **数据库升级无备份/回滚保护**：启动时自动 `alembic upgrade head`，正式生产使用前需补。
5. **案例资产不在包内**：`审计项目案例报告效果展示/`（66 MB 真实资料）未随包，案例页会如实提示目录缺失。
6. **网页入口是控制面最小壳**：运行总览、知识库导入、插件工作台、审批中心、策略模拟可用；
   组网画布、计划编排、报告视图在桌面版。
7. **Docker 方案未在本机构建验证**：本机会话里 Docker daemon 依赖 WSL2，被环境策略阻断。
   `Dockerfile` 与 `docker-compose.yml` 已按运行期目录写好，需在有 Docker 的机器上首次验证。

## 验证记录

发布前在本机做过三类验证，全部通过：

| 验证 | 范围 | 结果 |
|---|---|---|
| 打包产物全链路 | 用安装包内 `resources/runtime` 跑 initdb → 扩展 → 71 迁移 → 种子 → 插件/图谱 → API+worker | 首页 200，`status: ok`，`worker_heartbeats: 1` |
| HTTP 冒烟（14 项） | 健康、迁移版本、UI 契约、集群/蓝图/图谱、策略判定、写入边界与审批落库 | 14/14 通过 |
| 端口冲突 | 占住 8010 与 54329 后以全新数据目录启动 | 自动改用 8011/54330 并正常就绪 |
| **源码独立安装** | 独立副本（3589 文件）+ 全新 venv + `pip install -r requirements.txt` + 全新空库 | 迁移到 0069、插件 123 / 节点 152 / 蓝图 103、冒烟 14/14 |
| 测试门禁 | contract + security + DAG 闭环 836 条；核心业务集成 200 条；unit 1194 条；其余集成 307 条 | 约 2528 通过，0 失败 |

### 源码独立安装抓到的两个缺陷（已修）

1. **`packages/ai_planner/` 下 11 个模块从未提交**（`evidence.py`、`model_access.py`、
   `source_adapters.py`、`planning_service.py` 等），而 `apps/api/main.py` 直接 import 它们。
   打包版能跑是因为打包脚本从工作区复制；**从仓库 clone 会直接 `ModuleNotFoundError`**。
2. **5 个 `scripts/*.py` 依赖开发机上的 editable 安装**。以 `python scripts/xxx.py` 运行时
   `sys.path[0]` 是 `scripts/`，`packages` 导入不到。已在脚本内显式补上仓库根。

复现命令见 `docs/windows-package.md`、`docs/install-from-source.md` 与 `README.md` 的「发布前冒烟」。

## 许可

**源码可见、非商业**（见 `LICENSE`）。可以查看、学习、修改用于非商业目的；
商业用途（含收费部署、以此为核心的 SaaS、付费交付）需另行取得书面授权。
这不是 OSI 认可的开源许可。

## 下一步

1. 代码签名证书（消除 SmartScreen 警告）。
2. Docker 方案在具备 Docker 的机器上首次构建与冒烟。
3. 网页版复用桌面 React 工作台（当前 `web/` 只是控制面最小壳）。
4. 案例资产分发方式（随包 / Release 附件 / 独立仓库）。
