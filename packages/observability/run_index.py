"""Read-only run index: evidence bundles on disk joined to their run records.

An evidence bundle is the offline-verifiable record of one run: a zip holding
plan/attempts/edges/artifacts/logs plus a ``manifest.json`` of one raw sha256 per
member.  Bundles on disk and run rows in the database are *two different facts* —
a bundle can outlive its run row, and a run can exist with no bundle at all — so
they are reported side by side rather than merged into one optimistic "this run
has evidence" claim.

Two deliberate choices:

* **Listing never re-hashes.**  ``verify_bundle`` is a separate, single-bundle
  action.  Verification *is* the claim being made, so it is allowed to cost what
  it costs, and it is never quietly approximated by "the file exists".
* **An unlinked run says so.**  ``experience.archive_links`` is the project
  anchor; when a run has no link the field is ``None`` and ``archive_state`` is
  ``unlinked``.  Blank would read as "no project", which is a different — and
  false — statement.

Nothing in this module writes.
"""

from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

import psycopg2
from psycopg2.extras import register_uuid

from packages.observability.evidence import _MANIFEST_NAME, verify_evidence_bundle

register_uuid()  # type: ignore[no-untyped-call]

#: Project-relative directories searched for evidence bundles.  Both are real
#: locations in this repository; a missing one is reported, never invented.
DEFAULT_BUNDLE_ROOTS: tuple[str, ...] = (".data/evidence", ".data/demo/evidence")


@dataclass(frozen=True, slots=True)
class BundleRef:
    """One zip on disk, plus the manifest fields readable without re-hashing."""

    run_id: str
    path: str
    size_bytes: int
    modified_at: str
    plan_key: str | None
    trace_id: str | None
    exported_at: str | None
    members: int
    readable: bool


def _bundle_roots(project_root: Path, roots: tuple[str, ...]) -> list[Path]:
    return [project_root / entry for entry in roots]


def _safe_bundle_path(path: Path, roots: list[Path]) -> bool:
    """True when ``path`` resolves inside one of the declared roots.

    Bundle names are UUIDs, so the guard is mostly belt-and-braces; it exists
    because the path is assembled from data, and a path assembled from data is
    exactly the shape that produced the earlier traversal defect.
    """

    try:
        resolved = path.resolve()
    except OSError:  # pragma: no cover - unreadable path
        return False
    for root in roots:
        try:
            resolved.relative_to(root.resolve())
        except (OSError, ValueError):
            continue
        return True
    return False


def list_bundles(
    project_root: Path,
    *,
    roots: tuple[str, ...] = DEFAULT_BUNDLE_ROOTS,
) -> tuple[list[BundleRef], list[str]]:
    """Every readable ``*.zip`` under the declared roots.

    Returns ``(bundles, missing_roots)``.  A declared root that does not exist is
    surfaced instead of being silently skipped: "no bundles" and "wrong place"
    are different answers and the interface must not confuse them.
    """

    bundles: list[BundleRef] = []
    missing: list[str] = []
    for root in _bundle_roots(project_root, roots):
        if not root.is_dir():
            missing.append(str(root.relative_to(project_root)) if root.is_relative_to(project_root) else str(root))
            continue
        for path in sorted(root.glob("*.zip")):
            if not _safe_bundle_path(path, _bundle_roots(project_root, roots)):
                continue
            stat = path.stat()
            plan_key: str | None = None
            trace_id: str | None = None
            exported_at: str | None = None
            members = 0
            readable = True
            try:
                with zipfile.ZipFile(path) as archive:
                    names = archive.namelist()
                    members = len(names)
                    if _MANIFEST_NAME in names:
                        manifest = json.loads(archive.read(_MANIFEST_NAME).decode("utf-8"))
                        plan_key = manifest.get("plan_key")
                        trace_id = manifest.get("trace_id")
                        exported_at = manifest.get("exported_at")
                    else:
                        readable = False
            except Exception:  # pragma: no cover - corrupt zip is a real state, not a crash
                readable = False
            bundles.append(
                BundleRef(
                    run_id=path.stem,
                    path=path.relative_to(project_root).as_posix()
                    if path.is_relative_to(project_root)
                    else str(path),
                    size_bytes=stat.st_size,
                    modified_at=_isoformat(stat.st_mtime),
                    plan_key=plan_key,
                    trace_id=trace_id,
                    exported_at=exported_at,
                    members=members,
                    readable=readable,
                )
            )
    return bundles, missing


def _isoformat(timestamp: float) -> str:
    from datetime import datetime, timezone  # noqa: PLC0415

    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()


