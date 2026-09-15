"""audit.plan.annual-plan-build: build the annual audit plan from the
priority order.

Consumes a priority-order series (metric-series) and emits an annual-plan
document with ranked project entries and their planned quarters. Read-only.
"""
from __future__ import annotations

from typing import Any

from plugins.builtin._child_common import (
    InputRejected,
    allowed_roots,
    check_identity,
    child_main,
    read_artifact_json,
    read_verified_artifact,
)

PLUGIN_ID = "audit.plan.annual-plan-build"
CAPABILITY = "audit.plan.annual-plan-build"
_QUARTERS = ("Q1", "Q2", "Q3", "Q4")


def handle(envelope: dict[str, Any]) -> dict[str, Any]:
    check_identity(envelope, PLUGIN_ID, CAPABILITY)
    port = envelope.get("priority-order")
    if not isinstance(port, dict) or not isinstance(port.get("artifact"), dict):
        raise InputRejected("priority-order reference is missing")
    artifact = port["artifact"]
    roots = allowed_roots()
    read_verified_artifact(artifact, roots)
    payload = read_artifact_json(artifact, roots)
    points = payload.get("points")
    if not isinstance(points, list) or not points:
        raise InputRejected("priority-order requires points array")

    projects = []
    for index, point in enumerate(points, start=1):
        if not isinstance(point, dict):
            continue
        try:
            score = float(point.get("value") or 0)
        except (TypeError, ValueError):
            continue
        projects.append({
            "rank": index,
            "project": f"PR-{index:04d}",
            "score": score,
            "quarter": _QUARTERS[(index - 1) % 4],
        })
    if not projects:
        raise InputRejected("priority-order contains no projects")

    return {
        "contract_id": "document-content", "contract_version": "1.0.0",
        "artifact": {
            "title": "年度审计计划",
            "year": "2026",
            "summary": {"project_count": len(projects),
                        "by_quarter": {q: sum(1 for p in projects if p["quarter"] == q)
                                       for q in _QUARTERS}},
            "projects": projects,
        },
        "language": "zh-CN",
    }


if __name__ == "__main__":
    raise SystemExit(child_main(handle))
