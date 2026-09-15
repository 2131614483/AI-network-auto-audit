"""Read-only failure diagnostics: cluster failed attempts by what actually broke.

``error_kind`` alone is too coarse to act on.  In this tenant's history the whole
failure surface is two values — ``plugin_failed`` and ``lease_expired`` — but the
54 ``plugin_failed`` rows are not 54 different problems; they collapse into a
handful of real causes once the message is normalised.  So the unit of diagnosis
here is the **signature**, not the kind:

    error_kind   coarse bucket, written by the executor
    signature    first line of the message with volatile parts erased
                 (paths, uuids, hashes, large numbers)

Everything downstream — recurrence, affected runs, sample messages — is keyed on
the signature, because "this keeps happening" is only answerable at that grain.

Two deliberate limits:

* **Messages are data, not markup.**  Control characters are stripped and the
  text is truncated; the interface escapes what remains.  An error message is
  attacker-influenced content in the general case and is treated as such.
* **Recurrence is a fact, not a forecast.**  ``recurrent`` means an older
  occurrence exists *and* a recent one does; it is never inferred from a single
  event, and it is never a probability.

Nothing in this module writes, and nothing here executes a fix.
"""

from __future__ import annotations

import re
from typing import Any

import psycopg2
from psycopg2.extras import register_uuid

register_uuid()  # type: ignore[no-untyped-call]

_UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_LONG_HEX_RE = re.compile(r"\b[0-9a-fA-F]{16,}\b")
_QUOTED_RE = re.compile(r"""(['"])([^'"]{12,}?)\1""")
_NUMBER_RE = re.compile(r"\b\d{3,}\b")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

#: Message text kept per sample.  Long enough to recognise a cause, short enough
#: that one pathological traceback cannot dominate a page.
_MESSAGE_LIMIT = 500
_SIGNATURE_LIMIT = 200


def _clean(message: str) -> str:
    """Strip control characters and clamp length.  Escaping belongs to the view."""

    return _CONTROL_RE.sub("", message)[:_MESSAGE_LIMIT]


def _signature(message: str) -> str:
    """First line of a message with volatile parts replaced by placeholders.

    Order matters: uuids and quoted values are erased before bare long numbers,
    otherwise a uuid's segments get rewritten first and the placeholder is lost.
    """

    text = (message or "").strip()
    if not text:
        return "(no message)"
    first = text.splitlines()[0]
    first = _UUID_RE.sub("<uuid>", first)
    first = _QUOTED_RE.sub(r"\1<value>\1", first)
    first = _LONG_HEX_RE.sub("<hash>", first)
    first = _NUMBER_RE.sub("<n>", first)
    return first.strip()[:_SIGNATURE_LIMIT] or "(no message)"


def _totals(database_url: str, tenant_slug: str) -> dict[str, Any]:
    with psycopg2.connect(database_url) as connection, connection.cursor() as cur:
        cur.execute("SELECT id FROM iam.tenants WHERE slug=%s", (tenant_slug,))
        row = cur.fetchone()
        if row is None:
            raise ValueError(f"tenant not found: {tenant_slug}")
        tenant_id = row[0]
        cur.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),))
        cur.execute(
            """SELECT count(*),count(*) FILTER (WHERE status='succeeded'),
            count(*) FILTER (WHERE status<>'succeeded'),
            count(DISTINCT run_id),
            count(DISTINCT run_id) FILTER (WHERE status<>'succeeded')
            FROM control.node_attempts WHERE tenant_id=%s""",
            (tenant_id,),
        )
        row = cur.fetchone()
        if row is None:  # pragma: no cover - aggregate always returns one row
            raise RuntimeError("failure totals query returned no row")
        attempts, succeeded, failed, runs, failed_runs = row
        return {
            "attempts": attempts,
            "succeeded": succeeded,
            "failed": failed,
            "failure_rate": round(failed / attempts, 6) if attempts else 0.0,
            "runs": runs,
            "runs_with_failure": failed_runs,
        }


