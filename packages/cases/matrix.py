"""Read-only case x stage matrix over ``审计项目案例/``.

The three audit cases are organised on disk as directories named ``00_`` …
``08_``, which *is* the stage machine -- nine stages, zero configuration.  This
view turns that on-disk convention into a matrix: one row per case, nine
columns (one per stage) showing the file count, with a dedicated report table
below.

Two honesty rules are load-bearing here:

* **A missing stage still shows.**  Every case always reports all nine stages;
  a stage with no files is ``count=0`` rather than being dropped.  Hiding an
  empty stage would read as "this stage exists and is done".
* **The reports are counted, not copied.**  Report lines, bytes, modification
  time and two simple quality gates (the share of the §7-§9 result chapters,
  and whether the v1.1 mandatory sections are present) are derived on read.
  Nothing is written back, and the seventy markdown files are not duplicated
  into a database table.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

CASES_ROOT = "审计项目案例"

#: The nine stages, in order.  A directory whose name starts with the two-digit
#: prefix belongs to that stage, regardless of its trailing label -- ``07_审计报告``
#: and ``07_审计成果`` both count toward stage 07.
STAGES: tuple[tuple[str, str], ...] = (
    ("00", "00_项目立项"),
    ("01", "01_被审计单位提供资料"),
    ("02", "02_风险评估"),
    ("03", "03_审计计划"),
    ("04", "04_实质性程序"),
    ("05", "05_报表编制"),
    ("06", "06_发现与调整"),
    ("07", "07_审计成果"),
    ("08", "08_归档"),
)

_SECTION_7_PREFIXES = ("7 ", "7.", "7、", "七")
_SECTION_AFTER_7_PREFIXES = ("10 ", "10.", "10、", "十", "附")
_V11_REQUIRED = ("审计意见", "管理层声明")


def _iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()


def _md_count(directory: Path) -> int:
    if not directory.is_dir():
        return 0
    return sum(1 for path in directory.rglob("*.md") if path.is_file())


def _is_h2(line: str) -> str:
    stripped = line.strip()
    if stripped.startswith("## "):
        return stripped[3:].strip()
    return ""


def _report_quality(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    total = len(lines)

    start = None
    for index, line in enumerate(lines):
        heading = _is_h2(line)
        if heading.startswith(_SECTION_7_PREFIXES):
            start = index
            break
    end = total
    if start is not None:
        for index in range(start + 1, total):
            heading = _is_h2(lines[index])
            if heading.startswith(_SECTION_AFTER_7_PREFIXES):
                end = index
                break
        share = (end - start) / total if total else 0.0
    else:
        share = 0.0

    return {
        "lines": total,
        "m7_9_share": round(share, 4),
        "v11_ok": all(term in text for term in _V11_REQUIRED),
    }


def _reports_for(case_dir: Path, project_root: Path) -> list[dict[str, Any]]:
    reports: list[dict[str, Any]] = []
    for path in sorted(case_dir.rglob("*.md")):
        if not path.is_file() or "审计报告" not in path.name:
            continue
        stat = path.stat()
        quality = _report_quality(path)
        reports.append(
            {
                "path": path.relative_to(project_root).as_posix(),
                "lines": quality["lines"],
                "bytes": stat.st_size,
                "modified_at": _iso(stat.st_mtime),
                "m7_9_share": quality["m7_9_share"],
                "v11_ok": quality["v11_ok"],
            }
        )
    return reports


def case_matrix(project_root: Path, *, root_name: str = CASES_ROOT) -> dict[str, Any]:
    """One row per case, nine stage columns plus the report table; read-only."""

    cases_root = project_root / root_name
    cases: list[dict[str, Any]] = []
    if cases_root.is_dir():
        for case_dir in sorted(path for path in cases_root.iterdir() if path.is_dir() and not path.name.startswith("_")):
            valid_keys = {key for key, _ in STAGES}
            stage_counts: dict[str, int] = {}
            for child in case_dir.iterdir():
                if not child.is_dir():
                    continue
                name = child.name
                if len(name) >= 3 and name[2] == "_" and name[:2] in valid_keys:
                    stage_counts[name[:2]] = stage_counts.get(name[:2], 0) + _md_count(child)

            stages = [
                {"key": key, "name": name, "count": stage_counts.get(key, 0)}
                for key, name in STAGES
            ]
            reports = _reports_for(case_dir, project_root)
            cases.append(
                {
                    "case": case_dir.name,
                    "stages": stages,
                    "file_count": sum(stage["count"] for stage in stages),
                    "report_count": len(reports),
                    "reports": reports,
                }
            )
    return {"cases": cases}
