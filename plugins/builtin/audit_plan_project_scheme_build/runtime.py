"""audit.plan.project-scheme-build: build the project audit scheme from
high-risk areas and risk advice.

Consumes a high-risk-area series (metric-series) and a risk-advice-set
(document-content) and emits a project-scheme document with scope and
focus sections. Read-only.
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

PLUGIN_ID = "audit.plan.project-scheme-build"
CAPABILITY = "audit.plan.project-scheme-build"


def handle(envelope: dict[str, Any]) -> dict[str, Any]:
    check_identity(envelope, PLUGIN_ID, CAPABILITY)
    roots = allowed_roots()
    risk_port = envelope.get("high-risk-area")
    advice_port = envelope.get("risk-advice-set")
    if not isinstance(risk_port, dict) or not isinstance(risk_port.get("artifact"), dict):
        raise InputRejected("high-risk-area reference is missing")
    read_verified_artifact(risk_port["artifact"], roots)
    risk_payload = read_artifact_json(risk_port["artifact"], roots)

    areas = []
    for point in risk_payload.get("points") or []:
        if isinstance(point, dict) and point.get("value"):
            areas.append(str(point.get("label") or point.get("at") or "高风险领域"))
    if not areas:
        raise InputRejected("high-risk-area requires at least one area")

    advices = []
    if isinstance(advice_port, dict) and isinstance(advice_port.get("artifact"), dict):
        read_verified_artifact(advice_port["artifact"], roots)
        advice_payload = read_artifact_json(advice_port["artifact"], roots)
        body = advice_payload.get("artifact")
        entries = body.get("advices") if isinstance(body, dict) else advice_payload.get("advices")
        if isinstance(entries, list):
            advices = [str(a.get("advice") or a.get("text") or "") for a in entries if isinstance(a, dict)]

    return {
        "contract_id": "document-content", "contract_version": "1.0.0",
        "artifact": {
            "title": "项目审计方案",
            "scope_areas": areas,
            "focus": [{"area": a, "procedure": f"围绕{a}执行专项核查程序"} for a in areas],
            "risk_advices": advices,
            "status": "draft",
        },
        "language": "zh-CN",
    }


if __name__ == "__main__":
    raise SystemExit(child_main(handle))
