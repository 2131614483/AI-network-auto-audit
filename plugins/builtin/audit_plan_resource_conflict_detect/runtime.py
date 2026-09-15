"""audit.plan.resource-conflict-detect: detect members scheduled across
multiple projects.

Consumes a schedule-plan workflow and a project snapshot (dataset-validation)
and emits a conflict-alert dataset-validation whose violations flag members
appearing in more than one project. Read-only.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from typing import Any

from plugins.builtin._child_common import (
    InputRejected,
    allowed_roots,
    check_identity,
    child_main,
    read_artifact_json,
    read_verified_artifact,
)

PLUGIN_ID = "audit.plan.resource-conflict-detect"
CAPABILITY = "audit.plan.resource-conflict-detect"


def handle(envelope: dict[str, Any]) -> dict[str, Any]:
    check_identity(envelope, PLUGIN_ID, CAPABILITY)
    roots = allowed_roots()
    schedule_port = envelope.get("schedule-plan")
    snapshot_port = envelope.get("project-snapshot")
    if not isinstance(schedule_port, dict) or not isinstance(schedule_port.get("artifact"), dict):
        raise InputRejected("schedule-plan reference is missing")
    read_verified_artifact(schedule_port["artifact"], roots)
    schedule = read_artifact_json(schedule_port["artifact"], roots)

    project = "UNKNOWN"
    if isinstance(snapshot_port, dict) and isinstance(snapshot_port.get("artifact"), dict):
        read_verified_artifact(snapshot_port["artifact"], roots)
        snapshot = read_artifact_json(snapshot_port["artifact"], roots)
        checks = snapshot.get("checks")
        if isinstance(checks, list) and checks:
            match = re.search(r"project:(\S+)", str(checks[0].get("message") or ""))
            if match:
                project = match.group(1)

    members: Counter[str] = Counter()
    cross_project: list[str] = []
    for node in schedule.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        props = node.get("props") if isinstance(node.get("props"), dict) else {}
        member_projects: dict[str, set[str]] = {}
        for slot in props.get("slots") or []:
            if not isinstance(slot, dict):
                continue
            member = slot.get("member")
            if not member:
                continue
            members[str(member)] += 1
            member_projects.setdefault(str(member), set()).add(str(slot.get("project") or project))
        for member, projects in member_projects.items():
            if len(projects) > 1 and member not in cross_project:
                cross_project.append(member)
    if not members:
        return {
            "contract_id": "dataset-validation", "contract_version": "1.0.0",
            "snapshot_sha256": hashlib.sha256(
                json.dumps(schedule, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest(),
            "summary": {"valid": True, "checked_columns": 2,
                        "checked_rows": 0, "missing_values": 0},
            "checks": [{"check_id": "columns", "status": "pass",
                        "message": "排班成员 0 人（待人工确认），冲突 0 项"}],
            "violations": [],
        }

    violations = [
        {"check_id": "columns",
         "message": f"成员 {member} 同一排班日内被分配到多个项目，存在资源冲突"}
        for member in sorted(cross_project)
    ]
    return {
        "contract_id": "dataset-validation", "contract_version": "1.0.0",
        "snapshot_sha256": hashlib.sha256(
            json.dumps(schedule, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest(),
        "summary": {"valid": not violations, "checked_columns": 2,
                    "checked_rows": len(members), "missing_values": 0},
        "checks": [{"check_id": "columns", "status": "pass" if not violations else "warn",
                    "message": f"排班成员 {len(members)} 人，冲突 {len(violations)} 项"}],
        "violations": violations,
    }


if __name__ == "__main__":
    raise SystemExit(child_main(handle))
