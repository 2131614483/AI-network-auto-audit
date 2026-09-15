"""L2 light-solidification (design §5.5 exit 1).

Turn an *accepted* design-external relation suggestion into a durable
governance record: a ``knowledge.change_sets`` row of change_type
``experience`` whose single operation has target_kind ``experience.relation``,
published through the existing validate → approve → release → activate
pipeline.  The suggestion is then linked back to its ChangeSet/release so the
graph can render it as a *confirmed* relation.

This is governance-only by design: no ``graph.node`` is materialised (the
relation endpoints are plugins, not knowledge-graph nodes), and no plugin
manifest / executor is touched — that remains a later, separately authorised
stage (exit 2).  Solidification is idempotent: re-running it for an already
released suggestion returns the existing release without creating a duplicate.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

import psycopg2

from packages.knowledge.lifecycle import (
    RELATION_TARGET_KIND,
    ChangeOperation,
    KnowledgeLifecycleService,
)

logger = logging.getLogger(__name__)

ACCEPTED = "accepted"


class SolidificationError(ValueError):
    """Raised when a suggestion cannot be solidified from its current state."""


def _uuid(value: str | UUID) -> UUID:
    return value if isinstance(value, UUID) else UUID(str(value))


def solidify_accepted_suggestion(
    database_url: str,
    *,
    tenant_id: str | UUID,
    suggestion_id: str | UUID,
    tenant_slug: str = "local-dev",
) -> dict[str, Any]:
    """Publish an accepted suggestion as a confirmed relation (idempotent).

    Returns ``{suggestion_id, changeset_id, release_id, already_released}``.
    Raises :class:`SolidificationError` if the suggestion is missing or not in
    the ``accepted`` state.
    """
    tenant = _uuid(tenant_id)
    sid = _uuid(suggestion_id)

    with psycopg2.connect(database_url) as conn, conn.cursor() as cur:
        cur.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant),))
        cur.fetchone()
        cur.execute(
            """SELECT status,source_plugin_id,target_plugin_id,contract_id,
                      evidence_run_ids,decided_by,confidence,weight,changeset_id,release_id
               FROM experience.relation_suggestions
               WHERE tenant_id=%s AND suggestion_id=%s FOR UPDATE""",
            (str(tenant), sid),
        )
        row = cur.fetchone()
        if row is None:
            raise SolidificationError(f"suggestion not found: {sid}")
        (status, source, target, contract, run_ids, decided_by, confidence,
         weight, existing_changeset, existing_release) = row
        if status != ACCEPTED:
            raise SolidificationError(
                f"only accepted suggestions can be solidified, got: {status}"
            )
        if existing_release is not None:
            logger.info("suggestion %s already released as %s (idempotent)", sid, existing_release)
            return {
                "suggestion_id": str(sid),
                "changeset_id": str(existing_changeset),
                "release_id": str(existing_release),
                "already_released": True,
            }

    lifecycle = KnowledgeLifecycleService(database_url, tenant_slug)
    operation = ChangeOperation(
        RELATION_TARGET_KIND,
        "create",
        {
            "suggestion_id": str(sid),
            "source_plugin_id": source,
            "target_plugin_id": target,
            "contract_id": contract,
            "evidence_run_ids": run_ids if isinstance(run_ids, list) else [],
            "decided_by": decided_by or "desktop-local",
            "confidence": float(confidence or 0),
            "weight": float(weight or 0),
        },
        target_id=sid,
    )
    title = f"经验关系固化 {source} → {target} @{contract}"
    reason = f"L2 人工采纳的设计外协作关系，确认人 {decided_by or 'desktop-local'}"
    changeset_id = lifecycle.create_changeset(title, reason, [operation], risk_class="low")
    release_id = lifecycle.apply_changeset(changeset_id)

    with psycopg2.connect(database_url) as conn, conn.cursor() as cur:
        cur.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant),))
        cur.fetchone()
        cur.execute(
            """UPDATE experience.relation_suggestions
               SET changeset_id=%s,release_id=%s,released_at=now(),updated_at=now()
               WHERE tenant_id=%s AND suggestion_id=%s AND release_id IS NULL""",
            (changeset_id, release_id, str(tenant), sid),
        )
        if cur.rowcount != 1:
            raise SolidificationError(f"failed to link release back to suggestion: {sid}")
        conn.commit()
    logger.info("suggestion %s solidified as changeset %s / release %s", sid, changeset_id, release_id)
    return {
        "suggestion_id": str(sid),
        "changeset_id": str(changeset_id),
        "release_id": str(release_id),
        "already_released": False,
    }


__all__ = ["SolidificationError", "solidify_accepted_suggestion"]
