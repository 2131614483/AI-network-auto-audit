# 从源码安装（不用安装包，不用 Docker）

适用于：想看代码、要改代码、或部署到自己服务器上的人。
只要一个能跑的成品，请直接下载安装包，见 README「方式一」。

本文的每一步都在一份**全新复制出来的源码副本 + 全新虚拟环境 + 全新空数据库**里实测过，
结果见文末「验证记录」。

## 1. 前置条件

| 组件 | 版本 | 说明 |
|---|---|---|
| Python | 3.11+（实测 3.12.12） | 必须能 `python -m venv` |
| PostgreSQL | 16+（实测 16.13） | 需要超级用户建扩展 |
| pgvector | 0.8（实测） | 迁移 `0001` 要 `CREATE EXTENSION vector` |
| Node.js | 20+ | **仅桌面界面需要**；只跑 API 可以不装 |

> `vector` 不是 trusted 扩展，所以建扩展必须用超级用户。应用账号故意没有这个权限。

## 2. 安装依赖

```bash
python -m venv .venv

# Windows
.venv\Scripts\python -m pip install -r requirements.txt
# macOS / Linux
.venv/bin/python -m pip install -r requirements.txt
```

只有 9 个包，见 `requirements.txt`（由 `pyproject.toml` 派生，锁定到实测版本）。

国内网络若遇到镜像缺文件（例如 `markupsafe` 的 wheel 404），换源：

```bash
pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt
```

## 3. 建库与扩展

用超级用户（下面以 `postgres` 为例）执行，**SQL 全部是 ASCII**——
Windows 下 `psql` 命令行传中文会按 GBK 编码，报 `invalid byte sequence 0xd5 0xc5`。

```bash
psql -h 127.0.0.1 -U postgres -d postgres -c "CREATE ROLE audit_app NOINHERIT NOSUPERUSER NOBYPASSRLS LOGIN; CREATE ROLE audit_migrator NOINHERIT NOSUPERUSER NOBYPASSRLS LOGIN;"
# 交互设置两份不同的自选强密码（psql 不会把密码回显到终端）
psql -h 127.0.0.1 -U postgres -d postgres -c "\password audit_app"
psql -h 127.0.0.1 -U postgres -d postgres -c "\password audit_migrator"
psql -h 127.0.0.1 -U postgres -d postgres -c "CREATE DATABASE audit_network OWNER audit_migrator;"
psql -h 127.0.0.1 -U postgres -d audit_network -c "CREATE EXTENSION IF NOT EXISTS pgcrypto;"
psql -h 127.0.0.1 -U postgres -d audit_network -c "CREATE EXTENSION IF NOT EXISTS pg_trgm;"
psql -h 127.0.0.1 -U postgres -d audit_network -c "CREATE EXTENSION IF NOT EXISTS ltree;"
psql -h 127.0.0.1 -U postgres -d audit_network -c "CREATE EXTENSION IF NOT EXISTS vector;"
psql -h 127.0.0.1 -U postgres -d audit_network -c "GRANT USAGE ON SCHEMA public TO audit_app;"
```

角色必须先于建库创建。上面的流程适用于全新环境；已有角色时先确认所有权和权限，跳过创建，不覆盖密码或业务数据库。以下连接串中的 `YOUR_APP_PASSWORD` / `YOUR_MIGRATION_PASSWORD` 替换为刚设置的密码，URL 中的特殊字符需要编码；不要使用开发默认密码。

`scripts/init-native-postgres.sql` 和 `scripts/init-docker-postgres.sql` 已经把这些整理好了，
按自己的部署方式选用即可。

## 4. 迁移

迁移必须用 `audit_migrator`（非超级用户），这一步同时验证了权限边界：

```bash
# Windows
set DATABASE_URL=postgresql://audit_migrator:YOUR_MIGRATION_PASSWORD@localhost:5432/audit_network
.venv\Scripts\python -m alembic upgrade head

# macOS / Linux
export DATABASE_URL=postgresql://audit_migrator:YOUR_MIGRATION_PASSWORD@localhost:5432/audit_network
.venv/bin/python -m alembic upgrade head
```

