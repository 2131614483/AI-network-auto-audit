# 运行期镜像：控制平面（API + worker）与迁移工具共用同一份代码。
#
# 打包版桌面程序自带便携 Python 和 PostgreSQL，不需要本文件；这里服务的是
# 服务器部署与网页入口——`docker compose up` 一条命令起全栈。
#
# 只携带运行期目录（约 12MB 源码），测试、桌面端、案例资产、设计文档都不入镜像。

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONNOUSERSITE=1

WORKDIR /app

# 依赖只从 requirements.txt 安装。它是本仓库验证过的锁定组合（sqlalchemy==2.0.52 等）。
# 从 pyproject.toml 的宽松范围现场解析会漂移：sqlalchemy 2.1 把 `postgresql://` 的默认
# 驱动改成 psycopg3，而运行期只装 psycopg2，迁移会以 ModuleNotFoundError: psycopg 失败。
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# 非 root 运行。插件子进程走 `sys.executable -I`，用的就是镜像里的解释器，
# 因此不需要额外的运行时装在别处。
RUN useradd --system --uid 10001 --create-home --shell /usr/sbin/nologin audit

COPY --chown=audit:audit apps ./apps
COPY --chown=audit:audit packages ./packages
COPY --chown=audit:audit migrations ./migrations
COPY --chown=audit:audit contracts ./contracts
COPY --chown=audit:audit plugins ./plugins
COPY --chown=audit:audit web ./web
COPY --chown=audit:audit scripts ./scripts
COPY --chown=audit:audit alembic.ini ./

# /data 是指定的可写数据根（工件、草稿、日志），由 compose 挂卷。
RUN mkdir -p /data && chown audit:audit /data

USER audit

ENV AUDIT_NETWORK_DATA_DIR=/data

EXPOSE 8010

# 探针不写在镜像里：api 与 worker 共用本镜像，但只有 api 提供 HTTP 端点。
# 镜像级 HEALTHCHECK 会被 worker 继承，让它永远 unhealthy。
# api 的探针见 docker-compose.yml；worker 的存活由容器状态与心跳表反映。

CMD ["python", "-m", "uvicorn", "apps.api.main:app", "--host", "0.0.0.0", "--port", "8010"]
