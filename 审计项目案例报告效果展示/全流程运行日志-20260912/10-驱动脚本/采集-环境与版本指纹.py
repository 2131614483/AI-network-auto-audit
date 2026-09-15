"""Capture an environment / version fingerprint so the archive is self-describing.

Records what the run actually happened on: OS, interpreters, the exact git
revision plus per-file hashes of the code that executed, the resolved dependency
set, and the database's own version/extensions/migration head.  Without this a
log archive cannot be tied back to a specific build.
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import platform
import subprocess
import sys
from datetime import datetime, timezone

import psycopg2

REPO = pathlib.Path(r"D:\pythonpro\audit_network")
OUT = REPO / "docs" / "全流程运行日志-20260912" / "07-环境与版本指纹"
OUT.mkdir(parents=True, exist_ok=True)

#: Code that determined the observed behaviour — hashed so a reader can prove
#: which revision produced this archive.
FINGERPRINT_FILES = [
    "pyproject.toml", "uv.lock", "alembic.ini", "package.json", "desktop/package.json",
    "apps/api/main.py", "apps/worker/local.py",
    "packages/plugin_topology/compiler.py", "packages/plugin_topology/ir.py",
    "packages/plugin_topology/ports_executor.py", "packages/plugin_topology/isolated.py",
    "packages/plugin_topology/service.py", "packages/plugin_topology/port_adapters.py",
    "packages/plugin_topology/chain.py", "packages/plugin_topology/runs.py",
    "packages/ai_planner/composer.py", "packages/plugin_runtime/runner.py",
    "packages/policy/engine.py", "packages/ai/config.py", "packages/ai/env_store.py",
    "packages/ai/gateway.py",
]
DB = "postgresql://audit_app:admin@localhost:5432/audit_network"


def sh(*args: str) -> str:
    try:
        return subprocess.run(args, cwd=REPO, capture_output=True, text=True, timeout=60).stdout.strip()
    except Exception as exc:
        return f"<failed: {exc}>"


def main() -> None:
    lines: list[str] = []

    def add(title: str, body: str) -> None:
        lines.append(f"## {title}")
        lines.append("")
        lines.append("```")
        lines.append(body.rstrip() or "(空)")
        lines.append("```")
        lines.append("")

    add("采集时间", datetime.now(timezone.utc).astimezone().isoformat())
    add("操作系统", f"{platform.platform()}\n{platform.machine()}\n{platform.processor()}")
    add("Python", sys.version.replace("\n", " ") + f"\n解释器: {sys.executable}")
    add("uv / node / npm", "\n".join([
        f"uv    : {sh('uv', '--version')}",
        f"node  : {sh('node', '-v')}",
        f"npm   : {sh('npm', '-v')}",
    ]))

    add("git", "\n".join([
        f"commit : {sh('git', 'rev-parse', 'HEAD')}",
        f"branch : {sh('git', 'rev-parse', '--abbrev-ref', 'HEAD')}",
        f"提交数 : {sh('git', 'rev-list', '--count', 'HEAD')}",
        f"remote : {sh('git', 'remote', '-v') or '(无)'}",
        f"状态   : {'干净' if not sh('git', 'status', '--porcelain') else '有未提交改动'}",
    ]))

    # 依赖：从 venv 实际安装状态导出。
    # 注意：uv 创建的 venv 默认**不含 pip**，所以 `python -m pip freeze` 会静默返回空；
    # 必须用 uv 自己的命令，否则这份"依赖快照"是假的。
    freeze = sh("uv", "pip", "freeze", "--python", str(REPO / ".venv" / "Scripts" / "python.exe"))
    (OUT / "python-packages.txt").write_text(freeze, encoding="utf-8")
    pkg_count = len([ln for ln in freeze.splitlines() if ln and not ln.startswith("#")])
    add("Python 依赖", f"共 {pkg_count} 个包，完整清单见 python-packages.txt"
                       + ("" if pkg_count else "  ⚠ 采集为空，说明 uv 调用失败"))

    npm_ls = sh("npm", "ls", "--depth=0")
    (OUT / "npm-packages.txt").write_text(npm_ls, encoding="utf-8")

    # 代码指纹
    rows = []
    for rel in FINGERPRINT_FILES:
        p = REPO / rel
        if p.is_file():
            rows.append((rel, p.stat().st_size, hashlib.sha256(p.read_bytes()).hexdigest()))
    (OUT / "代码指纹-sha256.csv").write_text(
        "文件,字节,sha256\n" + "\n".join(f"{r},{s},{h}" for r, s, h in rows),
        encoding="utf-8-sig",
    )
    add("被测代码指纹", "\n".join(f"{h[:16]}…  {r}  ({s}B)" for r, s, h in rows))

    # 数据库
    conn = psycopg2.connect(DB)
    cur = conn.cursor()
    cur.execute("select version()")
    dbver = cur.fetchone()[0]
    cur.execute("select extname, extversion from pg_extension order by extname")
    exts = ", ".join(f"{n} {v}" for n, v in cur.fetchall())
    cur.execute("select version_num from alembic_version")
    head = cur.fetchone()[0]
    cur.execute("select set_config('app.tenant_id',(select id::text from iam.tenants where slug='local-dev'),false)")
    counts = {}
    for t in ("topology.plugin_blueprints", "topology.routing_plans", "topology.execution_runs",
              "control.node_attempts", "topology.execution_ledger", "policy.policy_sets"):
        try:
            cur.execute(f"select count(*) from {t}")
            counts[t] = cur.fetchone()[0]
        except Exception:
            counts[t] = "n/a"
    conn.close()
    add("数据库", "\n".join([
        dbver,
        f"扩展      : {exts}",
        f"迁移 head : {head}",
        "",
        "行数:",
        *[f"  {k:34s} {v}" for k, v in counts.items()],
    ]))

    add("本地模型（Ollama）", "见同目录 ollama-models.json；连接失败时会记录失败原因（如实反映采集时刻的状态）")
    try:
        import urllib.request
        raw = urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=5).read()
        (OUT / "ollama-models.json").write_text(raw.decode("utf-8"), encoding="utf-8")
        models = json.loads(raw).get("models", [])
        lines.append(f"Ollama 在线，{len(models)} 个模型。\n")
    except Exception as exc:
        (OUT / "ollama-models.json").write_text(
            json.dumps({"error": str(exc), "note": "采集时刻 Ollama 未运行；知识库向量检索会 fail-closed"},
                       ensure_ascii=False, indent=2),
            encoding="utf-8")
        lines.append(f"⚠ 采集时刻 Ollama 未运行：{exc}\n")

    (OUT / "环境与版本指纹.md").write_text(
        "# 环境与版本指纹\n\n> 本文件让归档可追溯到确切的代码版本与运行环境。\n\n" + "\n".join(lines),
        encoding="utf-8",
    )
    print(f"已生成: {OUT / '环境与版本指纹.md'}")
    print(f"  Python 包: {len(freeze.splitlines())}")
    print(f"  代码指纹: {len(rows)} 个文件")
    print(f"  数据库 head: {head}")


if __name__ == "__main__":
    main()
