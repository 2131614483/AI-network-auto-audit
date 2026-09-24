"""Read-only rule registry: what the system runs by, projected from disk.

This answers "按什么规则做事" with four honest sources of truth:

* **contracts** -- the JSON Schema files under ``contracts/jsonschema/``; a
  plugin port contract is only as strong as the schema that names it.
* **skills** -- the ``plugin.protocol.json`` manifests under
  ``plugins/builtin/``; the lifecycle they declare (``verified`` /
  ``contract_only``) is carried verbatim, never upgraded.
* **report_templates** -- the audit-report markdown documents under
  ``审计项目案例报告效果展示/``; these are the human-facing templates a case produces.

Policy sets themselves live in ``policy.policy_sets`` (the database is the
source of truth for them) and are joined by the route, not duplicated here.

This is a filesystem projection, not a second source of truth: it never opens
a schema body beyond what it needs to stat the file, never parses a manifest
into a model it does not already own, and never moves or renames anything.  A
small mtime/file-count cache avoids re-statting every file on each call but
the signature is recomputed from disk every time, so a changed file shows up
immediately.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

#: Project-relative roots that hold rule assets.  A missing root is surfaced in
#: ``missing_roots``, never silently skipped.
SCHEMA_GLOB = "contracts/jsonschema/*.json"
PROJECT_ROOT_DIR = "审计项目案例报告效果展示"


_lock = threading.Lock()
#: (newest_mtime, file_count, payload) cache.
_cache: tuple[float, int, dict[str, Any]] | None = None


def _iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()


def _is_report_template(path: Path) -> bool:
    """A report template is a markdown file whose name says so.

    Deliberately narrow: the whole ``审计项目案例报告效果展示/`` tree is already indexed by the
    library view; here we only surface the documents that read as report
    templates, not every working note under a case.
    """

    return ("报告" in path.name or "模板" in path.name) and path.suffix.lower() == ".md"


def _scan(project_root: Path) -> tuple[dict[str, Any], list[str], float, int]:
    missing: list[str] = []
    newest = 0.0
    total = 0

    contracts: list[dict[str, Any]] = []
    for path in sorted(project_root.glob(SCHEMA_GLOB)):
        if not path.is_file():
            continue
        stat = path.stat()
        total += 1
        newest = max(newest, stat.st_mtime)
        contracts.append(
            {
                "name": path.stem,
                "kind": "json_schema",
                "path": path.relative_to(project_root).as_posix(),
                "modified_at": _iso(stat.st_mtime),
                "status": "registered",
            }
        )
    if not (project_root / "contracts" / "jsonschema").is_dir():
        missing.append("contracts/jsonschema")

    skills: list[dict[str, Any]] = []
    plugin_root = project_root / "plugins" / "builtin"
    if plugin_root.is_dir():
        for proto_path in sorted(plugin_root.glob("*/plugin.protocol.json")):
            if not proto_path.is_file():
                continue
            stat = proto_path.stat()
            total += 1
            newest = max(newest, stat.st_mtime)
            try:
                proto = json.loads(proto_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                proto = {}
            skills.append(
                {
                    "name": str(proto.get("name") or proto_path.parent.name),
                    "kind": str(proto.get("id") or proto_path.parent.name),
                    "path": proto_path.relative_to(project_root).as_posix(),
                    "modified_at": _iso(stat.st_mtime),
                    "status": str(proto.get("lifecycle") or "contract_only"),
                }
            )
    else:
        missing.append("plugins/builtin")

    templates: list[dict[str, Any]] = []
    cases_root = project_root / PROJECT_ROOT_DIR
    if cases_root.is_dir():
        for path in sorted(cases_root.rglob("*.md")):
            if not path.is_file() or not _is_report_template(path):
                continue
            stat = path.stat()
            total += 1
            newest = max(newest, stat.st_mtime)
            templates.append(
                {
                    "name": path.stem,
                    "kind": "report_template",
                    "path": path.relative_to(project_root).as_posix(),
                    "modified_at": _iso(stat.st_mtime),
                    "status": "registered",
                }
            )
    else:
        missing.append(PROJECT_ROOT_DIR)

    payload = {"contracts": contracts, "skills": skills, "report_templates": templates}
    return payload, missing, newest, total


def rules_registry(project_root: Path) -> dict[str, Any]:
    """Filesystem projection of contracts, skills and report templates; read-only.

    ``summary`` counts what is actually on disk.  Policy sets are deliberately
    absent: they live in ``policy.policy_sets`` and are joined by the route so
    the database stays their single source of truth.
    """

    global _cache
    with _lock:
        payload, missing, newest, total = _scan(project_root)
        if _cache is not None and _cache[0] == newest and _cache[1] == total:
            payload = _cache[2]
        else:
            _cache = (newest, total, payload)

    return {
        "summary": {
            "contracts": len(payload["contracts"]),
            "skills": len(payload["skills"]),
            "report_templates": len(payload["report_templates"]),
            "missing_roots": missing,
        },
        **payload,
    }
