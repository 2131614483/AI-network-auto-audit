"""audit.plan.sample-size-compute: compute the required sample size from the
sampling plan.

Consumes a sampling plan (dataset-validation) and emits a sample-size series.
Size = min(population, ceil(population / (1 + population * 0.0025))) — a
deterministic 95%-style bound, capped by the population. Read-only.
"""
from __future__ import annotations

import math
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

PLUGIN_ID = "audit.plan.sample-size-compute"
CAPABILITY = "audit.plan.sample-size-compute"
_ENTRY = re.compile(r"population:(\d+) sample_size:(\d+)")


def handle(envelope: dict[str, Any]) -> dict[str, Any]:
    check_identity(envelope, PLUGIN_ID, CAPABILITY)
    port = envelope.get("sampling-plan")
    if not isinstance(port, dict) or not isinstance(port.get("artifact"), dict):
        raise InputRejected("sampling-plan reference is missing")
    artifact = port["artifact"]
    roots = allowed_roots()
    read_verified_artifact(artifact, roots)
    payload = read_artifact_json(artifact, roots)
    checks = payload.get("checks")
    if not isinstance(checks, list) or not checks:
        raise InputRejected("sampling-plan requires checks array")

    population = 0
    for check in checks:
        if not isinstance(check, dict):
            continue
        match = _ENTRY.search(str(check.get("message") or ""))
        if match:
            population = max(population, int(match.group(1)))
    if population <= 0:
        raise InputRejected("sampling-plan carries no population")

    size = min(population, math.ceil(population / (1 + population * 0.0025)))
    return {
        "contract_id": "metric-series", "contract_version": "1.0.0",
        "series_id": f"sample-size-{payload.get('period') or '2026'}",
        "metric": "sample_size", "unit": "count", "window_minutes": 1440,
        "points": [{"at": "2026-01-01T00:00:00+08:00", "value": float(size)}],
    }


if __name__ == "__main__":
    raise SystemExit(child_main(handle))
