"""Durable experience projector: run/attempt ledger → observations → stats.

Write path (design §3.3 / §5):
* observations are append-only and idempotent (ON CONFLICT DO NOTHING);
* after a run is projected, every touched edge/node stat is **recomputed from
  its full observation set** (not delta-incremented), so re-projecting the same
  run never double counts and incremental projection equals ``rebuild``;
* no plugin is executed and no network is touched — this only reads the
  attempt ledger and writes the ``experience`` schema in short transactions.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

import psycopg2
from psycopg2.extras import Json

from packages.experience.handoff import extract_handovers, extract_node_uses
from packages.experience.rollup import edge_stat_values, node_stat_values
from packages.experience.statistics import age_days, half_life_decay
from packages.experience.suggestion import (
    SuggestionNotFound,
    accumulates_evidence,
    transition_status,
)
from packages.experience.types import Handover, NodeUse
from packages.plugin_topology.dag_persistence import AttemptStore

logger = logging.getLogger("experience.projector")


def _uuid(value: str | UUID) -> UUID:
    return value if isinstance(value, UUID) else UUID(str(value))


class ExperienceProjector:
    """Project finished runs into tenant-scoped experience evidence."""

    def __init__(self, database_url: str, *, worker_id: str | None = None) -> None:
        self.database_url = database_url
        self.worker_id = worker_id or f"experience-{uuid4().hex[:8]}"

    # -- design-time edge set -------------------------------------------------

    @staticmethod
    def declared_dataflow_edges(lifecycle: str | None = None) -> set[tuple[str, str, str]]:
        """Design-time (source, target, contract) dataflow edges from manifests."""
        from packages.ai_planner.nebula_graph import build_nebula_graph

        graph = build_nebula_graph(lifecycle=lifecycle)
        return {
            (str(e["source"]), str(e["target"]), str(e["contract"]))
            for e in graph["edges"]
            if e["type"] == "dataflow"
        }

    # -- projection -----------------------------------------------------------

    def project_run(
        self,
        *,
        tenant_id: UUID,
        run_id: str | UUID,
        trace_id: str,
        declared_edges: set[tuple[str, str, str]] | None = None,
    ) -> dict[str, Any]:
        """Project one finished run; idempotent on (tenant, run)."""
        tenant = _uuid(tenant_id)
        run_uuid = _uuid(run_id)
        attempts = AttemptStore(self.database_url).query_attempts(
            tenant_id=tenant, run_id=str(run_uuid)
        )
        handovers = extract_handovers(attempts)
        node_uses = extract_node_uses(attempts)
        if declared_edges is None:
            declared_edges = self.declared_dataflow_edges()

        inserted_edges = inserted_nodes = 0
        touched_edges: set[tuple[str, str, str]] = set()
        touched_nodes: set[tuple[str, str]] = set()

        with psycopg2.connect(self.database_url) as conn, conn.cursor() as cur:
            cur.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant),))
            cur.fetchone()

            for handover in handovers:
                key = handover.edge_key()
                declared = key in declared_edges
                cur.execute(
                    """
                    INSERT INTO experience.edge_observations
                      (tenant_id,run_id,trace_id,source_plugin_id,target_plugin_id,contract_id,
                       source_attempt_id,target_attempt_id,source_instance,target_instance,
                       source_port,artifact_sha256,declared,status,latency_ms)
                    VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (tenant_id,run_id,source_instance,target_instance,contract_id)
                    DO NOTHING
                    """,
                    (
                        str(tenant), run_uuid, trace_id,
                        handover.source_plugin_id, handover.target_plugin_id, handover.contract_id,
                        _uuid(handover.source_attempt_id), _uuid(handover.target_attempt_id),
                        handover.source_instance, handover.target_instance,
                        handover.source_port, handover.artifact_sha256, declared,
                        handover.status, handover.latency_ms,
                    ),
                )
                inserted_edges += cur.rowcount
                touched_edges.add(key)

            for use in node_uses:
                cur.execute(
                    """
                    INSERT INTO experience.node_observations
                      (tenant_id,run_id,trace_id,plugin_id,capability,plugin_version,attempt_id,
                       attempt_seq,status,error_kind,latency_ms)
                    VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (tenant_id,attempt_id) DO NOTHING
                    """,
                    (
                        str(tenant), run_uuid, trace_id, use.plugin_id, use.capability,
                        use.plugin_version,
                        _uuid(use.attempt_id), use.attempt_seq, use.status,
                        use.error_kind, use.latency_ms,
                    ),
                )
                inserted_nodes += cur.rowcount
                touched_nodes.add((use.plugin_id, use.capability))

            for key in sorted(touched_edges):
                self._recompute_edge(cur, tenant, key)
            for plugin_id, capability in sorted(touched_nodes):
                self._recompute_node(cur, tenant, plugin_id, capability)

            # L1: design-external hand-offs become proposed suggestions;
            # terminal (accepted/dismissed) rows are frozen and never revived.
            suggestions_upserted = 0
            for key in sorted(touched_edges):
                suggestions_upserted += self._upsert_suggestion(cur, tenant, key)
            conn.commit()

        summary = {
            "run_id": str(run_uuid),
            "handovers": len(handovers),
            "node_uses": len(node_uses),
            "edge_observations_inserted": inserted_edges,
            "node_observations_inserted": inserted_nodes,
            "edges_recomputed": len(touched_edges),
            "nodes_recomputed": len(touched_nodes),
            "suggestions_upserted": suggestions_upserted,
        }
        logger.info("experience project_run %s: %s", run_uuid, summary)
        return summary

    # -- recompute (shared by projection and rebuild) ------------------------

    def _recompute_edge(
        self, cur: Any, tenant: UUID, key: tuple[str, str, str]
    ) -> None:
        source, target, contract = key
        cur.execute(
            """
            SELECT status,latency_ms,observed_at,run_id,declared FROM experience.edge_observations
            WHERE tenant_id=%s AND source_plugin_id=%s AND target_plugin_id=%s AND contract_id=%s
            ORDER BY observed_at DESC
            """,
            (str(tenant), source, target, contract),
        )
        rows = [
            {"status": r[0], "latency_ms": r[1], "observed_at": r[2], "run_id": r[3], "declared": r[4]}
            for r in cur.fetchall()
        ]
        values = edge_stat_values(rows)
        cur.execute(
            """
            INSERT INTO experience.edge_stats
              (tenant_id,source_plugin_id,target_plugin_id,contract_id,success_count,fail_count,
               total_latency_ms,first_used_at,last_used_at,declared,confidence,weight,evidence_run_ids)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (tenant_id,source_plugin_id,target_plugin_id,contract_id) DO UPDATE SET
              success_count=EXCLUDED.success_count, fail_count=EXCLUDED.fail_count,
              total_latency_ms=EXCLUDED.total_latency_ms, first_used_at=EXCLUDED.first_used_at,
              last_used_at=EXCLUDED.last_used_at, declared=EXCLUDED.declared,
              confidence=EXCLUDED.confidence, weight=EXCLUDED.weight,
              evidence_run_ids=EXCLUDED.evidence_run_ids
            """,
            (
                str(tenant), source, target, contract,
                values["success_count"], values["fail_count"], values["total_latency_ms"],
                values["first_used_at"], values["last_used_at"], values["declared"],
                values["confidence"], values["weight"], Json(values["evidence_run_ids"]),
            ),
        )

    def _recompute_node(
        self, cur: Any, tenant: UUID, plugin_id: str, capability: str
    ) -> None:
        cur.execute(
            """
            SELECT status,latency_ms,observed_at,error_kind FROM experience.node_observations
            WHERE tenant_id=%s AND plugin_id=%s AND capability=%s
            ORDER BY observed_at DESC
            """,
            (str(tenant), plugin_id, capability),
        )
        rows = [
            {"status": r[0], "latency_ms": r[1], "observed_at": r[2], "error_kind": r[3]}
            for r in cur.fetchall()
        ]
        values = node_stat_values(rows)
        cur.execute(
            """
            INSERT INTO experience.node_stats
              (tenant_id,plugin_id,capability,use_count,success_count,fail_count,total_latency_ms,
               error_kind_counts,first_used_at,last_used_at,confidence,weight)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (tenant_id,plugin_id,capability) DO UPDATE SET
              use_count=EXCLUDED.use_count, success_count=EXCLUDED.success_count,
              fail_count=EXCLUDED.fail_count, total_latency_ms=EXCLUDED.total_latency_ms,
              error_kind_counts=EXCLUDED.error_kind_counts, first_used_at=EXCLUDED.first_used_at,
              last_used_at=EXCLUDED.last_used_at, confidence=EXCLUDED.confidence,
              weight=EXCLUDED.weight
            """,
            (
                str(tenant), plugin_id, capability,
                values["use_count"], values["success_count"], values["fail_count"],
                values["total_latency_ms"], Json(values["error_kind_counts"]),
                values["first_used_at"], values["last_used_at"],
                values["confidence"], values["weight"],
            ),
        )

    # -- L1 suggestions / L2 decision ----------------------------------------

    def _upsert_suggestion(self, cur: Any, tenant: UUID, key: tuple[str, str, str]) -> int:
        """Propose/refresh a design-external edge suggestion. Returns 1 if written."""
        source, target, contract = key
        cur.execute(
            """SELECT success_count,fail_count,confidence,weight,last_used_at,declared,evidence_run_ids
               FROM experience.edge_stats
               WHERE tenant_id=%s AND source_plugin_id=%s AND target_plugin_id=%s AND contract_id=%s""",
            (str(tenant), source, target, contract),
        )
        stat = cur.fetchone()
        if stat is None or stat[5] is True:
            return 0  # declared in design manifests → no suggestion needed
        success, fail, confidence, weight, last_used, _declared, run_ids = stat
        evidence = success + fail

        cur.execute(
            """SELECT suggestion_id,status FROM experience.relation_suggestions
               WHERE tenant_id=%s AND source_plugin_id=%s AND target_plugin_id=%s AND contract_id=%s""",
            (str(tenant), source, target, contract),
        )
        existing = cur.fetchone()
        if existing is not None:
            if not accumulates_evidence(existing[1]):
                return 0  # accepted/dismissed is frozen
            cur.execute(
                """UPDATE experience.relation_suggestions SET
                     evidence_count=%s,success_count=%s,fail_count=%s,confidence=%s,weight=%s,
                     last_evidence_at=%s,evidence_run_ids=%s,updated_at=now()
                   WHERE suggestion_id=%s""",
                (evidence, success, fail, confidence, weight, last_used,
                 Json(run_ids if isinstance(run_ids, list) else []), existing[0]),
            )
            return 1
        cur.execute(
            """INSERT INTO experience.relation_suggestions
               (tenant_id,source_plugin_id,target_plugin_id,contract_id,status,
                evidence_count,success_count,fail_count,confidence,weight,last_evidence_at,evidence_run_ids)
               VALUES(%s,%s,%s,%s,'proposed',%s,%s,%s,%s,%s,%s,%s)""",
            (str(tenant), source, target, contract, evidence, success, fail,
             confidence, weight, last_used, Json(run_ids if isinstance(run_ids, list) else [])),
        )
        return 1

    @staticmethod
    def prune_dangling_suggestions(cur: Any) -> int:
        """Delete ``proposed`` suggestions whose evidence no longer exists.

        A proposal's evidence lives in the observation tables (copied into
        ``evidence_run_ids`` via ``edge_stats``).  If those observations are
        gone the row keeps advertising ``evidence_count`` / ``success_count``
        with nothing behind it — a confidence nobody can check, shown in the
        workbench as if it were earned.

        Production never deletes observations (they are append-only), so this
        exists for the one caller that does: the test-suite purge.  Only
        ``proposed`` rows are removed — an ``accepted`` / ``dismissed``
        suggestion is a human decision record and must outlive its evidence.

        Needs a role with DELETE on ``relation_suggestions`` (the app role
        deliberately has none — suggestions only move state), so the caller
        supplies the cursor.  Returns the number of rows removed.
        """
        cur.execute(
            """DELETE FROM experience.relation_suggestions s
               WHERE s.status = 'proposed'
                 AND NOT EXISTS (
                   SELECT 1 FROM jsonb_array_elements_text(s.evidence_run_ids) AS ev(run_id)
                   WHERE EXISTS (
                       SELECT 1 FROM experience.node_observations o
                       WHERE o.run_id::text = ev.run_id
                     )
                     OR EXISTS (
                       SELECT 1 FROM experience.edge_observations e
                       WHERE e.run_id::text = ev.run_id
                     )
                 )"""
        )
        return int(cur.rowcount)

    def decide_suggestion(
        self,
        *,
        tenant_id: UUID,
        suggestion_id: str | UUID,
        decision: str,
        decided_by: str,
        trace_id: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Human L2 decision (accept/dismiss); policy gate runs at the API layer."""
        tenant = _uuid(tenant_id)
        sid = _uuid(suggestion_id)
        with psycopg2.connect(self.database_url) as conn, conn.cursor() as cur:
            cur.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant),))
            cur.fetchone()
            cur.execute(
                "SELECT suggestion_id,status FROM experience.relation_suggestions "
                "WHERE tenant_id=%s AND suggestion_id=%s",
                (str(tenant), sid),
            )
            row = cur.fetchone()
            if row is None:
                raise SuggestionNotFound(str(sid))
            current = row[1]
            resulting = transition_status(current, decision)  # raises on illegal flip
            if resulting != current:
                cur.execute(
                    """UPDATE experience.relation_suggestions
                       SET status=%s,decided_by=%s,decided_at=now(),decision_trace_id=%s,
                           idempotency_key=%s,updated_at=now()
                       WHERE tenant_id=%s AND suggestion_id=%s""",
                    (resulting, decided_by, trace_id, idempotency_key, str(tenant), sid),
                )
            conn.commit()
        logger.info("suggestion %s decided %s by %s (trace=%s)", sid, resulting, decided_by, trace_id)
        return self.get_suggestion(tenant_id=tenant, suggestion_id=sid)

    def get_suggestion(self, *, tenant_id: UUID, suggestion_id: str | UUID) -> dict[str, Any]:
        tenant = _uuid(tenant_id)
        sid = _uuid(suggestion_id)
        with psycopg2.connect(self.database_url) as conn, conn.cursor() as cur:
            cur.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant),))
            cur.fetchone()
            cur.execute(
                """SELECT suggestion_id,source_plugin_id,target_plugin_id,contract_id,status,evidence_count,
                          success_count,fail_count,confidence,weight,last_evidence_at,created_at,
                          decided_by,decided_at,evidence_run_ids,changeset_id,release_id,released_at
                   FROM experience.relation_suggestions WHERE tenant_id=%s AND suggestion_id=%s""",
                (str(tenant), sid),
            )
            row = cur.fetchone()
        if row is None:
            raise SuggestionNotFound(str(sid))
        return _suggestion_dict(row)

    # -- full rebuild from observations --------------------------------------

    def rebuild(self, *, tenant_id: UUID) -> dict[str, Any]:
        """Recompute every rollup row from append-only observations (tenant scoped)."""
        tenant = _uuid(tenant_id)
        with psycopg2.connect(self.database_url) as conn, conn.cursor() as cur:
            cur.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant),))
            cur.fetchone()
            cur.execute("DELETE FROM experience.edge_stats")
            cur.execute("DELETE FROM experience.node_stats")
            cur.execute(
                """SELECT DISTINCT source_plugin_id,target_plugin_id,contract_id
                   FROM experience.edge_observations WHERE tenant_id=%s""",
                (str(tenant),),
            )
            edge_keys = [(r[0], r[1], r[2]) for r in cur.fetchall()]
            for key in edge_keys:
                self._recompute_edge(cur, tenant, key)
            cur.execute(
                """SELECT DISTINCT plugin_id,capability
                   FROM experience.node_observations WHERE tenant_id=%s""",
                (str(tenant),),
            )
            node_keys = [(r[0], r[1]) for r in cur.fetchall()]
            for plugin_id, capability in node_keys:
                self._recompute_node(cur, tenant, plugin_id, capability)
            conn.commit()
        return {"edges": len(edge_keys), "nodes": len(node_keys)}

    # -- read overlay for the API / UI ---------------------------------------

    def read_overlay(self, *, tenant_id: UUID) -> dict[str, Any]:
        """Return edge/node stats for graph overlay.

        Stored ``weight`` is a time-free base (usage × confidence); the 90-day
        half-life decay is applied here against ``last_used_at`` so older
        evidence fades without rewriting any row (design §5.3).
        """
        tenant = _uuid(tenant_id)
        now = datetime.now(timezone.utc)

        def decayed(base: float, last_used: datetime | None) -> float:
            if last_used is None:
                return base
            return base * half_life_decay(age_days(last_used, now) or 0.0)

        with psycopg2.connect(self.database_url) as conn, conn.cursor() as cur:
            cur.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant),))
            cur.fetchone()
            cur.execute(
                """SELECT source_plugin_id,target_plugin_id,contract_id,success_count,fail_count,
                          total_latency_ms,declared,confidence,weight,first_used_at,last_used_at,
                          evidence_run_ids
                   FROM experience.edge_stats ORDER BY source_plugin_id,target_plugin_id,contract_id"""
            )
            edge_rows = [
                {
                    "source": r[0], "target": r[1], "contract": r[2],
                    "success_count": r[3], "fail_count": r[4], "total_latency_ms": r[5],
                    "declared": r[6], "confidence": float(r[7]),
                    "base_weight": float(r[8]), "weight": decayed(float(r[8]), r[10]),
                    "first_used_at": r[9].isoformat() if r[9] else None,
                    "last_used_at": r[10].isoformat() if r[10] else None,
                    "evidence_run_ids": r[11] if isinstance(r[11], list) else [],
                }
                for r in cur.fetchall()
            ]
            cur.execute(
                """SELECT plugin_id,capability,use_count,success_count,fail_count,total_latency_ms,
                          error_kind_counts,confidence,weight,first_used_at,last_used_at
                   FROM experience.node_stats ORDER BY plugin_id,capability"""
            )
            node_rows = [
                {
                    "plugin_id": r[0], "capability": r[1], "use_count": r[2],
                    "success_count": r[3], "fail_count": r[4], "total_latency_ms": r[5],
                    "error_kind_counts": r[6] if isinstance(r[6], dict) else {},
                    "confidence": float(r[7]),
                    "base_weight": float(r[8]), "weight": decayed(float(r[8]), r[10]),
                    "first_used_at": r[9].isoformat() if r[9] else None,
                    "last_used_at": r[10].isoformat() if r[10] else None,
                }
                for r in cur.fetchall()
            ]
            cur.execute(
                """SELECT suggestion_id,source_plugin_id,target_plugin_id,contract_id,status,evidence_count,
                          success_count,fail_count,confidence,weight,last_evidence_at,created_at,
                          decided_by,decided_at,evidence_run_ids,changeset_id,release_id,released_at
                   FROM experience.relation_suggestions
                   ORDER BY status DESC, weight DESC, updated_at DESC"""
            )
            suggestion_rows = [_suggestion_dict(r) for r in cur.fetchall()]
            used_in_rows = self._used_in_rows(cur)
        return {
            "edge_stats": edge_rows,
            "node_stats": node_rows,
            "suggestions": suggestion_rows,
            "used_in": used_in_rows,
        }

    # -- project anchor -------------------------------------------------------

    def archive_run(
        self,
        *,
        tenant_id: UUID,
        run_id: str | UUID,
        project_id: str | UUID,
        trace_id: str,
        note: str = "",
    ) -> dict[str, Any]:
        """Anchor a run to the business project it was performed for.

        Idempotent on ``(tenant, run, project)``; re-archiving the same pair
        returns the existing link untouched.  Append-only — a link records what
        happened and is never rewritten.

        Raises :class:`ValueError` when the project does not exist in the
        tenant, rather than letting a foreign-key violation surface as a 500.
        """
        tenant = _uuid(tenant_id)
        run_uuid, project_uuid = _uuid(run_id), _uuid(project_id)
        with psycopg2.connect(self.database_url) as conn, conn.cursor() as cur:
            cur.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant),))
            cur.fetchone()
            cur.execute(
                """SELECT archive_link_id, archived_at FROM experience.archive_links
                   WHERE tenant_id=%s AND run_id=%s AND project_id=%s""",
                (str(tenant), str(run_uuid), str(project_uuid)),
            )
            existing = cur.fetchone()
            if existing is not None:
                return {
                    "archive_link_id": str(existing[0]),
                    "archived_at": existing[1].isoformat(),
                    "idempotent": True,
                }
            try:
                cur.execute(
                    """INSERT INTO experience.archive_links(tenant_id,run_id,project_id,note,trace_id)
                       VALUES(%s,%s,%s,%s,%s) RETURNING archive_link_id, archived_at""",
                    (str(tenant), str(run_uuid), str(project_uuid), note, trace_id),
                )
            except psycopg2.errors.ForeignKeyViolation as exc:
                raise ValueError(f"project not found in this tenant: {project_uuid}") from exc
            row = cur.fetchone()
            if row is None:
                raise RuntimeError("archive link insert returned no row")
            return {
                "archive_link_id": str(row[0]),
                "archived_at": row[1].isoformat(),
                "idempotent": False,
            }

    def used_in(self, *, tenant_id: UUID) -> list[dict[str, Any]]:
        """``plugin@version -> project`` usage, from archived runs only.

        Answers "which business projects has this plugin been proven in?" — the
        accumulation the knowledge graph shows.  A blank ``plugin_version``
        means the observation predates version recording (``0060``/``0062``);
        it is reported as-is rather than attributed to a guess.
        """
        tenant = _uuid(tenant_id)
        with psycopg2.connect(self.database_url) as conn, conn.cursor() as cur:
            cur.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant),))
            cur.fetchone()
            return self._used_in_rows(cur)

    def _used_in_rows(self, cur: Any) -> list[dict[str, Any]]:
        """The ``used_in`` projection, on a caller's tenant-scoped cursor."""
        cur.execute(
            """SELECT o.plugin_id, o.plugin_version, l.project_id, p.slug, p.name,
                      count(*) AS use_count,
                      count(*) FILTER (WHERE o.status='succeeded') AS success_count,
                      max(o.observed_at) AS last_used_at
               FROM experience.node_observations o
               JOIN experience.archive_links l
                 ON l.tenant_id = o.tenant_id AND l.run_id = o.run_id
               JOIN iam.projects p ON p.id = l.project_id
               GROUP BY o.plugin_id, o.plugin_version, l.project_id, p.slug, p.name
               ORDER BY o.plugin_id, o.plugin_version, p.slug"""
        )
        return [
            {
                "plugin_id": r[0],
                "plugin_version": r[1],
                "project_id": str(r[2]),
                "project_slug": r[3],
                "project_name": r[4],
                "use_count": int(r[5]),
                "success_count": int(r[6]),
                "last_used_at": r[7].isoformat() if r[7] else None,
            }
            for r in cur.fetchall()
        ]


