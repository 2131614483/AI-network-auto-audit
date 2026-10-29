#!/usr/bin/env python
"""生成发布资产的校验清单。

Release 页面上真正需要的东西只有三件：安装包、它的哈希、以及"这个包是从哪个
提交构建出来的"。缺了 third，用户无法判断下载的包与源码是否对得上；缺了哈希，
无法判断下载是否完整。

产物写到 dist 目录：

    SHA256SUMS.txt           sha256sum -c 可直接校验
    release-manifest.json    版本、提交、构建时间、迁移 head、每个文件的哈希

用法：

    python scripts/make-release-manifest.py
    python scripts/make-release-manifest.py --dist desktop/dist
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# 只收真正要给用户的文件；blockmap / builder-debug.yml 之类的构建中间件不进清单。
RELEASE_SUFFIXES = (".exe", ".msi", ".zip", ".tar.gz", ".whl")


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git(*args: str) -> str:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""


def migration_head() -> str:
    """最新迁移的 revision id（按文件名排序，文件名前缀即顺序）。"""
    versions = sorted((PROJECT_ROOT / "migrations" / "versions").glob("*.py"))
    if not versions:
        return ""
    return versions[-1].stem


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成发布清单与校验文件")
    parser.add_argument("--dist", default="desktop/dist", help="产物目录")
    args = parser.parse_args(argv)

    dist = (PROJECT_ROOT / args.dist).resolve()
    if not dist.is_dir():
        print(f"产物目录不存在：{dist}", file=sys.stderr)
        return 1

    artifacts = sorted(
        path for path in dist.iterdir() if path.is_file() and path.name.endswith(RELEASE_SUFFIXES)
    )
    if not artifacts:
        print(f"{dist} 里没有可发布的产物（找过 {', '.join(RELEASE_SUFFIXES)}）", file=sys.stderr)
        return 1

    package = json.loads((PROJECT_ROOT / "desktop" / "package.json").read_text(encoding="utf-8"))
    entries = []
    for artifact in artifacts:
        entries.append(
            {
                "name": artifact.name,
                "bytes": artifact.stat().st_size,
                "sha256": sha256_of(artifact),
            }
        )

    manifest = {
        "product": package.get("name", "audit-network"),
        "version": package.get("version", ""),
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "commit": git("rev-parse", "HEAD"),
        "commit_subject": git("log", "-1", "--pretty=%s"),
        "branch": git("rev-parse", "--abbrev-ref", "HEAD"),
        "dirty": bool(git("status", "--porcelain")),
        "migration_head": migration_head(),
        "artifacts": entries,
    }

    (dist / "release-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    sums = "\n".join(f"{entry['sha256']}  {entry['name']}" for entry in entries) + "\n"
    (dist / "SHA256SUMS.txt").write_text(sums, encoding="utf-8")

    print(f"版本      {manifest['version']}")
    print(f"提交      {manifest['commit'][:12] or '（非 git 工作区）'}"
          f"{'（有未提交改动）' if manifest['dirty'] else ''}")
    print(f"迁移 head {manifest['migration_head']}")
    print()
    for entry in entries:
        print(f"{entry['sha256']}  {entry['name']}  ({entry['bytes'] / 1048576:.1f} MB)")
    print()
    print(f"已写出：{dist / 'release-manifest.json'}")
    print(f"已写出：{dist / 'SHA256SUMS.txt'}")
    if manifest["dirty"]:
        print()
        print("警告：工作区有未提交改动，这份清单描述的构建无法由仓库复现。", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
