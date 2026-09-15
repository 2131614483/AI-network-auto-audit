"""audit.field.site-checkin-track: aggregate field check-in events into a
daily attendance series.

Consumes a checkin payload (artifact-ref JSON) and emits a metric-series of
per-day check-in counts. Nothing is inferred beyond the given events.
Read-only.
"""
from __future__ import annotations

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

PLUGIN_ID = "audit.field.site-checkin-track"
CAPABILITY = "audit.field.site-checkin-track"
MAX_CHECKINS = 50_000


def handle(envelope: dict[str, Any]) -> dict[str, Any]:
    check_identity(envelope, PLUGIN_ID, CAPABILITY)
    port = envelope.get("checkin-input")
    if not isinstance(port, dict) or not isinstance(port.get("artifact"), dict):
        raise InputRejected("checkin-input reference is missing")
    artifact = port["artifact"]
    roots = allowed_roots()
    read_verified_artifact(artifact, roots)
    payload = read_artifact_json(artifact, roots)
    checkins = payload.get("checkins")
    if not isinstance(checkins, list):
        raise InputRejected("checkin-input requires checkins array")

    per_day: Counter[str] = Counter()
    for item in checkins[:MAX_CHECKINS]:
        if not isinstance(item, dict):
            continue
        ts = str(item.get("ts") or "")
        day = ts[:10]
        if day:
            per_day[day] += 1
    if not per_day:
        raise InputRejected("checkin-input contains no dated events")

    points = [
        {"at": f"{day}T00:00:00+08:00", "value": per_day[day]}
        for day in sorted(per_day)
    ]
    return {
        "contract_id": "metric-series", "contract_version": "1.0.0",
        "series_id": f"checkin-{payload.get('period') or 'daily'}",
        "metric": "checkin_count", "unit": "count", "window_minutes": 1440,
        "points": points,
    }


if __name__ == "__main__":
    raise SystemExit(child_main(handle))