def _suggestion_dict(r: Any) -> dict[str, Any]:
    return {
        "id": str(r[0]),
        "source": r[1],
        "target": r[2],
        "contract": r[3],
        "status": r[4],
        "evidence_count": r[5],
        "success_count": r[6],
        "fail_count": r[7],
        "confidence": float(r[8]),
        "weight": float(r[9]),
        "last_evidence_at": r[10].isoformat() if r[10] else None,
        "created_at": r[11].isoformat() if r[11] else None,
        "decided_by": r[12],
        "decided_at": r[13].isoformat() if r[13] else None,
        "evidence_run_ids": r[14] if isinstance(r[14], list) else [],
        "changeset_id": str(r[15]) if r[15] is not None else None,
        "release_id": str(r[16]) if r[16] is not None else None,
        "released_at": r[17].isoformat() if r[17] else None,
    }


__all__ = ["ExperienceProjector", "Handover", "NodeUse", "project_run_best_effort"]


def project_run_best_effort(
    database_url: str,
    *,
    tenant_id: UUID,
    run_id: str | UUID,
    trace_id: str,
    declared_edges: set[tuple[str, str, str]] | None = None,
) -> dict[str, Any] | None:
    """Project a finished run in a separate short transaction (design §5.1).

    Runs *after* the business run has committed.  Any failure here is logged and
    swallowed so experience accumulation can never break or roll back a real
    plugin run; ``rebuild`` always provides a recovery path.
    """
    try:
        return ExperienceProjector(database_url).project_run(
            tenant_id=tenant_id,
            run_id=run_id,
            trace_id=trace_id,
            declared_edges=declared_edges,
        )
    except Exception:  # defensive: projection must never break a run
        logger.exception("experience projection failed for run %s (non-blocking)", run_id)
        return None