def failure_diagnostics(
    database_url: str,
    tenant_slug: str,
    *,
    recent_days: int = 7,
    limit: int = 200,
    sample_limit: int = 3,
) -> dict[str, Any]:
    """Failed attempts grouped by normalised signature, newest groups first.

    ``limit`` bounds the rows read; the summaries are computed over the same
    bounded set and the returned ``scanned`` count says how many rows that was,
    so a truncated view can never be mistaken for the whole population.
    """

    if not 1 <= limit <= 2000:
        raise ValueError("limit must be between 1 and 2000")
    if not 0 <= recent_days <= 3650:
        raise ValueError("recent_days must be between 0 and 3650")
    if not 1 <= sample_limit <= 20:
        raise ValueError("sample_limit must be between 1 and 20")

    with psycopg2.connect(database_url) as connection, connection.cursor() as cur:
        cur.execute("SELECT id FROM iam.tenants WHERE slug=%s", (tenant_slug,))
        row = cur.fetchone()
        if row is None:
            raise ValueError(f"tenant not found: {tenant_slug}")
        tenant_id = row[0]
        cur.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),))
        cur.execute(
            """SELECT attempt_id,run_id,trace_id,node_instance_id,capability,plugin_id,
            attempt_seq,status,error_kind,error_message,created_at,finished_at,
            plugin_version,plan_key
            FROM control.node_attempts
            WHERE tenant_id=%s AND status<>'succeeded'
            ORDER BY created_at DESC LIMIT %s""",
            (tenant_id, limit),
        )
        rows = cur.fetchall()
        cur.execute("SELECT now()")
        now_row = cur.fetchone()
        now = now_row[0] if now_row else None

    groups: dict[tuple[str, str], dict[str, Any]] = {}
    for (
        attempt_id,
        run_id,
        trace_id,
        node_instance_id,
        capability,
        plugin_id,
        attempt_seq,
        status,
        error_kind,
        error_message,
        created_at,
        finished_at,
        plugin_version,
        plan_key,
    ) in rows:
        message = _clean(error_message or "")
        key = (error_kind or "(unclassified)", _signature(message))
        group = groups.setdefault(
            key,
            {
                "error_kind": key[0],
                "signature": key[1],
                "occurrences": 0,
                "runs": set(),
                "nodes": set(),
                "plugins": {},
                "capabilities": {},
                "first_seen": None,
                "last_seen": None,
                "recent_occurrences": 0,
                "samples": [],
                "trace_ids": [],
            },
        )
        group["occurrences"] += 1
        group["runs"].add(str(run_id))
        group["nodes"].add(node_instance_id)
        group["plugins"][plugin_id] = group["plugins"].get(plugin_id, 0) + 1
        group["capabilities"][capability] = group["capabilities"].get(capability, 0) + 1
        seen_at = created_at
        if seen_at is not None:
            group["first_seen"] = seen_at if group["first_seen"] is None else min(group["first_seen"], seen_at)
            group["last_seen"] = seen_at if group["last_seen"] is None else max(group["last_seen"], seen_at)
            if now is not None and (now - seen_at).days <= recent_days:
                group["recent_occurrences"] += 1
        if len(group["samples"]) < sample_limit:
            group["samples"].append(
                {
                    "attempt_id": str(attempt_id),
                    "run_id": str(run_id),
                    "trace_id": str(trace_id) if trace_id is not None else None,
                    "node_instance_id": node_instance_id,
                    "plugin_id": plugin_id,
                    "plugin_version": plugin_version or None,
                    "capability": capability,
                    "attempt_seq": attempt_seq,
                    "status": status,
                    "plan_key": plan_key,
                    "occurred_at": created_at.isoformat() if created_at is not None else None,
                    "message": message,
                }
            )
        if len(group["trace_ids"]) < sample_limit and trace_id is not None:
            group["trace_ids"].append(str(trace_id))

    items: list[dict[str, Any]] = []
    for group in groups.values():
        first_seen = group["first_seen"]
        last_seen = group["last_seen"]
        spans_days = (last_seen - first_seen).days if first_seen is not None and last_seen is not None else 0
        items.append(
            {
                "error_kind": group["error_kind"],
                "signature": group["signature"],
                "occurrences": group["occurrences"],
                "recent_occurrences": group["recent_occurrences"],
                "runs": sorted(group["runs"]),
                "run_count": len(group["runs"]),
                "node_count": len(group["nodes"]),
                "plugins": sorted(group["plugins"].items(), key=lambda item: (-item[1], item[0])),
                "capabilities": sorted(group["capabilities"].items(), key=lambda item: (-item[1], item[0])),
                "first_seen": first_seen.isoformat() if first_seen is not None else None,
                "last_seen": last_seen.isoformat() if last_seen is not None else None,
                "spans_days": spans_days,
                # Recurrence requires an older onset *and* a recent hit; one
                # occurrence is new, not recurrent.
                "recurrent": group["recent_occurrences"] > 0 and group["occurrences"] > group["recent_occurrences"],
                "samples": group["samples"],
                "trace_ids": group["trace_ids"],
                # A locating hint, not an executable step: the trace endpoint and
                # the bundle are the two places the evidence actually lives.
                "locate": {
                    "trace_endpoint": f"/api/v1/observability/trace/{group['trace_ids'][0]}"
                    if group["trace_ids"]
                    else None,
                    "bundle_endpoint": f"/api/v1/observability/bundles/{sorted(group['runs'])[0]}/verify"
                    if group["runs"]
                    else None,
                    "note": "read-only locators; nothing is re-run on your behalf",
                },
            }
        )
    items.sort(key=lambda item: (-item["occurrences"], item["signature"]))

    by_kind: dict[str, int] = {}
    for item in items:
        by_kind[item["error_kind"]] = by_kind.get(item["error_kind"], 0) + item["occurrences"]

    return {
        "totals": _totals(database_url, tenant_slug),
        "summary": {
            "scanned": len(rows),
            "groups": len(items),
            "recurrent_groups": sum(1 for item in items if item["recurrent"]),
            "by_error_kind": by_kind,
            "recent_days": recent_days,
            "truncated": len(rows) >= limit,
        },
        "items": items,
    }
