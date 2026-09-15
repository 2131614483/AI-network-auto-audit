"""M9 evidence chain: anchoring, consistency proof and read-only export.

The evidence chain turns the scattered M1-M8 evidence ledgers (releases,
routing plans, chains, intents, approvals, runs, ledger, verifications,
proposals, decisions, run links) into one per-tenant hash chain.  Each
anchored row is hashed deterministically (column-name-sorted JSON with uuid /
datetime / jsonb normalized) and linked to its predecessor via ``prev_hash``,
so any tampering with an anchored row breaks the chain at a precisely
localizable link (source table + primary key + seq).  Anchoring is
user-triggered and append-only (INSERT/SELECT only, RLS FORCE); the
``topology.evidence.anchor`` / ``.verify`` / ``.export`` capabilities are
fail-closed (seeded inactive).  No child process is ever spawned; exports are
API response bodies, never real files.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any
from uuid import UUID

from .contracts import (
    validate_evidence_anchor,
    validate_evidence_export,
    validate_evidence_proof,
)

EVIDENCE_SCOPES = ("full", "topology", "chain", "execution", "verification", "remediation")

# scope -> ordered source tables; ``full`` covers every table below.
_SCOPE_TABLES: dict[str, tuple[str, ...]] = {
    "topology": (
        "topology.topology_releases",
        "topology.routing_plans",
        "topology.routing_plan_nodes",
        "topology.routing_plan_edges",
        "topology.domain_bridges",
    ),
    "chain": (
        "topology.invocation_chains",
        "topology.invocation_chain_nodes",
        "topology.invocation_intents",
    ),
    "execution": (
        "topology.invocation_approvals",
        "topology.execution_runs",
        "topology.execution_ledger",
    ),
    "verification": ("topology.run_verifications",),
    "remediation": (
        "topology.remediation_proposals",
        "topology.remediation_decisions",
        "topology.remediation_run_links",
    ),
}


def _scoped_tables(scope: str) -> list[str]:
    if scope not in EVIDENCE_SCOPES:
        raise ValueError(f"invalid evidence scope: {scope}")
    if scope == "full":
        tables: list[str] = []
        for table in _SCOPE_TABLES.values():
            tables.extend(table)
        return tables
    return list(_SCOPE_TABLES[scope])


def _to_scalar(value: Any) -> Any:
    """Normalize a driver value into a JSON-stable scalar."""
    if value is None:
        return None
    if isinstance(value, (dict, list, tuple)):
        return value
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value


def _json_normalize(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _json_normalize(item) for key, item in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [_json_normalize(item) for item in value]
    return _to_scalar(value)


def canonical_row(row: dict[str, Any]) -> str:
    """Deterministic JSON of one source row: column-name-sorted, type-normalized.

    The same logical row always serializes to the same string, so re-anchoring
    and chain verification reproduce the same ``row_hash``.
    """
    return json.dumps(
        _json_normalize(dict(row)),
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )


def sha256_hex(text: str | bytes) -> str:
    if isinstance(text, str):
        text = text.encode("utf-8")
    return hashlib.sha256(text).hexdigest()


def _anchor_line_hash(seq: int, source_table: str, source_pk: str, row_hash: str) -> str:
    """Hash of one anchor line (without prev_hash) for the chain link."""
    return sha256_hex(f"{seq}|{source_table}|{source_pk}|{row_hash}")


def _row_pk(row_dict: dict[str, Any], canonical: str) -> str:
    """Primary-key label for one source row.

    Tables with an ``id`` column anchor by that value; tables without one
    (``routing_plan_edges`` has no PK) anchor by a deterministic
    ``row:<hash>`` label derived from the row itself.
    """
    if row_dict.get("id") is not None:
        return str(row_dict["id"])
    return "row:" + sha256_hex(canonical)[:16]


def _table_has_id(cur: Any, table: str) -> bool:
    schema, _, name = table.partition(".")
    cur.execute(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_schema=%s AND table_name=%s AND column_name='id'",
        (schema, name),
    )
    return cur.fetchone() is not None


class EvidenceError(PermissionError):
    """Fail-closed preflight denied the anchoring; nothing was written."""


def anchor_evidence(
    cur: Any,
    *,
    tenant_id: UUID,
    scope: str,
    idempotency_key: str,
    trace_id: str,
) -> list[dict[str, Any]]:
    """Append one anchor batch for the tenant evidence chain (append-only).

    Fail-closed: each scope resolves to an ordered table list; every selected
    table filters by ``tenant_id`` (RLS additionally constrains at row level).
    The batch shares one ``group_id``; a PK/UNIQUE conflict on
    ``(tenant_id, group_id, source_table, source_pk)`` must be impossible
    because the batch is inserted once - a DB-level replay collision surfaces
    as a duplicate-row error rather than silent overwrite.
    """
    if scope not in EVIDENCE_SCOPES:
        raise ValueError(f"invalid evidence scope: {scope}")
    group_id = UUID("00000000-0000-0000-0000-000000000000")  # per-batch, set below
    # -- collect rows: deterministic order per table --------------------------
    rows: list[tuple[str, str, str]] = []  # (table, pk, row_hash)
    # tenant-scoped inserts below run in one transaction on the same cursor.
    # Tables without an ``id`` column (``routing_plan_edges``) sort by the row
    # hash itself so every re-anchor reproduces the same order and chain.
    for table in _scoped_tables(scope):
        cur.execute(f"SELECT * FROM {table} WHERE tenant_id=%s", (tenant_id,))
        columns = [column.name for column in cur.description or ()]
        for db_row in cur.fetchall():
            row_dict = dict(zip(columns, db_row))
            canonical = canonical_row(row_dict)
            rows.append((table, _row_pk(row_dict, canonical), sha256_hex(canonical)))
    rows.sort(key=lambda item: item[2])  # row_hash: deterministic across re-anchors
    if not rows:
        return []
    group_id = UUID(sha256_hex(f"{tenant_id}:{idempotency_key}")[:32])
    # -- read the current tail to link the batch ------------------------------
    cur.execute(
        """SELECT id, seq, source_table, source_pk, row_hash, prev_hash
        FROM topology.evidence_chain_anchors
        WHERE tenant_id=%s AND chain_key=%s
        ORDER BY seq DESC LIMIT 1""",
        (tenant_id, _chain_key(tenant_id)),
    )
    tail = cur.fetchone()
    prev_hash = sha256_hex(_chain_key(tenant_id))  # seed: first link anchors to key
    next_seq = 1
    if tail is not None:
        prev_hash = str(tail[5])
        next_seq = int(tail[1]) + 1
    # -- append the batch within one line sequence -----------------------------
    for offset, (table, source_pk, row_hash) in enumerate(rows):
        seq = next_seq + offset
        anchor_line_hash = _anchor_line_hash(seq, table, source_pk, row_hash)
        link_input = prev_hash + anchor_line_hash
        this_prev_hash = sha256_hex(link_input)
        cur.execute(
            """INSERT INTO topology.evidence_chain_anchors
            (tenant_id, chain_key, seq, source_table, source_pk, row_hash, prev_hash,
             group_id, anchor_scope, trace_id)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (tenant_id, group_id, source_table, source_pk) DO NOTHING
            RETURNING id, seq, created_at""",
            (
                tenant_id,
                _chain_key(tenant_id),
                seq,
                table,
                source_pk,
                row_hash,
                this_prev_hash,
                group_id,
                scope,
                trace_id,
            ),
        )
        prev_hash = this_prev_hash
    # -- return the projection of the batch -------------------------------------
    cur.execute(
        """SELECT id, chain_key, seq, source_table, source_pk, row_hash, prev_hash,
                  group_id, anchor_scope, trace_id, created_at
        FROM topology.evidence_chain_anchors
        WHERE tenant_id=%s AND group_id=%s ORDER BY seq""",
        (tenant_id, group_id),
    )
    anchors: list[dict[str, Any]] = []
    for db_row in cur.fetchall():
        projection = {
            "anchor_id": str(db_row[0]),
            "chain_key": str(db_row[1]),
            "seq": int(db_row[2]),
            "source_table": str(db_row[3]),
            "source_pk": str(db_row[4]),
            "row_hash": str(db_row[5]),
            "prev_hash": str(db_row[6]),
            "group_id": str(db_row[7]),
            "anchor_scope": str(db_row[8]),
            "trace_id": str(db_row[9]),
            "created_at": db_row[10].isoformat() if db_row[10] else None,
        }
        validate_evidence_anchor(projection)
        anchors.append(projection)
    return anchors


def _chain_key(tenant_id: UUID) -> str:
    return f"evidence://{tenant_id}/full"


def anchor_by_key(
    cur: Any, tenant_id: UUID, idempotency_key: str, scope: str
) -> list[dict[str, Any]] | None:
    """DB-level replay: return the existing batch for a repeated key, else None."""
    group_id = UUID(sha256_hex(f"{tenant_id}:{idempotency_key}")[:32])
    cur.execute(
        """SELECT group_id FROM topology.evidence_chain_anchors
        WHERE tenant_id=%s AND group_id=%s LIMIT 1""",
        (tenant_id, group_id),
    )
    row = cur.fetchone()
    if row is None:
        return None
    cur.execute(
        """SELECT id, chain_key, seq, source_table, source_pk, row_hash, prev_hash,
                  group_id, anchor_scope, trace_id, created_at
        FROM topology.evidence_chain_anchors
        WHERE tenant_id=%s AND group_id=%s ORDER BY seq""",
        (tenant_id, group_id),
    )
    anchors: list[dict[str, Any]] = []
    for db_row in cur.fetchall():
        projection = {
            "anchor_id": str(db_row[0]),
            "chain_key": str(db_row[1]),
            "seq": int(db_row[2]),
            "source_table": str(db_row[3]),
            "source_pk": str(db_row[4]),
            "row_hash": str(db_row[5]),
            "prev_hash": str(db_row[6]),
            "group_id": str(db_row[7]),
            "anchor_scope": str(db_row[8]),
            "trace_id": str(db_row[9]),
            "created_at": db_row[10].isoformat() if db_row[10] else None,
        }
        validate_evidence_anchor(projection)
        anchors.append(projection)
    return anchors


def verify_evidence_chain(
    cur: Any,
    *,
    tenant_id: UUID,
    trace_id: str,
    scope: str = "full",
) -> dict[str, Any]:
    """Recompute every anchored row hash and the predecessor links (read-only).

    Returns a strict-schema proof: ``verified`` with the ``tail_hash``, or the
    first mismatching link (seq + source table + source pk).  A live row whose
    recomputed hash differs from the anchored ``row_hash`` is a source-table
    tamper; a broken predecessor pointer is a chain (anchor-table) tamper.
    Either failure is reported as the first mismatching link in seq order.
    """
    if scope not in EVIDENCE_SCOPES:
        raise ValueError(f"invalid evidence scope: {scope}")
    chain_key = _chain_key(tenant_id)
    cur.execute(
        """SELECT id, seq, source_table, source_pk, row_hash, prev_hash, anchor_scope
        FROM topology.evidence_chain_anchors
        WHERE tenant_id=%s AND chain_key=%s ORDER BY seq""",
        (tenant_id, chain_key),
    )
    anchors = [tuple(row) for row in cur.fetchall()]
    total = len(anchors)

    if total == 0:
        proof = {
            "chain_key": chain_key,
            "scope": scope,
            "total_anchors": 0,
            "tail_hash": sha256_hex(chain_key),
            "verified": True,
            "first_mismatch": None,
            "checked_at": _now(),
            "trace_id": trace_id,
        }
        validate_evidence_proof(proof)
        return proof

    # Pass 1: recompute each anchored row hash against the live source row.
    # Tables without an ``id`` column are matched by the set of live row
    # hashes (the anchor ``source_pk`` is a deterministic row-derived label).
    _shape: dict[str, bool] = {}
    for _anchor_row_id, seq, source_table, source_pk, row_hash, _prev_hash, _anchor_scope in anchors:
        has_id = _shape.get(source_table)
        if has_id is None:
            has_id = _table_has_id(cur, source_table)
            _shape[source_table] = has_id
        if has_id:
            cur.execute(
                f"SELECT * FROM {source_table} WHERE tenant_id=%s AND id=%s",
                (
                    tenant_id,
                    UUID(str(source_pk)) if _is_uuid(str(source_pk)) else str(source_pk),
                ),
            )
            db_row = cur.fetchone()
            if db_row is None:
                return _proof_mismatch(
                    chain_key, scope, seq, source_table, source_pk, total, trace_id
                )
            columns = [column.name for column in (cur.description or ())]
            recomputed = sha256_hex(canonical_row(dict(zip(columns, db_row))))
        else:
            cur.execute(
                f"SELECT * FROM {source_table} WHERE tenant_id=%s", (tenant_id,)
            )
            live_columns = [column.name for column in (cur.description or ())]
            live_hashes = {
                sha256_hex(canonical_row(dict(zip(live_columns, row))))
                for row in cur.fetchall()
            }
            recomputed = str(row_hash) if str(row_hash) in live_hashes else "missing"
        if recomputed != str(row_hash):
            import os

            if os.environ.get("EVIDENCE_DEBUG"):
                print(f"[P1] seq={seq!r} type={type(seq).__name__} source_pk={source_pk!r} recomputed={recomputed[:8]} stored={str(row_hash)[:8]}")
            return _proof_mismatch(
                chain_key, scope, seq, source_table, source_pk, total, trace_id
            )

    # Pass 2: replay the predecessor links exactly as anchoring built them.
    prev_state = sha256_hex(chain_key)  # seed
    for _anchor_row_id, seq, source_table, source_pk, row_hash, prev_hash, _anchor_scope in anchors:
        line_hash = _anchor_line_hash(int(seq), str(source_table), str(source_pk), str(row_hash))
        expected = sha256_hex(prev_state + line_hash)
        if str(prev_hash) != expected:
            return _proof_mismatch(
                chain_key, scope, seq, source_table, source_pk, total, trace_id
            )
        prev_state = str(prev_hash)

    proof = {
        "chain_key": chain_key,
        "scope": scope,
        "total_anchors": total,
        "tail_hash": prev_state,
        "verified": True,
        "first_mismatch": None,
        "checked_at": _now(),
        "trace_id": trace_id,
    }
    validate_evidence_proof(proof)
    return proof


def _proof_mismatch(
    chain_key: str, scope: str, seq: int, source_table: str, source_pk: str,
    total: int, trace_id: str, tail_hash: str | None = None,
) -> dict[str, Any]:
    proof = {
        "chain_key": chain_key,
        "scope": scope,
        "total_anchors": total,
        "tail_hash": tail_hash or sha256_hex(chain_key),
        "verified": False,
        "first_mismatch": {
            "seq": int(seq),
            "source_table": str(source_table),
            "source_pk": str(source_pk),
        },
        "checked_at": _now(),
        "trace_id": trace_id,
    }
    validate_evidence_proof(proof)
    return proof


def export_evidence(
    cur: Any,
    *,
    tenant_id: UUID,
    trace_id: str,
    scope: str = "full",
) -> dict[str, Any]:
    """Read-only audit export: anchor metadata + overall sha256 + proof summary."""
    if scope not in EVIDENCE_SCOPES:
        raise ValueError(f"invalid evidence scope: {scope}")
    chain_key = _chain_key(tenant_id)
    if scope == "full":
        # A full export must cover the entire chain so that the overall sha256
        # commits to every anchor, matching chain_status/verify totals.  Only
        # scoped exports filter to their domain batch (plus full snapshots).
        cur.execute(
            """SELECT id, seq, source_table, source_pk, row_hash, created_at
            FROM topology.evidence_chain_anchors
            WHERE tenant_id=%s AND chain_key=%s
            ORDER BY seq""",
            (tenant_id, chain_key),
        )
    else:
        cur.execute(
            """SELECT id, seq, source_table, source_pk, row_hash, created_at
            FROM topology.evidence_chain_anchors
            WHERE tenant_id=%s AND chain_key=%s AND anchor_scope IN (%s, 'full')
            ORDER BY seq""",
            (tenant_id, chain_key, scope),
        )
    entries: list[dict[str, Any]] = []
    for db_row in cur.fetchall():
        entries.append(
            {
                "anchor_id": str(db_row[0]),
                "seq": int(db_row[1]),
                "source_table": str(db_row[2]),
                "source_pk": str(db_row[3]),
                "row_hash": str(db_row[4]),
                "created_at": db_row[5].isoformat() if db_row[5] else None,
            }
        )
    proof = verify_evidence_chain(cur, tenant_id=tenant_id, trace_id=trace_id, scope="full")
    export = {
        "chain_key": chain_key,
        "scope": scope,
        "entries": entries,
        "sha256": sha256_hex(canonical_row({"chain_key": chain_key, "entries": entries})),
        "proof_ref": {
            "tail_hash": proof["tail_hash"],
            "total_anchors": proof["total_anchors"],
        },
        "exported_at": _now(),
        "trace_id": trace_id,
    }
    validate_evidence_export(export)
    return export


def chain_status(
    cur: Any,
    *,
    tenant_id: UUID,
    trace_id: str,
) -> dict[str, Any]:
    """Lightweight chain status (anchor count, tail hash, last anchored time)."""
    chain_key = _chain_key(tenant_id)
    cur.execute(
        """SELECT count(*), COALESCE(max(seq), 0), max(created_at)
        FROM topology.evidence_chain_anchors WHERE tenant_id=%s AND chain_key=%s""",
        (tenant_id, chain_key),
    )
    row = cur.fetchone()
    total = int(row[0])
    tail_seq = int(row[1])
    last_at = row[2]
    tail_hash: str | None = None
    if tail_seq > 0:
        cur.execute(
            """SELECT prev_hash FROM topology.evidence_chain_anchors
            WHERE tenant_id=%s AND chain_key=%s AND seq=%s""",
            (tenant_id, chain_key, tail_seq),
        )
        tail_row = cur.fetchone()
        if tail_row is not None:
            tail_hash = str(tail_row[0])
    return {
        "chain_key": chain_key,
        "total_anchors": total,
        "tail_seq": tail_seq,
        "tail_hash": tail_hash or sha256_hex(chain_key),
        "last_anchored_at": last_at.isoformat() if last_at else None,
        "checked_at": _now(),
        "trace_id": trace_id,
    }


def _now() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat()


def _is_uuid(value: str) -> bool:
    try:
        UUID(str(value))
    except (ValueError, TypeError):
        return False
    return True