# Gitee 调试部署

日期：2026-10-29。当前目标是先把可部署源码和安装流程放到 Gitee 验证，再推进 GitHub 正式发行。当前分支为 `codex/gitee-deployment-debug`，Phase 10 继续进行中，仅产出 plan_only。

## 下载

在 [Gitee 仓库](https://gitee.com/hx2131614483/audit_network) 切换到上述分支，选择下载 ZIP；或运行：

```text
git clone --config core.longpaths=true --branch codex/gitee-deployment-debug https://gitee.com/hx2131614483/audit_network.git
cd audit_network
```

这条分支保留 Gitee 原有的 MIT LICENSE。真实数据库、API Key、作者的 .venv、node_modules 和安装包不通过 Git 提交；其他电脑需要按下列步骤准备源码运行环境。带全部运行时的 Windows 安装包作为后续单独交付。

Windows 建议检出到较短目录，例如 `C:\audit_network`。仓库保留了长路径的历史归档，上述 `core.longpaths=true` 只写入本次克隆的仓库配置；不修改系统或全局 Git 设置。下载 ZIP 时也应解压到短目录，避免在多层“下载/项目名/分支名”目录中展开归档。

## 准备环境

- Python 3.12；运行依赖使用仓库 requirements.txt，完整依赖锁为 uv.lock。
- PostgreSQL 16 与 pgvector；应用账号应为 NOSUPERUSER / NOBYPASSRLS。
- Node.js 20+ 与 npm，仅完整桌面界面需要。

可使用 uv：

```text
uv sync --python 3.12 --frozen --no-dev
```

或按 [源码安装](install-from-source.md) 创建虚拟环境并安装 requirements.txt。初次安装依赖需要可用的包源；`--offline` 只适用于缓存完整的机器。不能把开发工具缓存缺失当成业务代码测试通过。

## 建库和启动

按源码安装文档先创建角色、设置各自密码、创建空数据库与四个扩展，再由迁移账号显式运行 Alembic。已有业务数据库须先备份，不要删除、重建或覆盖。初始化后使用应用账号运行示例种子、插件注册和能力图谱索引。

两个终端都将 `DATABASE_URL` 设置为同一个应用账号连接串，再分别启动。Windows PowerShell 设置示例（替换密码）：

```powershell
$env:DATABASE_URL="postgresql://audit_app:YOUR_APP_PASSWORD@localhost:5432/audit_network"
```

然后在第一个终端启动 API，在第二个终端启动 Worker：

```text
.venv\Scripts\python -m uvicorn apps.api.main:app --host 127.0.0.1 --port 8010
.venv\Scripts\python -m apps.worker.local
```

macOS / Linux 使用 `.venv/bin/python`。数据库迁移不是普通启动的一部分。

完整桌面界面：

```text
npm ci --prefix desktop
npm --prefix desktop run dev
```

本地浏览器 `http://127.0.0.1:8010/` 仍为简化入口；不把它等同于完整桌面工作台。当前服务只面向本机，不直接暴露公网。

## AI 与当前验收范围

保留 Debug 和外接 AI API 两种接入。Debug 要求独立本机测试库、显式合成资料声明和当前 Codex 会话逐次回填。普通工作区使用自己配置的外接 API；更换模型配置不自动继承旧方案通过资格。

安装版已有“配置完成，启用本机规划”入口：原生确认框展示模型/端点，网关核对配置摘要并记录有限规划授权，再启用后台。该入口只操作安装版自己管理的工作区；源码开发模式继续按 [Phase 10 使用说明](phase10-collaborative-planning.md) 在独立测试库进行受控验证，不能为了跑通而绕过策略或随意启用开发库规则。

当前实际模型验证仍为本机可控端点和合成资料。源码可部署、接口正常、方案评估通过分别记录，不能合并成真实业务审计已通过。

## 调试检查

```text
python -m pytest -q
python -m ruff check .
python -m mypy packages
npm test
npm run typecheck
npm --prefix desktop test
npm --prefix desktop run typecheck
```

Python 开发依赖需额外安装 requirements-dev.txt，或使用 `uv sync --python 3.12 --frozen --extra dev`。所有数据库测试只能使用独立本机测试库；具体跳过项和运行时测试范围见 [状态记录](status.md)。

Windows 打包使用 `python scripts/package-windows.py`，默认创建新的独立 stage、保留旧包并预检本机离线工具链；工具不齐时停止，不能自动下载或把旧资源误装成新包。
