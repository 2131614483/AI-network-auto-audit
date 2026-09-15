"""Explicit registration command for verified Phase 1 built-in plugins.

Registration is deliberately separate from API startup and invocation.  An
operator must run this command (or an equivalent reviewed deployment step)
before a tenant can invoke a builtin runtime.
"""

from __future__ import annotations

import argparse
import json
import os
from uuid import UUID

import psycopg2
from psycopg2.extras import Json

from packages.catalog.registry import PluginRegistry
from packages.plugin_runtime.layout import declaration_dir
from packages.plugin_runtime.runner import (
    PROJECT_ROOT,
    PluginRuntimeError,
    load_verified_binding,
    verified_builtin_ids,
)

_READ_ONLY_POLICY_NAME = "phase1-plugin-runtime-read-only"
_READ_ONLY_RULE_ID = "6a2d4146-fb4c-4a2a-9d5b-42c494e7ea18"
_RICH_MEDIA_POLICY_NAME = "phase3-rich-media-extract"
_RICH_MEDIA_RULE_ID = "8b1f3c7d-2e4a-4d9f-9a1c-7c5e2f9d1a4b"
_GRAPH_EXTRACTION_POLICY_NAME = "phase5-graph-extraction"
_GRAPH_EXTRACTION_RULE_ID = "9c2f4a6b-3e5b-4a0c-b17c-8d6f3a0e2a5b"
_GRAPH_GOVERNANCE_POLICY_NAME = "phase5-graph-governance"
_GRAPH_GOVERNANCE_APPLY_RULE_ID = "5a1d8c0e-77c4-4f1b-9d3a-2b6e4f0a7c1e"
_GRAPH_GOVERNANCE_REJECT_RULE_ID = "3f8b2d6a-1e9c-4a50-a7d4-8c0e9f1b6d27"
_PHASE6_POLICY_NAME = "phase6-graph-merge-arbitration"
_PHASE6_MERGE_RULE_ID = "2c4e6a8d-59f3-4b7c-a21e-7d9f0b3a6c52"
_PHASE6_SPLIT_RULE_ID = "4e8a2c6f-03d1-4e5a-9b2f-c6a8d0e4f2b7"
_PHASE6_ARBITRATION_RULE_ID = "7b1d3f59-2a8c-46e0-9c3a-1f4d8b2e6a09"
_PHASE7_POLICY_NAME = "phase7-audit-evidence-chain"
_PHASE7_READ_RULE_ID = "8c2e4a6f-1b3d-4e7a-9c5b-3f1d6a8e2b09"
_PHASE7_CONFIRM_RULE_ID = "a3f5b7d9-2e4c-4f8a-b1d6-5c9e0f2a8d3b"
_PHASE8_POLICY_NAME = "phase8-quant-evidence-chain"
_PHASE8_READ_RULE_ID = "6d1b3f8a-4c2e-4b9d-a7f0-2e5c8a1d3f6b"
_PHASE9_POLICY_NAME = "phase9-aiops-governance"
_PHASE9_READ_RULE_ID = "3e9a1d4b-7c5f-4a20-b6d8-1f2e3c4b5a6d"
_PHASE9_VERIFY_RULE_ID = "9b2c7e1a-4d6f-4b8c-a5e0-3d1f7a2c6b9e"
_LEDGER_QUALITY_POLICY_NAME = "phase10-audit-ledger-quality"
_LEDGER_QUALITY_RULE_ID = "5f4a3b2c-1d9e-4c8a-b726-0f3e5a9d7c12"
_R1_PLUGINS_POLICY_NAME = "phase10-r1-readonly-plugins"
_R1_JOURNAL_RULE_ID = "1a9c3e5f-2b4d-4f6a-8c1e-7a2f9d0b4c6e"
_R1_ENTITY_RULE_ID = "3b2d4f6a-8c1e-4a5b-9d7e-1f2a3b4c5d6e"
_R1_SNAPSHOT_RULE_ID = "5c4e6a8f-0d2b-4c3e-b1f5-6a7b8c9d0e2f"
_R1_FACTOR_RULE_ID = "7d6f8b1a-3e4c-4d5f-a2b7-8c9d0e1f2a3b"
_R1_ALERT_RULE_ID = "9e8a1c3d-5f6e-4a7b-c3d9-0e1f2a3b4c5d"
_R1_RCA_RULE_ID = "2b4d6f8a-7c1e-4f5a-b8d0-1e2f3a4b5c6d"
_GRAPH_PLANNING_POLICY_NAME = "local-plugin-topology-graph-plan"
# Matches the rule seeded by migration 0048_graph_driven_planning so the
# registration script only flips the policy set active (fail-closed default).
_GRAPH_PLANNING_RULE_ID = "b1c2d3e4-5a6b-7c8d-9e0f-1a2b3c4d5e6f"
_R2_PROPOSAL_POLICY_NAME = "phase10-r2-graph-proposal"
_R2_PROPOSAL_RULE_ID = "4c8e2a6d-b5f1-4d9a-a37e-8b0c2d4f6e1a"
_R2_INVESTIGATION_POLICY_NAME = "phase10-r2-investigation-plan"
_R2_INVESTIGATION_RULE_ID = "6a1d3b5f-7c9e-4a2d-b8f4-0e1c3a5d7f9b"
_R2_FINDING_POLICY_NAME = "phase10-r2-finding-draft"
_R2_FINDING_RULE_ID = "8b3e5c7d-0a1f-4b3e-c9d5-2f3a4b5c6d7e"
_R2_EXPERIMENT_POLICY_NAME = "phase10-r2-experiment-evaluator"
_R2_EXPERIMENT_RULE_ID = "0c4f6e8a-2b3d-4c5e-a7f1-3f4a5b6c7d8e"
_R2_PLAYBOOK_POLICY_NAME = "phase10-r2-playbook-proposer"
_R2_PLAYBOOK_RULE_ID = "2e5a7c9b-4d6f-4a8e-b1d3-5f6a7b8c9d0e"
_R2_RECOVERY_POLICY_NAME = "phase10-r2-recovery-verifier"
_R2_RECOVERY_RULE_ID = "4a6c8e0d-5f7a-4b9c-a2e4-7f8a9b0c1d2e"
_ALERT_TRIAGE_POLICY_NAME = "phase10-r0-alert-triage"
_ALERT_TRIAGE_RULE_ID = "6b8a2c4e-1d3f-4e5a-9c7b-0f1e2d3c4b5a"
_LINEAGE_POLICY_NAME = "phase10-r0-evidence-lineage"
_LINEAGE_RULE_ID = "1a3b5c7d-9e0f-4a1b-8c2d-3e4f5a6b7c8d"
_BACKTEST_POLICY_NAME = "phase10-r0-simulated-backtest"
_BACKTEST_RULE_ID = "7c9e2a4d-6b8f-4a1c-b3e5-8d2f4a6c0e1b"
_WORKPAPER_POLICY_NAME = "phase10-r3-workpaper-export"
_WORKPAPER_RULE_ID = "2f6d9a1c-8b3e-4e7a-9c5d-0a1f2b3c4d5e"
_REPORT_DRAFT_POLICY_NAME = "phase10-r3-report-draft"
_REPORT_DRAFT_RULE_ID = "4a8c2e6d-9b5f-4a1c-b3d7-1e2f3a4b5c6d"
_TICKET_DRAFT_POLICY_NAME = "phase10-r3-ticket-draft"
_TICKET_DRAFT_RULE_ID = "6c9e1a3b-5d7f-4b8a-c2d4-0e3f5a7b9c1d"
_RETENTION_POLICY_NAME = "phase10-r3-retention-recommendation"
_RETENTION_RULE_ID = "8e2f4a6c-1d3b-4f5a-b7c9-2e4f6a8b0c1d"
_POSTMORTEM_DRAFT_POLICY_NAME = "phase10-r3-postmortem-draft"
_POSTMORTEM_DRAFT_RULE_ID = "0d1e2f3a-4b5c-6d7e-8f9a-1b2c3d4e5f6a"
_RESEARCH_NOTE_DRAFT_POLICY_NAME = "phase10-r3-research-note-draft"
_RESEARCH_NOTE_DRAFT_RULE_ID = "1c2d3e4f-5a6b-7c8d-9e0f-1a2b3c4d5e6f"


