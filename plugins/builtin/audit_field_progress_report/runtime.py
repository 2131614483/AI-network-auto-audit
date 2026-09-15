"""audit.field.progress-report: report daily progress from the field team's
daily completion form.

Consumes a daily-progress form (document-content JSON) supplied by the field
team — budget hours and completed hours are explicit fields; the completion
rate is derived and never inferred from another series. Read-only.
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

PLUGIN_ID = "audit.field.progress-report"
CAPABILITY = "audit.field.progress-report"


def _num(value: Any, label: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        raise InputRejected(f"daily-progress {label} must be numeric") from None


def handle(envelope: dict[str, Any]) -> dict[str, Any]:
    check_identity(envelope, PLUGIN_ID, CAPABILITY)
    port = envelope.get("daily-progress")
    if not isinstance(port, dict) or not isinstance(port.get("artifact"), dict):
        raise InputRejected("daily-progress reference is missing")
    artifact = port["artifact"]
    roots = allowed_roots()
    read_verified_artifact(artifact, roots)
    payload = read_artifact_json(artifact, roots)
    body = payload.get("artifact")
    if isinstance(body, dict):
        payload = body

    budget = _num(payload.get("budget_hours"), "budget_hours")
    done = _num(payload.get("completed_hours"), "completed_hours")
    if budget <= 0:
        raise InputRejected("daily-progress budget_hours must be positive")
    if done < 0 or done > budget:
        raise InputRejected("daily-progress completed_hours outside [0, budget_hours]")

    at = str(payload.get("date") or "2026-01-15T00:00:00+08:00")
    return {
        "contract_id": "metric-series", "contract_version": "1.0.0",
        "series_id": f"progress-{payload.get('period') or 'daily'}",
        "metric": "completion_rate", "unit": "ratio", "window_minutes": 1440,
        "points": [{"at": at, "value": round(done / budget, 4)}],
    }


if __name__ == "__main__":
    raise SystemExit(child_main(handle))
