"""Pure roll-up: observation rows → stored stats values (no DB, no clock).

Keeping this deterministic (and independent of the current time) makes the
incrementally-maintained stats and the full ``rebuild`` path provably equal:
both call these functions over the same observation rows and get identical
stored values.

Time decay is deliberately **not** applied here.  The stored ``weight`` is a
time-free base score (usage volume × Wilson confidence); the 90-day half-life
decay is applied at read time against ``last_used_at`` so it naturally ages
without rewriting rows (design §5.3).
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Any

from packages.experience.statistics import display_weight, wilson_lower_bound
from packages.experience.types import EVIDENCE_RUN_LIMIT, STATUS_SUCCEEDED


def _observed_at(row: dict[str, Any]) -> datetime | None:
    value = row.get("observed_at")
    return value if isinstance(value, datetime) else None


def _latency(row: dict[str, Any]) -> int:
    value = row.get("latency_ms")
    return int(value) if value is not None else 0


def _bounds(rows: list[dict[str, Any]]) -> tuple[datetime | None, datetime | None]:
    stamps = [ts for ts in (_observed_at(r) for r in rows) if ts is not None]
    if not stamps:
        return None, None
    return min(stamps), max(stamps)


def _recent_run_ids(rows: list[dict[str, Any]], evidence_limit: int) -> list[str]:
    # Most-recent distinct run ids first (rows with no timestamp sort last);
    # full evidence stays in the append-only observation tables.
    ordered = sorted(
        rows,
        key=lambda r: (r.get("observed_at") is not None, _observed_at(r)),
        reverse=True,
    )
    run_ids: list[str] = []
    for row in ordered:
        rid = str(row.get("run_id"))
        if rid and rid not in run_ids:
            run_ids.append(rid)
        if len(run_ids) >= evidence_limit:
            break
    return run_ids


def edge_stat_values(
    rows: list[dict[str, Any]],
    *,
    evidence_limit: int = EVIDENCE_RUN_LIMIT,
) -> dict[str, Any]:
    """Aggregate one edge's observation rows into stored edge_stats values.

    ``declared`` ORs the per-observation snapshot (did the hand-off match the
    design-time manifests when it happened), so rebuild never depends on the
    current on-disk plugin directory.
    """
    success = sum(1 for r in rows if r.get("status") == STATUS_SUCCEEDED)
    total = len(rows)
    fail = total - success
    declared = any(bool(r.get("declared")) for r in rows)
    first, last = _bounds(rows)
    confidence = wilson_lower_bound(success, total)
    return {
        "success_count": success,
        "fail_count": fail,
        "total_latency_ms": sum(_latency(r) for r in rows),
        "first_used_at": first,
        "last_used_at": last,
        "declared": declared,
        "confidence": confidence,
        "weight": display_weight(total, confidence, 1.0),
        "evidence_run_ids": _recent_run_ids(rows, evidence_limit),
    }


def node_stat_values(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate one (plugin, capability) node-observation row set."""
    success = sum(1 for r in rows if r.get("status") == STATUS_SUCCEEDED)
    total = len(rows)
    fail = total - success
    first, last = _bounds(rows)
    confidence = wilson_lower_bound(success, total)
    errors = Counter(str(r["error_kind"]) for r in rows if r.get("error_kind"))
    return {
        "use_count": total,
        "success_count": success,
        "fail_count": fail,
        "total_latency_ms": sum(_latency(r) for r in rows),
        "error_kind_counts": dict(sorted(errors.items())),
        "first_used_at": first,
        "last_used_at": last,
        "confidence": confidence,
        "weight": display_weight(total, confidence, 1.0),
    }
