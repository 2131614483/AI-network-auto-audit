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

# 依赖清单只保留 pyproject.toml 这一份：在这里解析后安装，
# 避免镜像里再写一份 requirements.txt 与主清单漂移。
COPY pyproject.toml ./
RUN python -c "import tomllib; deps = tomllib.load(open('pyproject.toml', 'rb'))['project']['dependencies']; open('/tmp/requirements.txt', 'w').write('\n'.join(deps))" \
    && pip install --no-cache-dir -r /tmp/requirements.txt \
    && rm -f /tmp/requirements.txt

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

# 容器内探测用轻量端点：/api/v1/health/ready 会带数据库与 worker 检查，
# 首次启动 worker 尚未注册心跳时会返回 degraded，用来判断"进程活着"不合适。
HEALTHCHECK --interval=15s --timeout=5s --start-period=60s --retries=5 \
    CMD python -c "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8010/health', timeout=4).status == 200 else 1)"

CMD ["python", "-m", "uvicorn", "apps.api.main:app", "--host", "0.0.0.0", "--port", "8010"]
