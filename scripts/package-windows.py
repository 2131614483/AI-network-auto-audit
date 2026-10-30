"""Assemble the Windows release staging tree.

The installer ships three things that the development checkout normally takes
from the machine: a Python runtime, a PostgreSQL runtime and the application
packages.  This script copies exactly those into ``build/stage`` so that
``electron-builder`` can treat them as plain resources.

Layout produced::

    build/stage/runtime/python/python.exe   portable CPython + dependencies
    build/stage/runtime/pgsql/bin/pg_ctl.exe portable PostgreSQL 16 + pgvector
    build/stage/app/...                     application code, migrations, plugins

Deliberately excluded:

* the local database cluster (``PGDATA``, several GB of unrelated data)
* ``pgAdmin``, ``doc``, ``include``, ``installer`` from the PostgreSQL install
* ``审计项目案例报告效果展示/`` - real case material stays out of the release
* ``__pycache__`` / ``*.pyc`` and the development ``.data`` directory

Usage::

    python scripts/package-windows.py --stage-only
    python scripts/package-windows.py
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

PROJECT_ROOT = Path(__file__).resolve().parents[1]
STAGE = PROJECT_ROOT / "build" / "stage"

APP_DIRS = [
    "apps",
    "packages",
    "migrations",
    "contracts",
    "plugins",
    "web",
    "scripts",
]
APP_FILES = ["alembic.ini", "pyproject.toml", "README.md", "LICENSE", "requirements.txt", "uv.lock"]

# 只有运行期真正需要的 PostgreSQL 目录；data/pgAdmin/doc/installer 一律不随包。
PG_DIRS = ["bin", "lib", "share"]
PG_LICENSES = ["server_license.txt"]

SKIP_DIR_NAMES = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
SKIP_SUFFIXES = {".pyc", ".pyo"}


def _ignore(directory: str, names: list[str]) -> set[str]:
    skipped: set[str] = set()
    for name in names:
        if (name in SKIP_DIR_NAMES or Path(name).suffix in SKIP_SUFFIXES
                or name.startswith("__editable__")
                or name in {"_virtualenv.pth", "_virtualenv.py", ".env", ".env.bak", ".git"}):
            skipped.add(name)
    return skipped


def base_python() -> Path:
    """Locate the interpreter behind ``.venv`` so the copy matches dev exactly."""
    cfg = PROJECT_ROOT / ".venv" / "pyvenv.cfg"
    if not cfg.exists():
        raise SystemExit("缺少 .venv/pyvenv.cfg，无法定位基础解释器")
    for line in cfg.read_text(encoding="utf-8").splitlines():
        key, _, value = line.partition("=")
        if key.strip().lower() == "home":
            return Path(value.strip())
    raise SystemExit(".venv/pyvenv.cfg 中没有 home 项")


def pg_install_dir(explicit: str | None) -> Path:
    candidates = []
    if explicit:
        candidates.append(Path(explicit))
    candidates.append(Path(r"C:\Program Files\PostgreSQL\16"))
    for candidate in candidates:
        if (candidate / "bin" / "pg_ctl.exe").exists():
            return candidate
    raise SystemExit("找不到 PostgreSQL 16 安装目录，请用 --pg-dir 指定")


def stage_python(stage: Path) -> None:
    target = stage / "runtime" / "python"
    if target.exists():
        raise FileExistsError(f"保留已有运行时，请使用新的 stage：{target}")
    source = base_python()
    print(f"[python] {source} -> {target}")
    shutil.copytree(source, target, ignore=_ignore)

    # 依赖装进解释器自带的 site-packages：插件子进程用 ``-I`` 启动，忽略
    # PYTHONPATH 与用户 site，只有这里能被找到。
    site = target / "Lib" / "site-packages"
    venv_site = PROJECT_ROOT / ".venv" / "Lib" / "site-packages"
    if not venv_site.exists():
        raise SystemExit("缺少 .venv/Lib/site-packages，先安装依赖")
    print(f"[python] 依赖 {venv_site} -> {site}")
    shutil.copytree(venv_site, site, dirs_exist_ok=True, ignore=_ignore)


def stage_pgsql(stage: Path, pg_dir: Path) -> None:
    target = stage / "runtime" / "pgsql"
    if target.exists():
        raise FileExistsError(f"保留已有数据库运行时，请使用新的 stage：{target}")
    target.mkdir(parents=True)
    print(f"[pgsql] {pg_dir} -> {target}")
    for name in PG_DIRS:
        shutil.copytree(pg_dir / name, target / name, ignore=_ignore)
    for name in PG_LICENSES:
        source = pg_dir / name
        if source.exists():
            shutil.copy2(source, target / name)
    vector = target / "lib" / "vector.dll"
    if not vector.exists():
        raise SystemExit("随包 PostgreSQL 缺少 pgvector（lib/vector.dll）")
    print(f"[pgsql] pgvector 就位：{vector.name}")


def stage_app(stage: Path) -> None:
    target = stage / "app"
    if target.exists():
        raise FileExistsError(f"保留已有应用资源，请使用新的 stage：{target}")
    target.mkdir(parents=True)
    print(f"[app] {PROJECT_ROOT} -> {target}")
    for name in APP_DIRS:
        source = PROJECT_ROOT / name
        if not source.exists():
            print(f"[app] 跳过缺失目录 {name}")
            continue
        shutil.copytree(source, target / name, ignore=_ignore)
    for name in APP_FILES:
        source = PROJECT_ROOT / name
        if source.exists():
            shutil.copy2(source, target / name)


def tree_size(path: Path) -> tuple[int, int]:
    files = 0
    total = 0
    for item in path.rglob("*"):
        if item.is_file():
            files += 1
            total += item.stat().st_size
    return files, total


def report(stage: Path) -> None:
    for name in ("runtime/python", "runtime/pgsql", "app"):
        files, total = tree_size(stage / name)
        print(f"  {name:16s} {files:6d} 文件  {total / 1024 / 1024:7.1f} MB")
    files, total = tree_size(stage)
    print(f"  {'合计':14s} {files:6d} 文件  {total / 1024 / 1024:7.1f} MB")


def offline_node_options(guard: Path) -> str:
    """NODE_OPTIONS parses backslashes as escapes, including quoted Windows paths."""
    return f'--require="{guard.resolve().as_posix()}"'


def prepare_desktop_app(desktop: Path, output: Path, target: Path) -> None:
    """Make one standalone application root; never collect developer out/ files."""
    for entry in ("main/index.js", "preload/index.js", "renderer/index.html"):
        if not (output / entry).is_file():
            raise ValueError(f"missing compiled desktop entry: {entry}")
    metadata = json.loads((desktop / "package.json").read_text(encoding="utf-8"))
    if metadata.get("main") not in {"./out/main/index.js", "out/main/index.js"}:
        raise ValueError("unexpected desktop main entry")
    # Main/preload import only Electron and Node. Renderer dependencies are
    # bundled by Vite. The ASAR gate rejects unbundled third-party requires.
    for field in ("dependencies", "devDependencies", "scripts", "build"):
        metadata.pop(field, None)
    target.mkdir(parents=True, exist_ok=False)
    shutil.copytree(output, target / "out")
    (target / "package.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")


def build_installer(stage: Path) -> int:
    """Use an explicit local toolchain; never let the builder fetch a runtime."""
    desktop = PROJECT_ROOT / "desktop"
    node = shutil.which("node")
    cache = Path(os.environ.get("LOCALAPPDATA", "")) / "electron-builder" / "Cache"
    electron = desktop / "node_modules" / "electron" / "dist"
    required = {
        "ELECTRON_BUILDER_NSIS_DIR": cache / "nsis" / "nsis-3.0.4.1",
        "ELECTRON_BUILDER_NSIS_RESOURCES_DIR": cache / "nsis" / "nsis-resources-3.4.1",
        "ELECTRON_BUILDER_7ZIP_PATH": cache / "7zip@1.0.0" / "7zip-win-x64-1nrf7" / "bin" / "7za.exe",
        "ELECTRON_BUILDER_ICONS_TOOLSET_DIR": cache / "icons@1.1.0" / "icons-bundle-1hpnz",
    }
    for path in [electron / "electron.exe", *required.values()]:
        if not path.exists():
            raise SystemExit(f"离线构建工具缺失，停止而不联网下载：{path}")
    if node is None:
        raise SystemExit("需要本机 Node.js 构建工具")
    env = {**os.environ, **{name: str(path) for name, path in required.items()},
           "ELECTRON_BUILDER_OFFLINE": "true", "CSC_IDENTITY_AUTO_DISCOVERY": "false",
           "HTTP_PROXY": "http://127.0.0.1:9", "HTTPS_PROXY": "http://127.0.0.1:9",
           "ALL_PROXY": "http://127.0.0.1:9", "NO_PROXY": "",
           "NODE_OPTIONS": offline_node_options(PROJECT_ROOT / "scripts" / "offline-build-guard.cjs")}
    output = stage.parent / "out"
    vite = desktop / "node_modules" / "electron-vite" / "bin" / "electron-vite.js"
    subprocess.run([node, str(vite), "build", "--outDir", str(output)], cwd=desktop, env=env, check=True)
    app_directory = stage.parent / "electron-app"
    prepare_desktop_app(desktop, output, app_directory)
    app_version = json.loads((app_directory / "package.json").read_text(encoding="utf-8"))["version"]
    import yaml

    config = yaml.safe_load((desktop / "electron-builder.yml").read_text(encoding="utf-8"))
    config.update({
        "extends": None,
        "directories": {"app": str(app_directory), "output": str(stage.parent / "dist"), "buildResources": str(desktop / "build")},
        "files": ["out/**/*", "package.json", "!**/node_modules/**/*"],
        "extraResources": [{"from": str(stage / "runtime"), "to": "runtime"}, {"from": str(stage / "app"), "to": "app"}],
        "electronDist": str(electron), "npmRebuild": False,
        "electronVersion": json.loads((desktop / "node_modules/electron/package.json").read_text(encoding="utf-8"))["version"],
        "win": {**config.get("win", {}), "signAndEditExecutable": False},
    })
    configuration = stage.parent / "electron-builder.release.json"
    configuration.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    builder = desktop / "node_modules" / "electron-builder" / "out" / "cli" / "cli.js"
    command = [node, str(builder), "--projectDir", str(app_directory), "--config", str(configuration), "--win", "--x64"]
    subprocess.run([*command, "--dir"], cwd=desktop, env=env, check=True)
    unpacked = stage.parent / "dist" / "win-unpacked"
    subprocess.run([node, str(PROJECT_ROOT / "scripts/verify-packaged-desktop.cjs"), str(output),
                    str(unpacked / "resources/app.asar"), app_version,
                    str(stage.parent / "desktop-integrity.json")], cwd=desktop, env=env, check=True)
    # The build guard denies all networking; the native smoke needs its own
    # loopback API. Its runner owns a synthetic profile and blocks remote UI traffic.
    smoke_env = {**env, "NO_PROXY": "localhost,127.0.0.1,::1"}
    smoke_env.pop("NODE_OPTIONS", None)
    subprocess.run([node, str(PROJECT_ROOT / "scripts/verify-windows-desktop.cjs"), str(unpacked),
                    str(stage.parent / "native-verification")], cwd=desktop, env=smoke_env, check=True)
    return subprocess.call([*command, "--prepackaged", str(unpacked)], cwd=desktop, env=env)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pg-dir", default=None, help="PostgreSQL 16 安装目录")
    parser.add_argument("--stage-only", action="store_true", help="只组装，不调用 electron-builder")
    parser.add_argument("--stage", type=Path, help="新的独立 stage；已有目录不会覆盖")
    args = parser.parse_args()

    stage = (args.stage or PROJECT_ROOT / "build" / ("release-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-") + uuid4().hex[:8]) / "stage").resolve()
    stage.mkdir(parents=True, exist_ok=False)
    stage_python(stage)
    stage_pgsql(stage, pg_install_dir(args.pg_dir))
    stage_app(stage)
    print("\n组装结果：")
    report(stage)

    if args.stage_only:
        return 0
    return build_installer(stage)


if __name__ == "__main__":
    sys.exit(main())