def _uuid_ids(run_ids: list[str]) -> list[str]:
    """Only well-formed UUIDs, because the columns they are matched against are uuid.

    A bundle whose file name is not a UUID (an old hand-made archive, say) is
    still listed as a bundle — it just cannot join to a run row, which is the
    honest answer rather than a query error.
    """

    valid: list[str] = []
    for run_id in run_ids:
        try:
            valid.append(str(UUID(run_id)))
        except (ValueError, AttributeError, TypeError):
            continue
    return valid


def _attempt_rows(rows: list[tuple[Any, ...]]) -> dict[str, dict[str, Any]]:
    parsed: dict[str, dict[str, Any]] = {}
    for (
        run_id,
        attempts,
        succeeded,
        failed,
        plugins,
        versioned,
        started,
        finished,
        plan_key,
        execution_hash,
        trace_id,
    ) in rows:
        parsed[str(run_id)] = {
            "attempts": attempts,
            "succeeded": succeeded,
            "failed": failed,
            "plugins": plugins,
            "versioned_attempts": versioned,
            "started_at": started.isoformat() if started is not None else None,
            "finished_at": finished.isoformat() if finished is not None else None,
            "plan_key": plan_key,
            "execution_hash": execution_hash,
            "trace_id": str(trace_id) if trace_id is not None else None,
        }
    return parsed


def _recent_run_rows(database_url: str, tenant_slug: str, limit: int) -> dict[str, dict[str, Any]]:
    """Attempt aggregates for the most recent runs, regardless of bundles.

    The bundle list cannot be the only seed: a run whose bundle was never
    exported (or has since been archived away) would otherwise be invisible,
    which is exactly the "two sources quietly aligned" lie this view exists to
    prevent.
    """

    with psycopg2.connect(database_url) as connection, connection.cursor() as cur:
        cur.execute("SELECT id FROM iam.tenants WHERE slug=%s", (tenant_slug,))
        row = cur.fetchone()
        if row is None:
            raise ValueError(f"tenant not found: {tenant_slug}")
        tenant_id = row[0]
        cur.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),))
        cur.execute(
            """SELECT run_id,count(*),
            count(*) FILTER (WHERE status='succeeded'),
            count(*) FILTER (WHERE status<>'succeeded'),
            count(DISTINCT plugin_id),
            count(*) FILTER (WHERE plugin_version IS NOT NULL AND plugin_version<>''),
            min(created_at),max(finished_at),min(plan_key),min(execution_hash),
            min(trace_id)
            FROM control.node_attempts
            WHERE tenant_id=%s
            GROUP BY run_id
            ORDER BY max(created_at) DESC NULLS LAST
            LIMIT %s""",
            (tenant_id, limit),
        )
        return _attempt_rows(cur.fetchall())


def _run_rows(database_url: str, tenant_slug: str, run_ids: list[str]) -> dict[str, dict[str, Any]]:
    """Attempt aggregates per run id, restricted to the requested ids.

    One query for the whole page: an evidence directory of 79 bundles must not
    become 79 round trips.
    """

    ids = _uuid_ids(run_ids)
    if not ids:
        return {}
    with psycopg2.connect(database_url) as connection, connection.cursor() as cur:
        cur.execute("SELECT id FROM iam.tenants WHERE slug=%s", (tenant_slug,))
        row = cur.fetchone()
        if row is None:
            raise ValueError(f"tenant not found: {tenant_slug}")
        tenant_id = row[0]
        cur.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),))
        cur.execute(
            """SELECT run_id,count(*),
            count(*) FILTER (WHERE status='succeeded'),
            count(*) FILTER (WHERE status<>'succeeded'),
            count(DISTINCT plugin_id),
            count(*) FILTER (WHERE plugin_version IS NOT NULL AND plugin_version<>''),
            min(created_at),max(finished_at),min(plan_key),min(execution_hash),
            min(trace_id)
            FROM control.node_attempts
            WHERE tenant_id=%s AND run_id = ANY(%s::uuid[])
            GROUP BY run_id""",
            (tenant_id, ids),
        )
        return _attempt_rows(cur.fetchall())


def _archive_rows(database_url: str, tenant_slug: str, run_ids: list[str]) -> dict[str, dict[str, Any]]:
    ids = _uuid_ids(run_ids)
    if not ids:
        return {}
    with psycopg2.connect(database_url) as connection, connection.cursor() as cur:
        cur.execute("SELECT id FROM iam.tenants WHERE slug=%s", (tenant_slug,))
        row = cur.fetchone()
        if row is None:
            raise ValueError(f"tenant not found: {tenant_slug}")
        tenant_id = row[0]
        cur.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),))
        cur.execute(
            """SELECT run_id,project_id,note,archived_at,trace_id
            FROM experience.archive_links
            WHERE tenant_id=%s AND run_id = ANY(%s::uuid[])""",
            (tenant_id, ids),
        )
        rows: dict[str, dict[str, Any]] = {}
        for run_id, project_id, note, archived_at, trace_id in cur.fetchall():
            rows[str(run_id)] = {
                "project_id": str(project_id) if project_id is not None else None,
                "note": note,
                "archived_at": archived_at.isoformat() if archived_at is not None else None,
                "trace_id": str(trace_id) if trace_id is not None else None,
            }
        return rows


