"""audit.mandate.notice-generate: generate an audit notice for the
top-ranked project.

Consumes a project snapshot (dataset-validation) and emits an audit-notice
document for the highest-score project. Read-only.
"""
from __future__ import annotations

import re
from typing import Any

from plugins.builtin._child_common import (
    InputRejected,
    allowed_roots,
    check_identity,
    child_main,
    read_artifact_json,
    read_verified_artifact,
)

PLUGIN_ID = "audit.mandate.notice-generate"
CAPABILITY = "audit.mandate.notice-generate"
_ENTRY = re.compile(r"project:(\S+) score:([0-9.]+)")


def handle(envelope: dict[str, Any]) -> dict[str, Any]:
    check_identity(envelope, PLUGIN_ID, CAPABILITY)
    port = envelope.get("project-snapshot")
    if not isinstance(port, dict) or not isinstance(port.get("artifact"), dict):
        raise InputRejected("project-snapshot reference is missing")
    artifact = port["artifact"]
    roots = allowed_roots()
    read_verified_artifact(artifact, roots)
    payload = read_artifact_json(artifact, roots)
    checks = payload.get("checks")
    if not isinstance(checks, list):
        raise InputRejected("project-snapshot requires checks array")

    best = None
    for check in checks:
        if not isinstance(check, dict):
            continue
        match = _ENTRY.search(str(check.get("message") or ""))
        if match and (best is None or float(match.group(2)) > best[1]):
            best = (str(match.group(1)), float(match.group(2)))
    if best is None:
        raise InputRejected("project-snapshot contains no projects")

    return {
        "contract_id": "document-content", "contract_version": "1.0.0",
        "artifact": {
            "title": "审计通知书",
            "project": best[0],
            "score": best[1],
            "status": "issued",
            "body": {
                "notice_no": f"NO-{best[0]}",
                "auditee": f"被审单位-{best[0]}",
                "basis": "年度审计计划",
                "requirement": "请于收到通知书后 5 个工作日内报送相关资料",
            },
        },
        "language": "zh-CN",
    }


if __name__ == "__main__":
    raise SystemExit(child_main(handle))
