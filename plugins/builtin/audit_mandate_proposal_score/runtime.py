"""audit.mandate.proposal-score: score proposals by a deterministic rubric.

Consumes a proposal-set (document-content) and emits a metric-series with
one point per proposal. Score = base by priority (high 85 / medium 70 /
low 55) minus alignment-penalty-free; basis is recorded in the series id.
Read-only.
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

PLUGIN_ID = "audit.mandate.proposal-score"
CAPABILITY = "audit.mandate.proposal-score"
_SCORE_BY_PRIORITY = {"high": 85.0, "medium": 70.0, "low": 55.0}
MAX_PROPOSALS = 10_000


def handle(envelope: dict[str, Any]) -> dict[str, Any]:
    check_identity(envelope, PLUGIN_ID, CAPABILITY)
    port = envelope.get("proposal-set")
    if not isinstance(port, dict) or not isinstance(port.get("artifact"), dict):
        raise InputRejected("proposal-set reference is missing")
    artifact = port["artifact"]
    roots = allowed_roots()
    read_verified_artifact(artifact, roots)
    payload = read_artifact_json(artifact, roots)
    artifact_body = payload.get("artifact")
    proposals = artifact_body.get("proposals") if isinstance(artifact_body, dict) else payload.get("proposals")
    if not isinstance(proposals, list):
        raise InputRejected("proposal-set requires proposals array")

    points = []
    for index, proposal in enumerate(proposals[:MAX_PROPOSALS], start=1):
        if not isinstance(proposal, dict):
            continue
        priority = str(proposal.get("priority") or "medium")
        score = _SCORE_BY_PRIORITY.get(priority, 70.0)
        points.append({
            "at": f"2026-01-{index:02d}T00:00:00+08:00",
            "value": score,
        })
    if not points:
        raise InputRejected("proposal-set contains no proposals")

    return {
        "contract_id": "metric-series", "contract_version": "1.0.0",
        "series_id": "proposal-score-2026",
        "metric": "proposal_score", "unit": "score", "window_minutes": 1440,
        "points": points,
    }


if __name__ == "__main__":
    raise SystemExit(child_main(handle))
