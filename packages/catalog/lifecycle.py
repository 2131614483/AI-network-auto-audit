"""Read-only plugin lifecycle view: declared lifecycle vs executable allow list.

Whether a plugin can actually run inside a flow is decided by two facts that are
supposed to agree:

* the protocol's declared ``lifecycle`` (``verified`` / ``contract_only``), and
* membership of the code-level allow list :data:`VERIFIED_PLUGIN_IDS`.

When they disagree the plugin sits in a third state — declared non-executable yet
carried in the allow list — and that state has to stay visible instead of being
averaged away.  ``verified`` is a *declaration*; the allow list is what the
runtime will actually resolve an entrypoint for; the version row is what was
published.  All three are reported side by side, and nothing here writes.

Names are deliberately explicit: ``protocol_lifecycle`` is what the file says,
``executable`` is what the code permits, ``conflict`` is the disagreement.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import psycopg2
from psycopg2.extras import register_uuid

from packages.ai_planner.composer import discover_plugins
from packages.plugin_runtime.layout import VERIFIED_PLUGIN_IDS

register_uuid()  # type: ignore[no-untyped-call]

VERIFIED = "verified"


def _version_rows(database_url: str, tenant_slug: str) -> dict[str, dict[str, Any]]:
    """Latest published version row per plugin, keyed by the dotted plugin id.

    ``catalog.plugin_versions.plugin_id`` is a UUID foreign key; the dotted
    identifier a caller knows lives on ``catalog.plugins.key``, so the two tables
    are joined here rather than guessed at from the UUID.  History is never
    collapsed in the table itself; this view reports only the newest row because
    that is the one a new run would resolve.
    """

    with psycopg2.connect(database_url) as connection, connection.cursor() as cur:
        cur.execute("SELECT id FROM iam.tenants WHERE slug=%s", (tenant_slug,))
        row = cur.fetchone()
        if row is None:
            raise ValueError(f"tenant not found: {tenant_slug}")
        tenant_id = row[0]
        cur.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),))
        cur.execute(
            """SELECT plugin.key,version.version,version.status,version.descriptor_sha256,
            version.runtime,version.entrypoint,version.side_effect_class,version.published_at
            FROM catalog.plugin_versions version
            JOIN catalog.plugins plugin ON plugin.id=version.plugin_id
            WHERE version.tenant_id=%s
            ORDER BY version.published_at DESC NULLS LAST""",
            (tenant_id,),
        )
        latest: dict[str, dict[str, Any]] = {}
        for key, version, status, descriptor, runtime, entrypoint, side_effect, published in cur.fetchall():
            plugin_id = str(key)
            if plugin_id in latest:
                continue
            latest[plugin_id] = {
                "version": version,
                "version_status": status,
                "descriptor_sha256": descriptor,
                "runtime": runtime,
                "entrypoint": entrypoint,
                "side_effect_class": side_effect,
                "published_at": published.isoformat() if published is not None else None,
            }
        return latest


def _row(plugin_id: str, spec: Any, version: dict[str, Any], verified: set[str]) -> dict[str, Any]:
    executable = plugin_id in verified
    declared = spec.lifecycle
    return {
        "plugin_id": plugin_id,
        "name": spec.name,
        "capability": spec.capability,
        "domain": spec.domains[0] if spec.domains else None,
        "domains": list(spec.domains),
        "protocol_lifecycle": declared,
        "executable": executable,
        "conflict": (declared == VERIFIED) != executable,
        "version": version.get("version"),
        "version_status": version.get("version_status"),
        "descriptor_sha256": version.get("descriptor_sha256"),
        "runtime": version.get("runtime"),
        "entrypoint": version.get("entrypoint"),
        "side_effect_class": version.get("side_effect_class"),
        "published_at": version.get("published_at"),
    }


def plugin_lifecycle(
    database_url: str,
    *,
    tenant_slug: str = "local-dev",
    root: Path | None = None,
    domain: str | None = None,
    lifecycle: str | None = None,
    conflict_only: bool = False,
) -> dict[str, Any]:
    """Declared lifecycle, executable status and published version for each plugin.

    ``summary`` always describes the **whole corpus**; ``items`` is the filtered
    view.  A filtered page must never make the corpus look smaller than it is,
    which is exactly the confusion this endpoint exists to remove.

    ``domain`` is filtered here rather than only through discovery: passing
    ``globs`` and ``domain`` together makes discovery ignore the domain pack, and
    a filter that silently does not filter is worse than no filter at all.
    """

    specs = discover_plugins(root, globs=("*",))
    verified = set(VERIFIED_PLUGIN_IDS)
    versions = _version_rows(database_url, tenant_slug)

    rows = [_row(plugin_id, spec, versions.get(plugin_id, {}), verified) for plugin_id, spec in sorted(specs.items())]

    by_domain: dict[str, int] = {}
    for item in rows:
        key = str(item["domain"] or "unknown")
        by_domain[key] = by_domain.get(key, 0) + 1

    summary = {
        "plugins": len(rows),
        "protocol_verified": sum(1 for item in rows if item["protocol_lifecycle"] == VERIFIED),
        "protocol_contract_only": sum(1 for item in rows if item["protocol_lifecycle"] != VERIFIED),
        "executable": sum(1 for item in rows if item["executable"]),
        "conflict": sum(1 for item in rows if item["conflict"]),
        "version_registered": sum(1 for item in rows if item["version"]),
        "descriptor_sha256_present": sum(1 for item in rows if item["descriptor_sha256"]),
        "allow_list_size": len(verified),
        "allow_list_unmatched": sorted(verified - set(specs)),
        "by_domain": by_domain,
    }

    items = rows
    if domain is not None:
        items = [item for item in items if domain in item["domains"]]
    if lifecycle is not None:
        items = [item for item in items if item["protocol_lifecycle"] == lifecycle]
    if conflict_only:
        items = [item for item in items if item["conflict"]]

    return {"summary": summary, "items": items}


def plugin_version_history(
    database_url: str,
    *,
    tenant_slug: str = "local-dev",
    plugin_id: str,
) -> list[dict[str, Any]]:
    """Every published version row for one dotted plugin id, newest first.

    Unlike :func:`plugin_lifecycle` this does **not** collapse to the latest
    row: the lineage view exists precisely to show that a plugin's descriptors
    accumulated over time.  The dotted id is resolved to ``catalog.plugins.key``
    so the caller does not have to know the UUID.  Nothing here writes.
    """

    with psycopg2.connect(database_url) as connection, connection.cursor() as cur:
        cur.execute("SELECT id FROM iam.tenants WHERE slug=%s", (tenant_slug,))
        row = cur.fetchone()
        if row is None:
            raise ValueError(f"tenant not found: {tenant_slug}")
        tenant_id = row[0]
        cur.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),))
        cur.execute(
            """SELECT version.version,version.status,version.descriptor_sha256,
            version.runtime,version.entrypoint,version.side_effect_class,version.published_at
            FROM catalog.plugin_versions version
            JOIN catalog.plugins plugin ON plugin.id=version.plugin_id
            WHERE version.tenant_id=%s AND plugin.key=%s
            ORDER BY version.published_at DESC NULLS LAST, version.id DESC""",
            (tenant_id, plugin_id),
        )
        rows = cur.fetchall()
    return [
        {
            "version": version,
            "version_status": status,
            "descriptor_sha256": descriptor,
            "runtime": runtime,
            "entrypoint": entrypoint,
            "side_effect_class": side_effect,
            "published_at": published.isoformat() if published is not None else None,
        }
        for version, status, descriptor, runtime, entrypoint, side_effect, published in rows
    ]