期望输出结尾：`Running upgrade 0068_connectivity_read_policy -> 0069_collaborative_planning`。

## 5. 写入初始数据

下面三步都用应用账号 `audit_app`，只执行一次：

```bash
set DATABASE_URL=postgresql://audit_app:YOUR_APP_PASSWORD@localhost:5432/audit_network

# 5.1 示例数据（5 个集群、示例任务与事件）
.venv\Scripts\python -c "import os; from packages.demo.seed import seed_local_demo; print(seed_local_demo(os.environ['DATABASE_URL']))"

# 5.2 插件目录注册（把插件写进 catalog）
.venv\Scripts\python -m packages.plugin_runtime.registration --database-url "%DATABASE_URL%" --tenant-slug local-dev

# 5.3 能力图谱 + 组网蓝图索引
.venv\Scripts\python scripts\index-plugin-graph.py --database-url "%DATABASE_URL%" ^
  --domain audit --domain aiops --domain quant --domain knowledge
```

**5.3 不能跳过**：只做迁移和种子，组网会"选不中插件"——图谱负责召回，蓝图负责规划，
缺一半链路就是断的。

期望规模：插件 123、图谱节点 152、蓝图 103。

## 6. 启动

```bash
# 控制平面
.venv\Scripts\python -m uvicorn apps.api.main:app --host 127.0.0.1 --port 8010

# 另开一个终端：后台执行器（心跳、组网触发、DAG 重试）
.venv\Scripts\python -m apps.worker.local
```

- 页面：`http://127.0.0.1:8010/`
- API 文档：`http://127.0.0.1:8010/docs`
- 就绪检查：`http://127.0.0.1:8010/api/v1/health/ready`

**不启 worker 时** `/health/ready` 会一直是 `degraded`（没有心跳），这是正常的。

## 7. 桌面界面（可选）

```bash
cd desktop
npm ci
npm run dev          # 开发模式
# 正式组装使用 scripts/package-windows.py；它预检本机离线工具并保留旧包。
```

## 8. 验证装对了

```bash
.venv\Scripts\python scripts\smoke-api.py --base-url http://127.0.0.1:8010 --tenant-id <租户 UUID>
```

租户 UUID 从库里取（每次新建库都不一样，不要写死）：

```sql
SELECT id FROM iam.tenants WHERE slug = 'local-dev';
```

14 项检查全过才算装对。其中「未审批写入被拒绝」是**预期行为**——该租户的写操作一律要求审批。

## 验证记录

2026-10-28，在一份独立副本里（`git ls-files` + 未跟踪源码，3589 个文件 / 95.8 MB），
用全新 `venv` + `pip install -r requirements.txt` + 全新空库 `audit_net_fresh` 实测：

| 步骤 | 结果 |
|---|---|
| `pip install -r requirements.txt` | 成功（31 个包，清华源） |
| `create_app()` | 134 条路由 |
| `alembic upgrade head` | 到 `0069_collaborative_planning` |
| 示例数据 | 3 missions / 3 workflow_runs / 2 incidents / 5 alerts |
| 插件注册 + 图谱索引 | 123 插件 / 152 节点 / 103 蓝图 |
| `/api/v1/health/ready` | `status: ok`、`worker_heartbeats: 1` |
| `smoke-api.py` | 14/14 通过 |

### 这一步抓到的两个真实缺陷（已修）

1. **`packages/ai_planner/` 下 11 个模块尚待纳入发布提交**（`evidence.py`、`model_access.py`、
   `source_adapters.py`、`planning_service.py` 等），而 `apps/api/main.py` 直接 import 它们。
   开发机上能跑是因为文件就在工作区；**别人 clone 下来会 `ModuleNotFoundError`**。
   打包版能跑是因为打包脚本从工作区复制。→ 发布提交必须包含这些文件。
2. **5 个 `scripts/*.py` 依赖 editable 安装**。以 `python scripts/xxx.py` 方式运行时
   `sys.path[0]` 是 `scripts/`，`packages` 导入不到；开发机上有 `audit_network.egg-info`
   兜住了，干净环境直接失败。已在这 5 个脚本里显式补上仓库根到 `sys.path`。