def run_index(
    database_url: str,
    tenant_slug: str,
    project_root: Path,
    *,
    roots: tuple[str, ...] = DEFAULT_BUNDLE_ROOTS,
    limit: int = 200,
) -> dict[str, Any]:
    """Bundles joined to run aggregates, plus the states that have no bundle.

    The result carries both directions of the join on purpose:

    * ``items``      — one row per run, ``bundle`` present or absent;
    * ``orphan_bundles`` — bundles whose run row is gone from this tenant.

    ``orphan_bundles`` is the interesting one: it is what "the archive outlives
    the database" looks like, and hiding it would make the two sources look
    perfectly aligned when they are not.
    """

    if not 1 <= limit <= 1000:
        raise ValueError("limit must be between 1 and 1000")

    bundles, missing_roots = list_bundles(project_root, roots=roots)
    by_run = {bundle.run_id: bundle for bundle in bundles}
    run_rows = {**_run_rows(database_url, tenant_slug, sorted(by_run)), **_recent_run_rows(database_url, tenant_slug, limit)}
    archive_rows = _archive_rows(database_url, tenant_slug, sorted(by_run))

    items: list[dict[str, Any]] = []
    for run_id in sorted(set(run_rows) | set(by_run)):
        bundle = by_run.get(run_id)
        run = run_rows.get(run_id, {})
        archive = archive_rows.get(run_id)
        items.append(
            {
                "run_id": run_id,
                "plan_key": run.get("plan_key") or (bundle.plan_key if bundle else None),
                "trace_id": run.get("trace_id") or (bundle.trace_id if bundle else None),
                "execution_hash": run.get("execution_hash"),
                "attempts": run.get("attempts", 0),
                "succeeded": run.get("succeeded", 0),
                "failed": run.get("failed", 0),
                "plugins": run.get("plugins", 0),
                "versioned_attempts": run.get("versioned_attempts", 0),
                "started_at": run.get("started_at"),
                "finished_at": run.get("finished_at"),
                "bundle": None
                if bundle is None
                else {
                    "path": bundle.path,
                    "size_bytes": bundle.size_bytes,
                    "modified_at": bundle.modified_at,
                    "exported_at": bundle.exported_at,
                    "members": bundle.members,
                    "readable": bundle.readable,
                    "verified": None,  # never claimed until verify_bundle() runs
                },
                "project_id": archive["project_id"] if archive else None,
                "archive_state": "linked" if archive else "unlinked",
                "archived_at": archive["archived_at"] if archive else None,
            }
        )
        if len(items) >= limit:
            break

    orphan_bundles = sorted(set(by_run) - set(run_rows))
    return {
        "items": items,
        "orphan_bundles": orphan_bundles,
        "summary": {
            "runs_listed": len(items),
            "bundles_on_disk": len(bundles),
            "bundles_unreadable": sum(1 for bundle in bundles if not bundle.readable),
            "runs_with_bundle": sum(1 for item in items if item["bundle"] is not None),
            "runs_without_bundle": sum(1 for item in items if item["bundle"] is None),
            "orphan_bundles": len(orphan_bundles),
            "linked_to_project": sum(1 for item in items if item["archive_state"] == "linked"),
            "unlinked": sum(1 for item in items if item["archive_state"] == "unlinked"),
            "missing_roots": missing_roots,
        },
    }


def verify_bundle(
    run_id: str,
    project_root: Path,
    *,
    roots: tuple[str, ...] = DEFAULT_BUNDLE_ROOTS,
) -> dict[str, Any]:
    """Recompute every manifest sha256 for one bundle.

    ``run_id`` names a bundle on disk and is treated as untrusted input: it must
    parse as a UUID and the resolved path must stay inside a declared root.  On
    failure the answer is an explicit not-ok record, never an exception the caller
    might turn into "probably fine".
    """

    try:
        parsed = UUID(run_id)
    except (ValueError, AttributeError, TypeError):
        return {"run_id": run_id, "found": False, "reason": "run_id is not a uuid"}
    declared = _bundle_roots(project_root, roots)
    for root in declared:
        candidate = root / f"{parsed}.zip"
        if not _safe_bundle_path(candidate, declared):
            continue
        if not candidate.is_file():
            continue
        verification = verify_evidence_bundle(candidate)
        return {
            "run_id": str(parsed),
            "found": True,
            "path": candidate.relative_to(project_root).as_posix(),
            "size_bytes": candidate.stat().st_size,
            "verification": verification.as_dict(),
        }
    return {"run_id": str(parsed), "found": False, "reason": "no bundle in the declared roots"}