def register_verified_builtin(database_url: str, *, tenant_slug: str = "local-dev") -> dict[str, UUID]:
    """Publish every verified built-in after checking its code binding.

    Registration is deliberately separate from API startup and invocation: it
    only publishes the checked-in read-only manifests in the tenant catalog and
    never grants execution rights.  Each capability still requires its own
    explicit narrow policy rule before the runtime will start a child process.
    """

    registry = PluginRegistry(
        database_url,
        schema_dir=PROJECT_ROOT / "contracts" / "jsonschema",
        tenant_slug=tenant_slug,
    )
    version_ids: dict[str, UUID] = {}
    for plugin_id in verified_builtin_ids():
        binding = load_verified_binding(plugin_id)
        try:
            manifest = json.loads((declaration_dir(plugin_id) / "plugin.manifest.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise PluginRuntimeError(f"verified builtin manifest cannot be read: {plugin_id}") from exc
        if not isinstance(manifest, dict):
            raise PluginRuntimeError(f"verified builtin manifest must be an object: {plugin_id}")
        if (
            manifest.get("id") != binding.plugin_id
            or manifest.get("version") != binding.version
            or manifest.get("entrypoint") != binding.entrypoint
            or manifest.get("side_effects") != "read_only"
        ):
            raise PluginRuntimeError("verified builtin manifest identity does not match runtime binding")
        version_ids[plugin_id] = registry.register(
            manifest, descriptor_sha256=binding.protocol_sha256
        )
    return version_ids


def publish_read_only_allow_policy(database_url: str, *, tenant_slug: str = "local-dev") -> UUID:
    """Publish the narrow allow-list needed by the Phase 1 read-only runtime.

    This is intentionally a separate operator action.  It authorizes only the
    one read-only capability and does not enable broad AUTO mode, arbitrary
    plugin execution, file writes, network access or another capability.
    """

    with psycopg2.connect(database_url) as connection:
        with connection.cursor() as cur:
            cur.execute("SELECT id FROM iam.tenants WHERE slug=%s", (tenant_slug,))
            row = cur.fetchone()
            if row is None:
                raise ValueError(f"tenant not found: {tenant_slug}")
            tenant_id = UUID(str(row[0]))
            cur.execute("SELECT set_config('app.tenant_id',%s,false)", (str(tenant_id),))
            cur.execute(
                """
                INSERT INTO policy.policy_sets(tenant_id,name,version,status,rules)
                VALUES(%s,%s,1,'active',%s)
                ON CONFLICT(tenant_id,name,version)
                DO UPDATE SET status='active', rules=EXCLUDED.rules
                """,
                (
                    tenant_id,
                    _READ_ONLY_POLICY_NAME,
                    Json(
                        [
                            {
                                "rule_id": _READ_ONLY_RULE_ID,
                                "effect": "allow",
                                "match": {
                                    "capabilities": ["knowledge.extract.document"],
                                    "risk_classes": ["read_only"],
                                    "side_effects": ["read_only"],
                                },
                            }
                        ]
                    ),
                ),
            )
    return tenant_id


def publish_rich_media_allow_policy(database_url: str, *, tenant_slug: str = "local-dev") -> UUID:
    """Publish the narrow allow-list for the policy-gated local MinerU worker.

    It authorizes exactly one capability (``knowledge.extract.rich_media``) with
    high-risk write semantics, without enabling AUTO mode, arbitrary execution,
    file writes outside the drop root, or network access.
    """

    with psycopg2.connect(database_url) as connection:
        with connection.cursor() as cur:
            cur.execute("SELECT id FROM iam.tenants WHERE slug=%s", (tenant_slug,))
            row = cur.fetchone()
            if row is None:
                raise ValueError(f"tenant not found: {tenant_slug}")
            tenant_id = UUID(str(row[0]))
            cur.execute("SELECT set_config('app.tenant_id',%s,false)", (str(tenant_id),))
            cur.execute(
                """
                INSERT INTO policy.policy_sets(tenant_id,name,version,status,rules)
                VALUES(%s,%s,1,'active',%s)
                ON CONFLICT(tenant_id,name,version)
                DO UPDATE SET status='active', rules=EXCLUDED.rules
                """,
                (
                    tenant_id,
                    _RICH_MEDIA_POLICY_NAME,
                    Json(
                        [
                            {
                                "rule_id": _RICH_MEDIA_RULE_ID,
                                "effect": "allow",
                                "match": {
                                    "capabilities": ["knowledge.extract.rich_media"],
                                    "risk_classes": ["high"],
                                    "side_effects": ["write_data"],
                                },
                            }
                        ]
                    ),
                ),
            )
    return tenant_id


def publish_graph_extraction_allow_policy(database_url: str, *, tenant_slug: str = "local-dev") -> UUID:
    """Publish the narrow allow-list for Phase 5 governed graph extraction.

    It authorizes exactly one capability (``knowledge.extract.graph``) for
    read-only preview and low-risk shadow-ChangeSet staging.  It does not
    authorize applying a release, direct active-graph writes, AUTO mode,
    external network or arbitrary execution.
    """

    with psycopg2.connect(database_url) as connection:
        with connection.cursor() as cur:
            cur.execute("SELECT id FROM iam.tenants WHERE slug=%s", (tenant_slug,))
            row = cur.fetchone()
            if row is None:
                raise ValueError(f"tenant not found: {tenant_slug}")
            tenant_id = UUID(str(row[0]))
            cur.execute("SELECT set_config('app.tenant_id',%s,false)", (str(tenant_id),))
            cur.execute(
                """
                INSERT INTO policy.policy_sets(tenant_id,name,version,status,rules)
                VALUES(%s,%s,1,'active',%s)
                ON CONFLICT(tenant_id,name,version)
                DO UPDATE SET status='active', rules=EXCLUDED.rules
                """,
                (
                    tenant_id,
                    _GRAPH_EXTRACTION_POLICY_NAME,
                    Json(
                        [
                            {
                                "rule_id": _GRAPH_EXTRACTION_RULE_ID,
                                "effect": "allow",
                                "match": {
                                    "capabilities": ["knowledge.extract.graph"],
                                    "risk_classes": ["read_only", "low"],
                                    "side_effects": ["read_only", "write_data"],
                                },
                            }
                        ]
                    ),
                ),
            )
    return tenant_id


def publish_graph_governance_allow_policy(database_url: str, *, tenant_slug: str = "local-dev") -> UUID:
    """Publish the narrow allow-list for Phase 5 ChangeSet governance.

    Authorizes two capabilities only:
      - ``graph.change.apply``  (放行) risk_class=medium, write_data
      - ``graph.change.reject`` (驳回) risk_class=low, write_data
    It never authorizes running AUTO mode, external network or arbitrary execution.
    """

    with psycopg2.connect(database_url) as connection:
        with connection.cursor() as cur:
            cur.execute("SELECT id FROM iam.tenants WHERE slug=%s", (tenant_slug,))
            row = cur.fetchone()
            if row is None:
                raise ValueError(f"tenant not found: {tenant_slug}")
            tenant_id = UUID(str(row[0]))
            cur.execute("SELECT set_config('app.tenant_id',%s,false)", (str(tenant_id),))
            cur.execute(
                """
                INSERT INTO policy.policy_sets(tenant_id,name,version,status,rules)
                VALUES(%s,%s,1,'active',%s)
                ON CONFLICT(tenant_id,name,version)
                DO UPDATE SET status='active', rules=EXCLUDED.rules
                """,
                (
                    tenant_id,
                    _GRAPH_GOVERNANCE_POLICY_NAME,
                    Json(
                        [
                            {
                                "rule_id": _GRAPH_GOVERNANCE_APPLY_RULE_ID,
                                "effect": "allow",
                                "match": {
                                    "capabilities": ["graph.change.apply"],
                                    "risk_classes": ["medium"],
                                    "side_effects": ["write_data"],
                                },
                            },
                            {
                                "rule_id": _GRAPH_GOVERNANCE_REJECT_RULE_ID,
                                "effect": "allow",
                                "match": {
                                    "capabilities": ["graph.change.reject"],
                                    "risk_classes": ["low"],
                                    "side_effects": ["write_data"],
                                },
                            },
                        ]
                    ),
                ),
            )
    return tenant_id


def publish_graph_merge_arbitration_allow_policy(database_url: str, *, tenant_slug: str = "local-dev") -> UUID:
    """Publish the narrow allow-list for Phase 6 graph merge/split/arbitration.

    Authorizes three write capabilities only, all ``medium``/``write_data``:
      - ``graph.node.merge``      受治理合并
      - ``graph.node.split``      受治理拆分
      - ``graph.arbitration.resolve``  显式冲突仲裁
    Read-only ledger access stays behind the existing governance read capability.
    """

    with psycopg2.connect(database_url) as connection:
        with connection.cursor() as cur:
            cur.execute("SELECT id FROM iam.tenants WHERE slug=%s", (tenant_slug,))
            row = cur.fetchone()
            if row is None:
                raise ValueError(f"tenant not found: {tenant_slug}")
            tenant_id = UUID(str(row[0]))
            cur.execute("SELECT set_config('app.tenant_id',%s,false)", (str(tenant_id),))
            cur.execute(
                """
                INSERT INTO policy.policy_sets(tenant_id,name,version,status,rules)
                VALUES(%s,%s,1,'active',%s)
                ON CONFLICT(tenant_id,name,version)
                DO UPDATE SET status='active', rules=EXCLUDED.rules
                """,
                (
                    tenant_id,
                    _PHASE6_POLICY_NAME,
                    Json(
                        [
                            {
                                "rule_id": _PHASE6_MERGE_RULE_ID,
                                "effect": "allow",
                                "match": {
                                    "capabilities": ["graph.node.merge"],
                                    "risk_classes": ["medium"],
                                    "side_effects": ["write_data"],
                                },
                            },
                            {
                                "rule_id": _PHASE6_SPLIT_RULE_ID,
                                "effect": "allow",
                                "match": {
                                    "capabilities": ["graph.node.split"],
                                    "risk_classes": ["medium"],
                                    "side_effects": ["write_data"],
                                },
                            },
                            {
                                "rule_id": _PHASE6_ARBITRATION_RULE_ID,
                                "effect": "allow",
                                "match": {
                                    "capabilities": ["graph.arbitration.resolve"],
                                    "risk_classes": ["medium"],
                                    "side_effects": ["write_data"],
                                },
                            },
                        ]
                    ),
                ),
            )
    return tenant_id


def publish_audit_evidence_chain_allow_policy(database_url: str, *, tenant_slug: str = "local-dev") -> UUID:
    """Publish the narrow allow-list for Phase 7 audit evidence chain.

    Authorizes two capabilities only:
      - ``audit.chain.read``       read_only, evidence-chain/linkage reads
      - ``audit.finding.confirm``  medium/write_data, explicit finding confirmation
    The confirmation route never enables AUTO, arbitrary execution or network access.
    """

    with psycopg2.connect(database_url) as connection:
        with connection.cursor() as cur:
            cur.execute("SELECT id FROM iam.tenants WHERE slug=%s", (tenant_slug,))
            row = cur.fetchone()
            if row is None:
                raise ValueError(f"tenant not found: {tenant_slug}")
            tenant_id = UUID(str(row[0]))
            cur.execute("SELECT set_config('app.tenant_id',%s,false)", (str(tenant_id),))
            cur.execute(
                """
                INSERT INTO policy.policy_sets(tenant_id,name,version,status,rules)
                VALUES(%s,%s,1,'active',%s)
                ON CONFLICT(tenant_id,name,version)
                DO UPDATE SET status='active', rules=EXCLUDED.rules
                """,
                (
                    tenant_id,
                    _PHASE7_POLICY_NAME,
                    Json(
                        [
                            {
                                "rule_id": _PHASE7_READ_RULE_ID,
                                "effect": "allow",
                                "match": {
                                    "capabilities": ["audit.chain.read"],
                                    "risk_classes": ["read_only"],
                                    "side_effects": ["read_only"],
                                },
                            },
                            {
                                "rule_id": _PHASE7_CONFIRM_RULE_ID,
                                "effect": "allow",
                                "match": {
                                    "capabilities": ["audit.finding.confirm"],
                                    "risk_classes": ["medium"],
                                    "side_effects": ["write_data"],
                                },
                            },
                        ]
                    ),
                ),
            )
    return tenant_id


def publish_quant_evidence_chain_allow_policy(database_url: str, *, tenant_slug: str = "local-dev") -> UUID:
    """Publish the narrow read-only allow-list for Phase 8 quant evidence chain.

    Authorizes exactly one capability:
      - ``quant.chain.read``  read_only, backtest/dataset lineage reads
    The read route never enables AUTO, arbitrary execution, network access or
    any write to ``quant.*``.
    """

    with psycopg2.connect(database_url) as connection:
        with connection.cursor() as cur:
            cur.execute("SELECT id FROM iam.tenants WHERE slug=%s", (tenant_slug,))
            row = cur.fetchone()
            if row is None:
                raise ValueError(f"tenant not found: {tenant_slug}")
            tenant_id = UUID(str(row[0]))
            cur.execute("SELECT set_config('app.tenant_id',%s,false)", (str(tenant_id),))
            cur.execute(
                """
                INSERT INTO policy.policy_sets(tenant_id,name,version,status,rules)
                VALUES(%s,%s,1,'active',%s)
                ON CONFLICT(tenant_id,name,version)
                DO UPDATE SET status='active', rules=EXCLUDED.rules
                """,
                (
                    tenant_id,
                    _PHASE8_POLICY_NAME,
                    Json(
                        [
                            {
                                "rule_id": _PHASE8_READ_RULE_ID,
                                "effect": "allow",
                                "match": {
                                    "capabilities": ["quant.chain.read"],
                                    "risk_classes": ["read_only"],
                                    "side_effects": ["read_only"],
                                },
                            }
                        ]
                    ),
                ),
            )
    return tenant_id


def publish_ledger_quality_allow_policy(database_url: str, *, tenant_slug: str = "local-dev") -> UUID:
    """Publish the narrow read-only allow-list for the audit.ledger-quality plugin.

    Authorizes exactly one capability:
      - ``audit.ledger.validate``  read_only, ledger-snapshot validation
    The invoke route never enables AUTO, arbitrary execution, network access,
    file writes or any ledger mutation.
    """

    with psycopg2.connect(database_url) as connection:
        with connection.cursor() as cur:
            cur.execute("SELECT id FROM iam.tenants WHERE slug=%s", (tenant_slug,))
            row = cur.fetchone()
            if row is None:
                raise ValueError(f"tenant not found: {tenant_slug}")
            tenant_id = UUID(str(row[0]))
            cur.execute("SELECT set_config('app.tenant_id',%s,false)", (str(tenant_id),))
            cur.execute(
                """
                INSERT INTO policy.policy_sets(tenant_id,name,version,status,rules)
                VALUES(%s,%s,1,'active',%s)
                ON CONFLICT(tenant_id,name,version)
                DO UPDATE SET status='active', rules=EXCLUDED.rules
                """,
                (
                    tenant_id,
                    _LEDGER_QUALITY_POLICY_NAME,
                    Json(
                        [
                            {
                                "rule_id": _LEDGER_QUALITY_RULE_ID,
                                "effect": "allow",
                                "match": {
                                    "capabilities": ["audit.ledger.validate"],
                                    "risk_classes": ["read_only"],
                                    "side_effects": ["read_only"],
                                },
                            }
                        ]
                    ),
                ),
            )
    return tenant_id


def publish_aiops_governance_allow_policy(database_url: str, *, tenant_slug: str = "local-dev") -> UUID:
    """Publish the exact Phase 9 allow-list for local AIOps governance.

    This permits evidence-chain reads and one human Canary verification record.
    It never authorizes Playbook execution, live mode, external commands,
    network access or broad AUTO behavior.
    """

    with psycopg2.connect(database_url) as connection:
        with connection.cursor() as cur:
            cur.execute("SELECT id FROM iam.tenants WHERE slug=%s", (tenant_slug,))
            row = cur.fetchone()
            if row is None:
                raise ValueError(f"tenant not found: {tenant_slug}")
            tenant_id = UUID(str(row[0]))
            cur.execute("SELECT set_config('app.tenant_id',%s,false)", (str(tenant_id),))
            cur.execute(
                """
                INSERT INTO policy.policy_sets(tenant_id,name,version,status,rules)
                VALUES(%s,%s,1,'active',%s)
                ON CONFLICT(tenant_id,name,version)
                DO UPDATE SET status='active', rules=EXCLUDED.rules
                """,
                (
                    tenant_id,
                    _PHASE9_POLICY_NAME,
                    Json(
                        [
                            {
                                "rule_id": _PHASE9_READ_RULE_ID,
                                "effect": "allow",
                                "match": {
                                    "capabilities": ["aiops.chain.read"],
                                    "risk_classes": ["read_only"],
                                    "side_effects": ["read_only"],
                                },
                            },
                            {
                                "rule_id": _PHASE9_VERIFY_RULE_ID,
                                "effect": "allow",
                                "match": {
                                    "capabilities": ["aiops.canary.verify"],
                                    "risk_classes": ["medium"],
                                    "side_effects": ["write_data"],
                                },
                            },
                        ]
                    ),
                ),
            )
    return tenant_id


def publish_r1_plugins_allow_policy(database_url: str, *, tenant_slug: str = "local-dev") -> UUID:
    """Publish the narrow read-only allow-list for the Phase 10 R1 plugin batch.

    Authorizes exactly six deterministic read-only capabilities:
      - ``audit.journal.detect``       journal anomaly candidates
      - ``knowledge.extract.relations`` entity/relation graph candidates
      - ``quant.dataset.validate``     market snapshot dataset gates
      - ``quant.factor.compute``       deterministic factor computation
      - ``aiops.alert.correlate``      alert correlation candidates
      - ``aiops.rca.rank``             root-cause candidate ranking
    It never enables AUTO mode, arbitrary execution, network access, file
    writes or any mutation of graph nodes, findings, backtests or executions.
    """

    with psycopg2.connect(database_url) as connection:
        with connection.cursor() as cur:
            cur.execute("SELECT id FROM iam.tenants WHERE slug=%s", (tenant_slug,))
            row = cur.fetchone()
            if row is None:
                raise ValueError(f"tenant not found: {tenant_slug}")
            tenant_id = UUID(str(row[0]))
            cur.execute("SELECT set_config('app.tenant_id',%s,false)", (str(tenant_id),))
            cur.execute(
                """
                INSERT INTO policy.policy_sets(tenant_id,name,version,status,rules)
                VALUES(%s,%s,1,'active',%s)
                ON CONFLICT(tenant_id,name,version)
                DO UPDATE SET status='active', rules=EXCLUDED.rules
                """,
                (
                    tenant_id,
                    _R1_PLUGINS_POLICY_NAME,
                    Json(
                        [
                            {
                                "rule_id": rule_id,
                                "effect": "allow",
                                "match": {
                                    "capabilities": [capability],
                                    "risk_classes": ["read_only"],
                                    "side_effects": ["read_only"],
                                },
                            }
                            for rule_id, capability in (
                                (_R1_JOURNAL_RULE_ID, "audit.journal.detect"),
                                (_R1_ENTITY_RULE_ID, "knowledge.extract.relations"),
                                (_R1_SNAPSHOT_RULE_ID, "quant.dataset.validate"),
                                (_R1_FACTOR_RULE_ID, "quant.factor.compute"),
                                (_R1_ALERT_RULE_ID, "aiops.alert.correlate"),
                                (_R1_RCA_RULE_ID, "aiops.rca.rank"),
                            )
                        ]
                    ),
                ),
            )
    return tenant_id


def publish_topology_graph_planning_allow_policy(database_url: str, *, tenant_slug: str = "local-dev") -> UUID:
    """Activate the M10 graph-driven planning policy set (fail-closed by default).

    Migration 0048 seeds ``local-plugin-topology-graph-plan`` as **inactive**
    with exactly one rule authorizing ``topology.intent.plan`` (low /
    write_data).  This function flips that policy set active for acceptance;
    without an explicit publish the endpoint stays fail-closed (403).  It never
    enables AUTO mode, arbitrary execution, network access or live execution.
    """

    with psycopg2.connect(database_url) as connection:
        with connection.cursor() as cur:
            cur.execute("SELECT id FROM iam.tenants WHERE slug=%s", (tenant_slug,))
            row = cur.fetchone()
            if row is None:
                raise ValueError(f"tenant not found: {tenant_slug}")
            tenant_id = UUID(str(row[0]))
            cur.execute("SELECT set_config('app.tenant_id',%s,false)", (str(tenant_id),))
            cur.execute(
                """
                INSERT INTO policy.policy_sets(tenant_id,name,version,status,rules)
                VALUES(%s,%s,1,'active',%s)
                ON CONFLICT(tenant_id,name,version)
                DO UPDATE SET status='active', rules=EXCLUDED.rules
                """,
                (
                    tenant_id,
                    _GRAPH_PLANNING_POLICY_NAME,
                    Json(
                        [
                            {
                                "rule_id": _GRAPH_PLANNING_RULE_ID,
                                "effect": "allow",
                                "match": {
                                    "capabilities": ["topology.intent.plan"],
                                    "risk_classes": ["low"],
                                    "side_effects": ["write_data"],
                                },
                            }
                        ]
                    ),
                ),
            )
    return tenant_id


def publish_graph_proposal_allow_policy(database_url: str, *, tenant_slug: str = "local-dev") -> UUID:
    """Publish the narrow read-only allow-list for the Phase 10 R2 proposal builder.

    Authorizes exactly one deterministic capability:
      - ``graph.proposal.build``  read_only, ChangeSet-draft construction
    The subprocess only reads the candidate-set artifact (and an optional graph
    snapshot) and returns a draft; staging that draft into a shadow ChangeSet
    remains a separate governance identity behind its own policy rule.  This
    rule never enables AUTO mode, arbitrary execution, network access, file
    writes or active-graph mutations.
    """

    with psycopg2.connect(database_url) as connection:
        with connection.cursor() as cur:
            cur.execute("SELECT id FROM iam.tenants WHERE slug=%s", (tenant_slug,))
            row = cur.fetchone()
            if row is None:
                raise ValueError(f"tenant not found: {tenant_slug}")
            tenant_id = UUID(str(row[0]))
            cur.execute("SELECT set_config('app.tenant_id',%s,false)", (str(tenant_id),))
            cur.execute(
                """
                INSERT INTO policy.policy_sets(tenant_id,name,version,status,rules)
                VALUES(%s,%s,1,'active',%s)
                ON CONFLICT(tenant_id,name,version)
                DO UPDATE SET status='active', rules=EXCLUDED.rules
                """,
                (
                    tenant_id,
                    _R2_PROPOSAL_POLICY_NAME,
                    Json(
                        [
                            {
                                "rule_id": _R2_PROPOSAL_RULE_ID,
                                "effect": "allow",
                                "match": {
                                    "capabilities": ["graph.proposal.build"],
                                    "risk_classes": ["read_only"],
                                    "side_effects": ["read_only"],
                                },
                            }
                        ]
                    ),
                ),
            )
    return tenant_id


def publish_investigation_plan_allow_policy(database_url: str, *, tenant_slug: str = "local-dev") -> UUID:
    """Publish the narrow read-only allow-list for the Phase 10 R2 investigation plan.

    Authorizes exactly one deterministic capability:
      - ``audit.investigation.plan``  read_only, audit investigation-plan draft
    The subprocess only reads the anomaly-candidates artifact (and an optional
    audit evidence index) and returns a shadow plan draft; confirming Findings
    and mutating evidence remain separate governed identities.  This rule never
    enables AUTO mode, arbitrary execution, network access, file writes or
    finding confirmation.
    """

    with psycopg2.connect(database_url) as connection:
        with connection.cursor() as cur:
            cur.execute("SELECT id FROM iam.tenants WHERE slug=%s", (tenant_slug,))
            row = cur.fetchone()
            if row is None:
                raise ValueError(f"tenant not found: {tenant_slug}")
            tenant_id = UUID(str(row[0]))
            cur.execute("SELECT set_config('app.tenant_id',%s,false)", (str(tenant_id),))
            cur.execute(
                """
                INSERT INTO policy.policy_sets(tenant_id,name,version,status,rules)
                VALUES(%s,%s,1,'active',%s)
                ON CONFLICT(tenant_id,name,version)
                DO UPDATE SET status='active', rules=EXCLUDED.rules
                """,
                (
                    tenant_id,
                    _R2_INVESTIGATION_POLICY_NAME,
                    Json(
                        [
                            {
                                "rule_id": _R2_INVESTIGATION_RULE_ID,
                                "effect": "allow",
                                "match": {
                                    "capabilities": ["audit.investigation.plan"],
                                    "risk_classes": ["read_only"],
                                    "side_effects": ["read_only"],
                                },
                            }
                        ]
                    ),
                ),
            )
    return tenant_id


def publish_finding_draft_allow_policy(database_url: str, *, tenant_slug: str = "local-dev") -> UUID:
    """Publish the narrow read-only allow-list for the Phase 10 R2 finding draft.

    Authorizes exactly one deterministic capability:
      - ``audit.finding.draft``  read_only, audit finding-draft (Claim/EvidenceRef)
    The subprocess only reads the anomaly-candidates artifact (and an optional
    investigation-plan draft) and returns a shadow finding draft; confirming
    Findings and mutating evidence remain separate governed identities.  This
    rule never enables AUTO mode, arbitrary execution, network access, file
    writes or finding confirmation.
    """

    with psycopg2.connect(database_url) as connection:
        with connection.cursor() as cur:
            cur.execute("SELECT id FROM iam.tenants WHERE slug=%s", (tenant_slug,))
            row = cur.fetchone()
            if row is None:
                raise ValueError(f"tenant not found: {tenant_slug}")
            tenant_id = UUID(str(row[0]))
            cur.execute("SELECT set_config('app.tenant_id',%s,false)", (str(tenant_id),))
            cur.execute(
                """
                INSERT INTO policy.policy_sets(tenant_id,name,version,status,rules)
                VALUES(%s,%s,1,'active',%s)
                ON CONFLICT(tenant_id,name,version)
                DO UPDATE SET status='active', rules=EXCLUDED.rules
                """,
                (
                    tenant_id,
                    _R2_FINDING_POLICY_NAME,
                    Json(
                        [
                            {
                                "rule_id": _R2_FINDING_RULE_ID,
                                "effect": "allow",
                                "match": {
                                    "capabilities": ["audit.finding.draft"],
                                    "risk_classes": ["read_only"],
                                    "side_effects": ["read_only"],
                                },
                            }
                        ]
                    ),
                ),
            )
    return tenant_id


def publish_experiment_evaluator_allow_policy(database_url: str, *, tenant_slug: str = "local-dev") -> UUID:
    """Publish the narrow read-only allow-list for the Phase 10 R2 experiment evaluator.

    Authorizes exactly one deterministic capability:
      - ``quant.experiment.evaluate``  read_only, experiment-evaluation research proposal
    The subprocess only reads backtest-report artifacts (challenger and optional
    baseline) and returns a research-proposal draft; updating production models
    and generating orders remain separate governed identities.  This rule never
    enables AUTO mode, arbitrary execution, network access, file writes or
    production promotion.
    """

    with psycopg2.connect(database_url) as connection:
        with connection.cursor() as cur:
            cur.execute("SELECT id FROM iam.tenants WHERE slug=%s", (tenant_slug,))
            row = cur.fetchone()
            if row is None:
                raise ValueError(f"tenant not found: {tenant_slug}")
            tenant_id = UUID(str(row[0]))
            cur.execute("SELECT set_config('app.tenant_id',%s,false)", (str(tenant_id),))
            cur.execute(
                """
                INSERT INTO policy.policy_sets(tenant_id,name,version,status,rules)
                VALUES(%s,%s,1,'active',%s)
                ON CONFLICT(tenant_id,name,version)
                DO UPDATE SET status='active', rules=EXCLUDED.rules
                """,
                (
                    tenant_id,
                    _R2_EXPERIMENT_POLICY_NAME,
                    Json(
                        [
                            {
                                "rule_id": _R2_EXPERIMENT_RULE_ID,
                                "effect": "allow",
                                "match": {
                                    "capabilities": ["quant.experiment.evaluate"],
                                    "risk_classes": ["read_only"],
                                    "side_effects": ["read_only"],
                                },
                            }
                        ]
                    ),
                ),
            )
    return tenant_id


def publish_playbook_proposer_allow_policy(database_url: str, *, tenant_slug: str = "local-dev") -> UUID:
    """Publish the narrow read-only allow-list for the Phase 10 R2 playbook proposer.

    Authorizes exactly one deterministic capability:
      - ``aiops.remediation.propose``  read_only, remediation-proposal shadow draft
    The subprocess only reads an rca-candidates artifact and returns a
    remediation-proposal draft; it never generates free Shell, never creates a
    ChangeRequest and never executes any change.  Execution remains behind
    Policy Gateway, ChangeRequest and human approval.  This rule never enables
    AUTO mode, arbitrary execution, network access or file writes.
    """

    with psycopg2.connect(database_url) as connection:
        with connection.cursor() as cur:
            cur.execute("SELECT id FROM iam.tenants WHERE slug=%s", (tenant_slug,))
            row = cur.fetchone()
            if row is None:
                raise ValueError(f"tenant not found: {tenant_slug}")
            tenant_id = UUID(str(row[0]))
            cur.execute("SELECT set_config('app.tenant_id',%s,false)", (str(tenant_id),))
            cur.execute(
                """
                INSERT INTO policy.policy_sets(tenant_id,name,version,status,rules)
                VALUES(%s,%s,1,'active',%s)
                ON CONFLICT(tenant_id,name,version)
                DO UPDATE SET status='active', rules=EXCLUDED.rules
                """,
                (
                    tenant_id,
                    _R2_PLAYBOOK_POLICY_NAME,
                    Json(
                        [
                            {
                                "rule_id": _R2_PLAYBOOK_RULE_ID,
                                "effect": "allow",
                                "match": {
                                    "capabilities": ["aiops.remediation.propose"],
                                    "risk_classes": ["read_only"],
                                    "side_effects": ["read_only"],
                                },
                            }
                        ]
                    ),
                ),
            )
    return tenant_id


def publish_recovery_verifier_allow_policy(database_url: str, *, tenant_slug: str = "local-dev") -> UUID:
    """Publish the narrow read-only allow-list for the Phase 10 R2 recovery verifier.

    Authorizes exactly one deterministic capability:
      - ``aiops.recovery.verify``  read_only, immutable recovery-verification ledger entry
    The subprocess only reads baseline/observed metric-series artifacts (and an
    optional remediation-proposal artifact) and returns a verification ledger
    entry; it never executes a fix, never lifts a circuit breaker and never
    creates a ChangeRequest.  This rule never enables AUTO mode, arbitrary
    execution, network access or file writes.
    """

    with psycopg2.connect(database_url) as connection:
        with connection.cursor() as cur:
            cur.execute("SELECT id FROM iam.tenants WHERE slug=%s", (tenant_slug,))
            row = cur.fetchone()
            if row is None:
                raise ValueError(f"tenant not found: {tenant_slug}")
            tenant_id = UUID(str(row[0]))
            cur.execute("SELECT set_config('app.tenant_id',%s,false)", (str(tenant_id),))
            cur.execute(
                """
                INSERT INTO policy.policy_sets(tenant_id,name,version,status,rules)
                VALUES(%s,%s,1,'active',%s)
                ON CONFLICT(tenant_id,name,version)
                DO UPDATE SET status='active', rules=EXCLUDED.rules
                """,
                (
                    tenant_id,
                    _R2_RECOVERY_POLICY_NAME,
                    Json(
                        [
                            {
                                "rule_id": _R2_RECOVERY_RULE_ID,
                                "effect": "allow",
                                "match": {
                                    "capabilities": ["aiops.recovery.verify"],
                                    "risk_classes": ["read_only"],
                                    "side_effects": ["read_only"],
                                },
                            }
                        ]
                    ),
                ),
            )
    return tenant_id


def publish_alert_triage_allow_policy(database_url: str, *, tenant_slug: str = "local-dev") -> UUID:
    """Publish the narrow read-only allow-list for the R0-promoted alert-triage plugin.

    Authorizes exactly one deterministic capability:
      - ``aiops.alert.triage``  read_only, human-review incident proposal
    The subprocess only reads a single immutable alert-event artifact and
    returns an incident-proposal draft; it never connects to infrastructure,
    never executes a Playbook and never creates an Incident.  This rule never
    enables AUTO mode, arbitrary execution, network access or file writes.
    """

    with psycopg2.connect(database_url) as connection:
        with connection.cursor() as cur:
            cur.execute("SELECT id FROM iam.tenants WHERE slug=%s", (tenant_slug,))
            row = cur.fetchone()
            if row is None:
                raise ValueError(f"tenant not found: {tenant_slug}")
            tenant_id = UUID(str(row[0]))
            cur.execute("SELECT set_config('app.tenant_id',%s,false)", (str(tenant_id),))
            cur.execute(
                """
                INSERT INTO policy.policy_sets(tenant_id,name,version,status,rules)
                VALUES(%s,%s,1,'active',%s)
                ON CONFLICT(tenant_id,name,version)
                DO UPDATE SET status='active', rules=EXCLUDED.rules
                """,
                (
                    tenant_id,
                    _ALERT_TRIAGE_POLICY_NAME,
                    Json(
                        [
                            {
                                "rule_id": _ALERT_TRIAGE_RULE_ID,
                                "effect": "allow",
                                "match": {
                                    "capabilities": ["aiops.alert.triage"],
                                    "risk_classes": ["read_only"],
                                    "side_effects": ["read_only"],
                                },
                            }
                        ]
                    ),
                ),
            )
    return tenant_id


def publish_evidence_lineage_allow_policy(database_url: str, *, tenant_slug: str = "local-dev") -> UUID:
    """Publish the narrow read-only allow-list for the R0-promoted evidence-lineage plugin.

    Authorizes exactly one deterministic capability:
      - ``audit.evidence.lineage``  read_only, budgeted released-graph query
    The subprocess only reads one immutable released-graph artifact and returns
    an evidence-lineage query result; it never writes a Finding, never mutates
    the released graph and never connects to infrastructure.  This rule never
    enables AUTO mode, arbitrary execution, network access or file writes.
    """

    with psycopg2.connect(database_url) as connection:
        with connection.cursor() as cur:
            cur.execute("SELECT id FROM iam.tenants WHERE slug=%s", (tenant_slug,))
            row = cur.fetchone()
            if row is None:
                raise ValueError(f"tenant not found: {tenant_slug}")
            tenant_id = UUID(str(row[0]))
            cur.execute("SELECT set_config('app.tenant_id',%s,false)", (str(tenant_id),))
            cur.execute(
                """
                INSERT INTO policy.policy_sets(tenant_id,name,version,status,rules)
                VALUES(%s,%s,1,'active',%s)
                ON CONFLICT(tenant_id,name,version)
                DO UPDATE SET status='active', rules=EXCLUDED.rules
                """,
                (
                    tenant_id,
                    _LINEAGE_POLICY_NAME,
                    Json(
                        [
                            {
                                "rule_id": _LINEAGE_RULE_ID,
                                "effect": "allow",
                                "match": {
                                    "capabilities": ["audit.evidence.lineage"],
                                    "risk_classes": ["read_only"],
                                    "side_effects": ["read_only"],
                                },
                            }
                        ]
                    ),
                ),
            )
    return tenant_id


def publish_simulated_backtest_allow_policy(database_url: str, *, tenant_slug: str = "local-dev") -> UUID:
    """Publish the narrow read-only allow-list for the R0-promoted simulated-backtest plugin.

    Authorizes exactly one deterministic capability:
      - ``quant.backtest.simulate``  read_only, frozen-snapshot simulated report
    The subprocess only reads one immutable market-snapshot CSV artifact and
    returns a simulated-only backtest report; it never generates an order,
    never contacts a broker and never writes a dataset.  This rule never
    enables AUTO mode, arbitrary execution, network access or file writes.
    """

    with psycopg2.connect(database_url) as connection:
        with connection.cursor() as cur:
            cur.execute("SELECT id FROM iam.tenants WHERE slug=%s", (tenant_slug,))
            row = cur.fetchone()
            if row is None:
                raise ValueError(f"tenant not found: {tenant_slug}")
            tenant_id = UUID(str(row[0]))
            cur.execute("SELECT set_config('app.tenant_id',%s,false)", (str(tenant_id),))
            cur.execute(
                """
                INSERT INTO policy.policy_sets(tenant_id,name,version,status,rules)
                VALUES(%s,%s,1,'active',%s)
                ON CONFLICT(tenant_id,name,version)
                DO UPDATE SET status='active', rules=EXCLUDED.rules
                """,
                (
                    tenant_id,
                    _BACKTEST_POLICY_NAME,
                    Json(
                        [
                            {
                                "rule_id": _BACKTEST_RULE_ID,
                                "effect": "allow",
                                "match": {
                                    "capabilities": ["quant.backtest.simulate"],
                                    "risk_classes": ["read_only"],
                                    "side_effects": ["read_only"],
                                },
                            }
                        ]
                    ),
                ),
            )
    return tenant_id


def publish_workpaper_export_allow_policy(database_url: str, *, tenant_slug: str = "local-dev") -> UUID:
    """Publish the narrow read-only allow-list for the Phase 10 R3 workpaper-export plugin.

    Authorizes exactly one deterministic capability:
      - ``audit.workpaper.export``  read_only, approved-finding workpaper draft
    The subprocess only reads one immutable finding-set artifact and returns a
    traceable workpaper draft; it never overwrites a historical report and never
    persists anything itself (persistence stays behind a governed identity and
    human approval).  This rule never enables AUTO mode, arbitrary execution,
    network access or file writes.
    """

    with psycopg2.connect(database_url) as connection:
        with connection.cursor() as cur:
            cur.execute("SELECT id FROM iam.tenants WHERE slug=%s", (tenant_slug,))
            row = cur.fetchone()
            if row is None:
                raise ValueError(f"tenant not found: {tenant_slug}")
            tenant_id = UUID(str(row[0]))
            cur.execute("SELECT set_config('app.tenant_id',%s,false)", (str(tenant_id),))
            cur.execute(
                """
                INSERT INTO policy.policy_sets(tenant_id,name,version,status,rules)
                VALUES(%s,%s,1,'active',%s)
                ON CONFLICT(tenant_id,name,version)
                DO UPDATE SET status='active', rules=EXCLUDED.rules
                """,
                (
                    tenant_id,
                    _WORKPAPER_POLICY_NAME,
                    Json(
                        [
                            {
                                "rule_id": _WORKPAPER_RULE_ID,
                                "effect": "allow",
                                "match": {
                                    "capabilities": ["audit.workpaper.export"],
                                    "risk_classes": ["read_only"],
                                    "side_effects": ["read_only"],
                                },
                            }
                        ]
                    ),
                ),
            )
    return tenant_id


def publish_report_draft_allow_policy(database_url: str, *, tenant_slug: str = "local-dev") -> UUID:
    """Publish the narrow read-only allow-list for the Phase 10 R3 report-draft plugin.

    Authorizes exactly one deterministic capability:
      - ``audit.report.draft``  read_only, approved-finding report draft
    The subprocess only reads one immutable finding-set artifact and returns a
    traceable report draft; the report is never issued by the plugin (human
    issuance required), never overwrites a historical report and never persists
    anything itself (persistence stays behind a governed identity and human
    approval).  This rule never enables AUTO mode, arbitrary execution, network
    access or file writes.
    """

    with psycopg2.connect(database_url) as connection:
        with connection.cursor() as cur:
            cur.execute("SELECT id FROM iam.tenants WHERE slug=%s", (tenant_slug,))
            row = cur.fetchone()
            if row is None:
                raise ValueError(f"tenant not found: {tenant_slug}")
            tenant_id = UUID(str(row[0]))
            cur.execute("SELECT set_config('app.tenant_id',%s,false)", (str(tenant_id),))
            cur.execute(
                """
                INSERT INTO policy.policy_sets(tenant_id,name,version,status,rules)
                VALUES(%s,%s,1,'active',%s)
                ON CONFLICT(tenant_id,name,version)
                DO UPDATE SET status='active', rules=EXCLUDED.rules
                """,
                (
                    tenant_id,
                    _REPORT_DRAFT_POLICY_NAME,
                    Json(
                        [
                            {
                                "rule_id": _REPORT_DRAFT_RULE_ID,
                                "effect": "allow",
                                "match": {
                                    "capabilities": ["audit.report.draft"],
                                    "risk_classes": ["read_only"],
                                    "side_effects": ["read_only"],
                                },
                            }
                        ]
                    ),
                ),
            )
    return tenant_id


def publish_ticket_draft_allow_policy(database_url: str, *, tenant_slug: str = "local-dev") -> UUID:
    """Publish the narrow read-only allow-list for the Phase 10 R3 ticket-draft plugin.

    Authorizes exactly one deterministic capability:
      - ``aiops.ticket.draft``  read_only, approved-incident ticket draft
    The subprocess only reads one immutable incident-proposal artifact and returns
    a traceable external ticket draft; the ticket is never sent by the plugin
    (no_auto_send, target-system whitelist and human approval required), never
    overwrites a historical ticket and never persists anything itself.  This rule
    never enables AUTO mode, arbitrary execution, network access or file writes.
    """

    with psycopg2.connect(database_url) as connection:
        with connection.cursor() as cur:
            cur.execute("SELECT id FROM iam.tenants WHERE slug=%s", (tenant_slug,))
            row = cur.fetchone()
            if row is None:
                raise ValueError(f"tenant not found: {tenant_slug}")
            tenant_id = UUID(str(row[0]))
            cur.execute("SELECT set_config('app.tenant_id',%s,false)", (str(tenant_id),))
            cur.execute(
                """
                INSERT INTO policy.policy_sets(tenant_id,name,version,status,rules)
                VALUES(%s,%s,1,'active',%s)
                ON CONFLICT(tenant_id,name,version)
                DO UPDATE SET status='active', rules=EXCLUDED.rules
                """,
                (
                    tenant_id,
                    _TICKET_DRAFT_POLICY_NAME,
                    Json(
                        [
                            {
                                "rule_id": _TICKET_DRAFT_RULE_ID,
                                "effect": "allow",
                                "match": {
                                    "capabilities": ["aiops.ticket.draft"],
                                    "risk_classes": ["read_only"],
                                    "side_effects": ["read_only"],
                                },
                            }
                        ]
                    ),
                ),
            )
    return tenant_id


def publish_retention_recommendation_allow_policy(database_url: str, *, tenant_slug: str = "local-dev") -> UUID:
    """Publish the narrow read-only allow-list for the Phase 10 R3 retention-recommendation plugin.

    Authorizes exactly one deterministic capability:
      - ``knowledge.retention.recommend``  read_only, retention recommendation
    The subprocess only reads one immutable document-content artifact plus caller
    retention metadata and returns a traceable retain/archive/delete_candidate
    recommendation.  The plugin never deletes evidence (no_delete,
    evidence_immutable, human approval required) and never persists anything
    itself.  This rule never enables AUTO mode, arbitrary execution, network
    access or file writes.
    """

    with psycopg2.connect(database_url) as connection:
        with connection.cursor() as cur:
            cur.execute("SELECT id FROM iam.tenants WHERE slug=%s", (tenant_slug,))
            row = cur.fetchone()
            if row is None:
                raise ValueError(f"tenant not found: {tenant_slug}")
            tenant_id = UUID(str(row[0]))
            cur.execute("SELECT set_config('app.tenant_id',%s,false)", (str(tenant_id),))
            cur.execute(
                """
                INSERT INTO policy.policy_sets(tenant_id,name,version,status,rules)
                VALUES(%s,%s,1,'active',%s)
                ON CONFLICT(tenant_id,name,version)
                DO UPDATE SET status='active', rules=EXCLUDED.rules
                """,
                (
                    tenant_id,
                    _RETENTION_POLICY_NAME,
                    Json(
                        [
                            {
                                "rule_id": _RETENTION_RULE_ID,
                                "effect": "allow",
                                "match": {
                                    "capabilities": ["knowledge.retention.recommend"],
                                    "risk_classes": ["read_only"],
                                    "side_effects": ["read_only"],
                                },
                            }
                        ]
                    ),
                ),
            )
    return tenant_id


def publish_postmortem_draft_allow_policy(database_url: str, *, tenant_slug: str = "local-dev") -> UUID:
    """Publish the narrow read-only allow-list for the Phase 10 R3 postmortem-draft plugin.

    Authorizes exactly one deterministic capability:
      - ``aiops.postmortem.draft``  read_only, postmortem draft
    The subprocess only reads one immutable incident-proposal artifact and an
    optional immutable recovery-verification ledger and returns a traceable
    postmortem draft; the draft is never published by the plugin
    (no_auto_publish, human approval required), never overwrites a historical
    postmortem and never persists anything itself.  This rule never enables AUTO
    mode, arbitrary execution, network access or file writes.
    """

    with psycopg2.connect(database_url) as connection:
        with connection.cursor() as cur:
            cur.execute("SELECT id FROM iam.tenants WHERE slug=%s", (tenant_slug,))
            row = cur.fetchone()
            if row is None:
                raise ValueError(f"tenant not found: {tenant_slug}")
            tenant_id = UUID(str(row[0]))
            cur.execute("SELECT set_config('app.tenant_id',%s,false)", (str(tenant_id),))
            cur.execute(
                """
                INSERT INTO policy.policy_sets(tenant_id,name,version,status,rules)
                VALUES(%s,%s,1,'active',%s)
                ON CONFLICT(tenant_id,name,version)
                DO UPDATE SET status='active', rules=EXCLUDED.rules
                """,
                (
                    tenant_id,
                    _POSTMORTEM_DRAFT_POLICY_NAME,
                    Json(
                        [
                            {
                                "rule_id": _POSTMORTEM_DRAFT_RULE_ID,
                                "effect": "allow",
                                "match": {
                                    "capabilities": ["aiops.postmortem.draft"],
                                    "risk_classes": ["read_only"],
                                    "side_effects": ["read_only"],
                                },
                            }
                        ]
                    ),
                ),
            )
    return tenant_id


def publish_research_note_draft_allow_policy(database_url: str, *, tenant_slug: str = "local-dev") -> UUID:
    """Publish the narrow read-only allow-list for the Phase 10 R3 research-note-draft plugin.

    Authorizes exactly one deterministic capability:
      - ``quant.research-note.draft``  read_only, research conclusion draft
    The subprocess only reads one immutable experiment-evaluation artifact and
    returns a traceable research-note draft; the draft is never published by the
    plugin (no_auto_publish, human approval required), generates no orders,
    never updates production models and never persists anything itself.  This rule
    never enables AUTO mode, arbitrary execution, network access or file writes.
    """

    with psycopg2.connect(database_url) as connection:
        with connection.cursor() as cur:
            cur.execute("SELECT id FROM iam.tenants WHERE slug=%s", (tenant_slug,))
            row = cur.fetchone()
            if row is None:
                raise ValueError(f"tenant not found: {tenant_slug}")
            tenant_id = UUID(str(row[0]))
            cur.execute("SELECT set_config('app.tenant_id',%s,false)", (str(tenant_id),))
            cur.execute(
                """
                INSERT INTO policy.policy_sets(tenant_id,name,version,status,rules)
                VALUES(%s,%s,1,'active',%s)
                ON CONFLICT(tenant_id,name,version)
                DO UPDATE SET status='active', rules=EXCLUDED.rules
                """,
                (
                    tenant_id,
                    _RESEARCH_NOTE_DRAFT_POLICY_NAME,
                    Json(
                        [
                            {
                                "rule_id": _RESEARCH_NOTE_DRAFT_RULE_ID,
                                "effect": "allow",
                                "match": {
                                    "capabilities": ["quant.research-note.draft"],
                                    "risk_classes": ["read_only"],
                                    "side_effects": ["read_only"],
                                },
                            }
                        ]
                    ),
                ),
            )
    return tenant_id


def main() -> int:
    parser = argparse.ArgumentParser(description="Register the verified Phase 1 local read-only plugin")
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL"), required=os.getenv("DATABASE_URL") is None)
    parser.add_argument("--tenant-slug", default="local-dev")
    parser.add_argument("--enable-read-only-policy", action="store_true")
    parser.add_argument("--enable-rich-media-policy", action="store_true")
    parser.add_argument("--enable-graph-extraction-policy", action="store_true")
    parser.add_argument("--enable-graph-governance-policy", action="store_true")
    parser.add_argument("--enable-graph-merge-arbitration-policy", action="store_true")
    parser.add_argument("--enable-audit-evidence-chain-policy", action="store_true")
    parser.add_argument("--enable-quant-evidence-chain-policy", action="store_true")
    parser.add_argument("--enable-aiops-governance-policy", action="store_true")
    parser.add_argument("--enable-ledger-quality-policy", action="store_true")
    parser.add_argument("--enable-r1-plugins-policy", action="store_true")
    parser.add_argument("--enable-graph-planning-policy", action="store_true")
    parser.add_argument("--enable-r2-graph-proposal-policy", action="store_true")
    parser.add_argument("--enable-r2-investigation-plan-policy", action="store_true")
    parser.add_argument("--enable-r2-finding-draft-policy", action="store_true")
    parser.add_argument("--enable-r2-experiment-evaluator-policy", action="store_true")
    parser.add_argument("--enable-r2-playbook-proposer-policy", action="store_true")
    parser.add_argument("--enable-r2-recovery-verifier-policy", action="store_true")
    parser.add_argument("--enable-alert-triage-policy", action="store_true")
    parser.add_argument("--enable-evidence-lineage-policy", action="store_true")
    parser.add_argument("--enable-simulated-backtest-policy", action="store_true")
    parser.add_argument("--enable-workpaper-export-policy", action="store_true")
    parser.add_argument("--enable-report-draft-policy", action="store_true")
    parser.add_argument("--enable-ticket-draft-policy", action="store_true")
    parser.add_argument("--enable-retention-policy", action="store_true")
    parser.add_argument("--enable-postmortem-draft-policy", action="store_true")
    parser.add_argument("--enable-research-note-draft-policy", action="store_true")
    args = parser.parse_args()
    version_ids = register_verified_builtin(args.database_url, tenant_slug=args.tenant_slug)
    for plugin_id, version_id in version_ids.items():
        print(f"registered verified plugin {plugin_id}; version_id={version_id}")
    if args.enable_read_only_policy:
        tenant_id = publish_read_only_allow_policy(args.database_url, tenant_slug=args.tenant_slug)
        print(f"published narrow read-only policy for tenant_id={tenant_id}")
    if args.enable_rich_media_policy:
        tenant_id = publish_rich_media_allow_policy(args.database_url, tenant_slug=args.tenant_slug)
        print(f"published rich-media extraction policy for tenant_id={tenant_id}")
    if args.enable_graph_extraction_policy:
        tenant_id = publish_graph_extraction_allow_policy(args.database_url, tenant_slug=args.tenant_slug)
        print(f"published graph-extraction policy for tenant_id={tenant_id}")
    if args.enable_graph_governance_policy:
        tenant_id = publish_graph_governance_allow_policy(args.database_url, tenant_slug=args.tenant_slug)
        print(f"published graph-governance policy for tenant_id={tenant_id}")
    if args.enable_graph_merge_arbitration_policy:
        tenant_id = publish_graph_merge_arbitration_allow_policy(args.database_url, tenant_slug=args.tenant_slug)
        print(f"published graph merge/arbitration policy for tenant_id={tenant_id}")
    if args.enable_audit_evidence_chain_policy:
        tenant_id = publish_audit_evidence_chain_allow_policy(args.database_url, tenant_slug=args.tenant_slug)
        print(f"published audit evidence-chain policy for tenant_id={tenant_id}")
    if args.enable_quant_evidence_chain_policy:
        tenant_id = publish_quant_evidence_chain_allow_policy(args.database_url, tenant_slug=args.tenant_slug)
        print(f"published quant evidence-chain policy for tenant_id={tenant_id}")
    if args.enable_aiops_governance_policy:
        tenant_id = publish_aiops_governance_allow_policy(args.database_url, tenant_slug=args.tenant_slug)
        print(f"published local AIOps governance policy for tenant_id={tenant_id}")
    if args.enable_ledger_quality_policy:
        tenant_id = publish_ledger_quality_allow_policy(args.database_url, tenant_slug=args.tenant_slug)
        print(f"published ledger-quality policy for tenant_id={tenant_id}")
    if args.enable_r1_plugins_policy:
        tenant_id = publish_r1_plugins_allow_policy(args.database_url, tenant_slug=args.tenant_slug)
        print(f"published R1 readonly plugin policy for tenant_id={tenant_id}")
    if args.enable_graph_planning_policy:
        tenant_id = publish_topology_graph_planning_allow_policy(args.database_url, tenant_slug=args.tenant_slug)
        print(f"published topology graph-planning policy for tenant_id={tenant_id}")
    if args.enable_r2_graph_proposal_policy:
        tenant_id = publish_graph_proposal_allow_policy(args.database_url, tenant_slug=args.tenant_slug)
        print(f"published R2 graph-proposal policy for tenant_id={tenant_id}")
    if args.enable_r2_investigation_plan_policy:
        tenant_id = publish_investigation_plan_allow_policy(args.database_url, tenant_slug=args.tenant_slug)
        print(f"published R2 investigation-plan policy for tenant_id={tenant_id}")
    if args.enable_r2_finding_draft_policy:
        tenant_id = publish_finding_draft_allow_policy(args.database_url, tenant_slug=args.tenant_slug)
        print(f"published R2 finding-draft policy for tenant_id={tenant_id}")
    if args.enable_r2_experiment_evaluator_policy:
        tenant_id = publish_experiment_evaluator_allow_policy(args.database_url, tenant_slug=args.tenant_slug)
        print(f"published R2 experiment-evaluator policy for tenant_id={tenant_id}")
    if args.enable_r2_playbook_proposer_policy:
        tenant_id = publish_playbook_proposer_allow_policy(args.database_url, tenant_slug=args.tenant_slug)
        print(f"published R2 playbook-proposer policy for tenant_id={tenant_id}")
    if args.enable_r2_recovery_verifier_policy:
        tenant_id = publish_recovery_verifier_allow_policy(args.database_url, tenant_slug=args.tenant_slug)
        print(f"published R2 recovery-verifier policy for tenant_id={tenant_id}")
    if args.enable_alert_triage_policy:
        tenant_id = publish_alert_triage_allow_policy(args.database_url, tenant_slug=args.tenant_slug)
        print(f"published R0 alert-triage policy for tenant_id={tenant_id}")
    if args.enable_evidence_lineage_policy:
        tenant_id = publish_evidence_lineage_allow_policy(args.database_url, tenant_slug=args.tenant_slug)
        print(f"published R0 evidence-lineage policy for tenant_id={tenant_id}")
    if args.enable_simulated_backtest_policy:
        tenant_id = publish_simulated_backtest_allow_policy(args.database_url, tenant_slug=args.tenant_slug)
        print(f"published R0 simulated-backtest policy for tenant_id={tenant_id}")
    if args.enable_workpaper_export_policy:
        tenant_id = publish_workpaper_export_allow_policy(args.database_url, tenant_slug=args.tenant_slug)
        print(f"published R3 workpaper-export policy for tenant_id={tenant_id}")
    if args.enable_report_draft_policy:
        tenant_id = publish_report_draft_allow_policy(args.database_url, tenant_slug=args.tenant_slug)
        print(f"published R3 report-draft policy for tenant_id={tenant_id}")
    if args.enable_ticket_draft_policy:
        tenant_id = publish_ticket_draft_allow_policy(args.database_url, tenant_slug=args.tenant_slug)
        print(f"published R3 ticket-draft policy for tenant_id={tenant_id}")
    if args.enable_retention_policy:
        tenant_id = publish_retention_recommendation_allow_policy(args.database_url, tenant_slug=args.tenant_slug)
        print(f"published R3 retention-recommendation policy for tenant_id={tenant_id}")
    if args.enable_postmortem_draft_policy:
        tenant_id = publish_postmortem_draft_allow_policy(args.database_url, tenant_slug=args.tenant_slug)
        print(f"published R3 postmortem-draft policy for tenant_id={tenant_id}")
    if args.enable_research_note_draft_policy:
        tenant_id = publish_research_note_draft_allow_policy(args.database_url, tenant_slug=args.tenant_slug)
        print(f"published R3 research-note-draft policy for tenant_id={tenant_id}")
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through the PowerShell operator script
    raise SystemExit(main())
