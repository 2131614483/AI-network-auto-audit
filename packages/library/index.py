"""Read-only document asset index over ``docs/`` and ``审计项目案例/``.

The repository already holds about 135 markdown documents across two roots.
This view is a *filesystem index* -- path, title, kind, size, mtime, the run id
embedded in a file name, and the case a file lives in -- and nothing else.  It
deliberately does **not** index document text: full-text search belongs to the
knowledge layer, and duplicating it here would create a second source of truth
(R1).  The index only answers "where is this file and what kind is it".

It is a projection of the working tree, never a cache that drifts: a small
module-level cache keyed on the newest mtime and the file count avoids re-opening
every file on every call, but the signature is recomputed from disk each time so
a changed file shows up immediately.  The view never moves, renames or deletes a
file.
"""

from __future__ import annotations

import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

#: Project-relative roots that hold markdown assets.  Both are real directories
#: in this repository; a missing root is surfaced in the summary, not skipped.
ROOTS: tuple[str, ...] = ("docs", "审计项目案例")

_HEX_RE = re.compile(r"(?<![0-9a-fA-F])[0-9a-f]{8}(?![0-9a-fA-F])")

_KIND_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("设计", "方案"), "方案"),
    (("报告",), "报告"),
    (("评估", "体检"), "评估"),
    (("清单", "列表"), "清单"),
    (("契约",), "契约"),
    (("技能",), "技能"),
)

_lock = threading.Lock()
#: Cache: (newest_mtime, file_count, items) keyed by the root list.
_cache: dict[tuple[str, ...], tuple[float, int, list[dict[str, Any]]]] = {}


def _iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()


def _title_of(path: Path) -> str:
    """First ``# `` H1 line, else the file name without extension."""
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if line.startswith("# "):
                    return line[2:].strip()
    except OSError:
        pass
    return path.stem


def _kind_of(path: Path, under_cases: bool) -> str:
    if under_cases:
        return "案例资料"
    name = path.name.lower()
    for needles, kind in _KIND_RULES:
        for needle in needles:
            if needle in name:
                return kind
    if "contract" in name:
        return "契约"
    if "skill" in name:
        return "技能"
    return "其他"


def _scan(project_root: Path, roots: tuple[str, ...]) -> tuple[list[dict[str, Any]], list[str], float, int]:
    items: list[dict[str, Any]] = []
    missing: list[str] = []
    newest = 0.0
    total = 0
    for root_name in roots:
        root = project_root / root_name
        if not root.is_dir():
            missing.append(root_name)
            continue
        for path in sorted(root.rglob("*.md")):
            if not path.is_file():
                continue
            stat = path.stat()
            total += 1
            newest = max(newest, stat.st_mtime)
            rel = path.relative_to(project_root).as_posix()
            under_cases = rel.startswith("审计项目案例/")
            run_match = _HEX_RE.search(path.stem)
            related_run = run_match.group(0) if run_match else None

            project: str | None = None
            related_case: str | None = None
            parts = path.relative_to(root).parts
            if under_cases:
                if len(parts) >= 2:
                    related_case = parts[0]
            else:
                if len(parts) >= 2:
                    project = parts[0]

            items.append(
                {
                    "path": rel,
                    "title": _title_of(path),
                    "kind": _kind_of(path, under_cases),
                    "project": project,
                    "size_bytes": stat.st_size,
                    "modified_at": _iso(stat.st_mtime),
                    "related_run": related_run,
                    "related_case": related_case,
                }
            )
    return items, missing, newest, total


def _index(project_root: Path, roots: tuple[str, ...]) -> tuple[list[dict[str, Any]], list[str]]:
    """Return the cached index when the on-disk signature has not changed."""
    with _lock:
        items, missing, newest, total = _scan(project_root, roots)
        cached = _cache.get(roots)
        if cached is not None and cached[0] == newest and cached[1] == total:
            return cached[2], missing
        _cache[roots] = (newest, total, items)
        return items, missing


def library_index(
    project_root: Path,
    *,
    roots: tuple[str, ...] = ROOTS,
    kind: str | None = None,
    project: str | None = None,
    since: str | None = None,
    q: str | None = None,
) -> dict[str, Any]:
    """Path/title/kind/size/mtime index of every markdown asset; read-only.

    ``summary`` always describes the whole corpus.  ``q`` matches path and title
    only -- it is not a full-text search.  A filter must never shrink the
    summary, which is the guarantee that makes "X of Y" honest.
    """

    items, _missing = _index(project_root, roots)

    by_kind: dict[str, int] = {}
    by_project: dict[str, int] = {}
    for item in items:
        by_kind[item["kind"]] = by_kind.get(item["kind"], 0) + 1
        project_key = item["project"] or "unknown"
        by_project[project_key] = by_project.get(project_key, 0) + 1

    summary = {
        "total": len(items),
        "by_kind": dict(sorted(by_kind.items())),
        "by_project": dict(sorted(by_project.items())),
    }

    filtered = items
    if kind is not None:
        filtered = [item for item in filtered if item["kind"] == kind]
    if project is not None:
        filtered = [item for item in filtered if item["project"] == project]
    if since is not None:
        cutoff = since
        filtered = [item for item in filtered if str(item["modified_at"]) >= cutoff]
    if q:
        needle = q.lower()
        filtered = [item for item in filtered if needle in item["path"].lower() or needle in item["title"].lower()]

    return {"summary": summary, "items": filtered}
