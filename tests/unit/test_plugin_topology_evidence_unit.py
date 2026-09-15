"""Plugin Topology M9 unit tests: deterministic evidence chain primitives.

Covers the pure functions in ``packages/plugin_topology/evidence.py``:
canonical row serialization is deterministic and column-order independent;
scope resolution is locked to the M9 enum; primary-key labels adapt to
tables with/without an ``id`` column; and ``verify_evidence_chain`` locates
the first mismatching link for both source-table tampering and broken
predecessor links (scripted fake cursor, no database).
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, NamedTuple
from uuid import UUID, uuid4

import pytest

from packages.plugin_topology.evidence import (
    EVIDENCE_SCOPES,
    _anchor_line_hash,
    _row_pk,
    _scoped_tables,
    canonical_row,
    chain_status,
    export_evidence,
    sha256_hex,
    verify_evidence_chain,
)

_TABLE = "topology.topology_releases"
_CHAIN_KEY = "evidence://" + str(uuid4()) + "/full"
_ROW_COLUMNS = ("id", "tenant_id", "release_key", "version", "status", "created_at")


class _Column(NamedTuple):
    """``column.name`` description entry as returned by the psycopg driver."""

    name: str


_ROW_DESCRIPTION = tuple(_Column(name) for name in _ROW_COLUMNS)


def _row(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": uuid4(),
        "tenant_id": uuid4(),
        "release_key": "rel-a",
        "version": "1.0.0",
        "status": "published",
        "created_at": datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc),
    }
    base.update(overrides)
    return base


# -- deterministic serialization -------------------------------------------------


def test_canonical_row_is_deterministic_and_order_independent() -> None:
    row_a = _row(release_key="rel-a", version="1.0.0")
    row_a_reversed: dict[str, Any] = dict(reversed(list(row_a.items())))
    assert canonical_row(row_a) == canonical_row(row_a_reversed)
    assert sha256_hex(canonical_row(row_a)) == sha256_hex(canonical_row(row_a_reversed))


def test_canonical_row_normalizes_uuid_datetime_decimal_and_nested_jsonb() -> None:
    nested = {"z": [1, 2], "a": {"m": 1, "k": 2}}
    row = _row(status="draft", nested=nested, amount=Decimal("1.50"), day=date(2026, 2, 3))
    first = canonical_row(row)
    # a logically identical row built in a different key order serializes equal
    second = canonical_row(
        {
            "day": date(2026, 2, 3),
            "amount": Decimal("1.5"),
            "nested": {"a": {"k": 2, "m": 1}, "z": [1, 2]},
            "status": "draft",
            **row,
        }
    )
    assert first == second


def test_canonical_row_changes_with_value() -> None:
    assert canonical_row(_row(release_key="rel-a")) != canonical_row(_row(release_key="rel-b"))


# -- scope and primary-key helpers -------------------------------------------------


def test_scoped_tables_full_unrolls_all_scopes() -> None:
    assert EVIDENCE_SCOPES == ("full", "topology", "chain", "execution", "verification", "remediation")
    full = set(_scoped_tables("full"))
    for scope in ("topology", "chain", "execution", "verification", "remediation"):
        assert full.issuperset(_scoped_tables(scope))
    assert len(_scoped_tables("verification")) == 1


def test_scoped_tables_rejects_invalid_scope() -> None:
    with pytest.raises(ValueError, match="invalid evidence scope"):
        _scoped_tables("everything")


def test_row_pk_uses_id_when_present_and_hash_label_otherwise() -> None:
    row_id = uuid4()
    assert _row_pk(_row(id=row_id), "canonical") == str(row_id)
    no_id = {"source_node": "n1", "target_node": "n2", "relation_type": "runs"}
    assert _row_pk(no_id, "canonical") == "row:" + sha256_hex("canonical")[:16]
    # deterministic for the same canonical payload
    assert _row_pk(no_id, "canonical") == _row_pk(no_id, "canonical")


def test_anchor_line_hash_is_stable() -> None:
    assert _anchor_line_hash(1, "topology.runs", "id-1", "h" * 64) == _anchor_line_hash(1, "topology.runs", "id-1", "h" * 64)
    assert _anchor_line_hash(1, "topology.runs", "id-1", "h" * 64) != _anchor_line_hash(2, "topology.runs", "id-1", "h" * 64)


# -- chain verification: tamper localization (scripted fake cursor) ---------------


class _FakeCur:
    """Cursor that replays a pre-built step script of (tag, description, rows)."""

    def __init__(self, steps: list[tuple[str, Any, list[Any]]]) -> None:
        self._steps = list(steps)
        self._index = 0
        self._rows: list[Any] = []
        self.description: Any = None

    def execute(self, sql: str, params: tuple = ()) -> None:
        assert self._index < len(self._steps), f"unexpected query: {sql}"
        tag, self.description, self._rows = self._steps[self._index]
        self._index += 1
        assert tag in sql, f"step {self._index} tag {tag!r} not in {sql!r}"
        assert params is not None  # tenant filtering is always parameterized

    def fetchone(self) -> Any:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[Any]:
        return list(self._rows)


def _anchor_tuples(live: list[dict[str, Any]], tenant_id: UUID) -> list[tuple]:
    """Build a self-consistent anchor set and the matching live-row tuples."""
    anchors: list[tuple] = []
    rows: list[tuple] = []
    prev = sha256_hex(f"evidence://{tenant_id}/full")
    for index, row in enumerate(live, start=1):
        pk = str(row["id"])
        row_hash = sha256_hex(canonical_row(row))
        line = _anchor_line_hash(index, _TABLE, pk, row_hash)
        link = sha256_hex(prev + line)
        anchors.append((uuid4(), index, _TABLE, pk, row_hash, link, "topology"))
        prev = link
        rows.append(tuple(row[column] for column in _ROW_COLUMNS))
    return anchors, rows


def _verify_steps(anchors: list[tuple], rows: list[tuple]) -> list[tuple[str, Any, list[Any]]]:
    # one info_schema probe per table (cached in the service), then one row
    # lookup per anchor, matching the exact query order of verify_evidence_chain.
    steps: list[tuple[str, Any, list[Any]]] = [
        ("FROM topology.evidence_chain_anchors", None, anchors),
        ("information_schema.columns", None, [(1,)]),
    ]
    for row in rows:
        steps.append(("WHERE tenant_id", _ROW_DESCRIPTION, [row]))
    return steps


def test_verify_reports_verified_when_chain_is_intact() -> None:
    tenant_id = uuid4()
    live = [_row(release_key=f"rel-{i}") for i in range(3)]
    anchors, rows = _anchor_tuples(live, tenant_id)
    cur = _FakeCur(_verify_steps(anchors, rows))
    proof = verify_evidence_chain(cur, tenant_id=tenant_id, trace_id=str(uuid4()), scope="topology")
    assert proof["verified"] is True
    assert proof["total_anchors"] == 3
    assert proof["first_mismatch"] is None
    assert proof["tail_hash"] == anchors[-1][5]


def test_verify_locates_first_mismatch_on_source_tamper() -> None:
    tenant_id = uuid4()
    live = [_row(release_key=f"rel-{i}") for i in range(3)]
    anchors, rows = _anchor_tuples(live, tenant_id)
    # tamper the second live row (source-table modification, row hash no longer matches)
    tampered = [rows[0]] + [tuple(_row(release_key="rel-1-evil").get(c) for c in _ROW_COLUMNS)] + [rows[2]]
    cur = _FakeCur(_verify_steps(anchors, tampered))
    proof = verify_evidence_chain(cur, tenant_id=tenant_id, trace_id=str(uuid4()), scope="topology")
    assert proof["verified"] is False
    assert proof["first_mismatch"]["seq"] == 2
    assert proof["first_mismatch"]["source_table"] == _TABLE
    assert proof["first_mismatch"]["source_pk"] == str(live[1]["id"])


def test_verify_locates_first_mismatch_on_broken_link() -> None:
    tenant_id = uuid4()
    live = [_row(release_key=f"rel-{i}") for i in range(3)]
    anchors, rows = _anchor_tuples(live, tenant_id)
    # break the predecessor pointer of the second anchor (anchor-table tamper)
    broken = [anchors[0], tuple(list(anchors[1][:5]) + ["0" * 64, anchors[1][-1]])] + anchors[2:]
    cur = _FakeCur(_verify_steps(broken, rows))
    proof = verify_evidence_chain(cur, tenant_id=tenant_id, trace_id=str(uuid4()), scope="topology")
    assert proof["verified"] is False
    assert proof["first_mismatch"]["seq"] == 2
    assert proof["first_mismatch"]["source_pk"] == str(live[1]["id"])


def test_verify_empty_chain_is_vacuous_true() -> None:
    tenant_id = uuid4()
    cur = _FakeCur([("FROM topology.evidence_chain_anchors", None, [])])
    proof = verify_evidence_chain(cur, tenant_id=tenant_id, trace_id=str(uuid4()), scope="full")
    assert proof["verified"] is True
    assert proof["total_anchors"] == 0
    assert proof["tail_hash"] == sha256_hex(f"evidence://{tenant_id}/full")


def _chain_status_steps(anchors: list[tuple]) -> list[tuple[str, Any, list[Any]]]:
    steps: list[tuple[str, Any, list[Any]]] = [
        (
            "FROM topology.evidence_chain_anchors WHERE",
            None,
            [(len(anchors), len(anchors) if anchors else 0, datetime(2026, 1, 1, tzinfo=timezone.utc))],
        ),
    ]
    if anchors:
        steps.append(("AND seq=", None, [(anchors[-1][5],)]))
    return steps


def test_chain_status_reports_count_and_tail() -> None:
    tenant_id = uuid4()
    live = [_row(release_key=f"rel-{i}") for i in range(2)]
    anchors, _rows = _anchor_tuples(live, tenant_id)
    cur = _FakeCur(_chain_status_steps(anchors))
    status = chain_status(cur, tenant_id=tenant_id, trace_id=str(uuid4()))
    assert status["total_anchors"] == 2
    assert status["tail_seq"] == 2
    assert status["tail_hash"] == anchors[-1][5]


def test_export_is_read_only_projection() -> None:
    tenant_id = uuid4()
    live = [_row(release_key=f"rel-{i}") for i in range(2)]
    anchors, rows = _anchor_tuples(live, tenant_id)
    entry_rows = [tuple([a[0], a[1], a[2], a[3], a[4], datetime(2026, 1, 1, tzinfo=timezone.utc)]) for a in anchors]
    steps: list[tuple[str, Any, list[Any]]] = [
        ("AND anchor_scope IN", None, entry_rows),
        ("FROM topology.evidence_chain_anchors", None, anchors),
        ("information_schema.columns", None, [(1,)]),
    ]
    for row in rows:
        steps.append(("WHERE tenant_id", _ROW_DESCRIPTION, [row]))
    cur = _FakeCur(steps)
    export = export_evidence(cur, tenant_id=tenant_id, trace_id=str(uuid4()), scope="topology")
    assert export["scope"] == "topology"
    assert len(export["entries"]) == 2
    assert len(export["sha256"]) == 64
    assert export["proof_ref"]["total_anchors"] == 2
    assert all(entry["row_hash"] for entry in export["entries"])