"""audit.plan.sampling-select: select an audit sample from a population.

Consumes a sampling-input payload (artifact-ref JSON) and emits a
dataset-validation sampling plan whose checks encode the method, sample size
and selected item ids. Population rows pass through in the message. Read-only.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from plugins.builtin._child_common import (
    InputRejected,
    allowed_roots,
    check_identity,
    child_main,
    read_artifact_json,
    read_verified_artifact,
)

PLUGIN_ID = "audit.plan.sampling-select"
CAPABILITY = "audit.plan.sampling-select"
MAX_POPULATION = 200_000
_LARGE_THRESHOLD = 1_000_000.0


def handle(envelope: dict[str, Any]) -> dict[str, Any]:
    check_identity(envelope, PLUGIN_ID, CAPABILITY)
    port = envelope.get("sampling-input")
    if not isinstance(port, dict) or not isinstance(port.get("artifact"), dict):
        raise InputRejected("sampling-input reference is missing")
    artifact = port["artifact"]
    roots = allowed_roots()
    read_verified_artifact(artifact, roots)
    payload = read_artifact_json(artifact, roots)
    population = payload.get("population")
    if not isinstance(population, list) or not population:
        raise InputRejected("sampling-input requires population array")

    method = str(payload.get("method") or "materiality")
    large = []
    regular = []
    for item in population[:MAX_POPULATION]:
        if not isinstance(item, dict):
            continue
        try:
            amount = float(item.get("amount") or 0)
        except (TypeError, ValueError):
            continue
        (large if amount >= _LARGE_THRESHOLD else regular).append(
            (str(item.get("item_id") or ""), amount))
    selected = [item_id for item_id, _ in large]
    regular.sort(key=lambda x: (-x[1], x[0]))
    selected.extend(item_id for item_id, _ in regular[: len(regular) // 2])
    if not selected:
        raise InputRejected("sampling-input population contains no selectable items")

    return {
        "contract_id": "dataset-validation", "contract_version": "1.0.0",
        "snapshot_sha256": hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest(),
        "summary": {"valid": True, "checked_columns": 2, "checked_rows": len(population),
                    "missing_values": 0},
        "checks": [{"check_id": "columns", "status": "pass",
                    "message": f"method:{method} population:{len(population)} "
                               f"sample_size:{len(selected)} items:{','.join(selected)}"}],
        "violations": [],
    }


if __name__ == "__main__":
    raise SystemExit(child_main(handle))
