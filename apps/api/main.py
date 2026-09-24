"""Minimal Phase 1 control-plane API.

This module intentionally does not connect to PostgreSQL or read private domain
tables.  The routes establish the stable HTTP envelope that later repositories
and policy services will implement.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, Any, AsyncIterator, Literal, cast
from urllib.parse import unquote, urlparse
from urllib.request import url2pathname
from uuid import UUID, uuid4

import psycopg2
from fastapi import FastAPI, File, Form, Header, HTTPException, Query, Request, UploadFile
from psycopg2.extras import Json
from pydantic import BaseModel, ConfigDict, Field, model_validator
from starlette.responses import FileResponse, JSONResponse, Response, StreamingResponse
from starlette.staticfiles import StaticFiles

from packages.ai import AIClientError, AIConfigurationError
from packages.ai import config as ai_config
from packages.ai import env_store as ai_env_store
from packages.ai import gateway as ai_gateway
from packages.ai_planner import capability_catalog_from_db
from packages.ai_planner import chat as ai_chat_module
from packages.ai_planner import experience_prior as ai_experience_prior_module
from packages.ai_planner import planner as ai_planner_module
from packages.ai_planner import workbench as ai_workbench_module
from packages.ai_planner.catalog import PORT_CONTRACTS
from packages.ai_planner.nebula_graph import build_nebula_graph
from packages.aiops.service import AIOpsGovernanceService
from packages.audit.pipeline import AuditPipeline
from packages.cases.matrix import case_matrix
from packages.catalog.connectivity_view import connectivity_report
from packages.catalog.lifecycle import plugin_lifecycle, plugin_version_history
from packages.catalog.rules_registry import rules_registry
from packages.control.scheduler import Scheduler
from packages.experience.evolution import evolution_status
from packages.graph.graph_planning import CapabilityGraphAdapter
from packages.graph.service import GraphBudget, GraphService
from packages.knowledge.graph_extraction import GraphExtractionService, extract_text
from packages.knowledge.ingest import default_drop_root, ingest_directory
from packages.knowledge.library import KnowledgeLibraryService
from packages.knowledge.lifecycle import KnowledgeLifecycleService
from packages.knowledge.local_extractors import local_adapter_catalog
from packages.knowledge.retrieval import KnowledgeRetrievalService
from packages.knowledge.rich_media import RichMediaParseError
from packages.knowledge.rich_media_service import (
    RichMediaQueueConflict,
    enqueue_rich_media_extraction,
    list_pending_rich_media,
)
from packages.knowledge.upload_idempotency import (
    KnowledgeUploadIdempotencyStore,
    UploadIdempotencyConflict,
    UploadInProgressError,
)
from packages.library.index import library_index
from packages.observability import (
    attach_spool_logging,
    configure_rotating_file_logging,
    load_code_map,
    trace_context,
)
from packages.observability.code_map import build_code_map
from packages.observability.diagnostics import failure_diagnostics
from packages.observability.log_spool import SegmentedSpool
from packages.observability.run_index import run_index as build_run_index
from packages.observability.run_index import verify_bundle
from packages.plugin_runtime.runner import (
    ArtifactInput,
    IsolatedPluginRuntime,
    PluginInvocation,
    PluginPolicyDenied,
    PluginRuntimeError,
    load_verified_binding,
    verified_builtin_ids,
)
from packages.plugin_topology.graph_planning import GraphPlanningService
from packages.plugin_topology.remediation import RemediationConflictError, RemediationError
from packages.plugin_topology.runs import RunConflictError
from packages.plugin_topology.service import TopologyService
from packages.plugin_topology.verification import RunVerificationError, VerificationConflictError
from packages.policy.engine import PolicyEngine, PolicyResult
from packages.quant.service import QuantService
from packages.search.fusion import search_all

logger = logging.getLogger("audit.api")
EXPECTED_MIGRATION_HEAD = "0068_connectivity_read_policy"

# The project ``.env`` is the durable home of the AI settings the desktop
# settings page writes.  Load it as *defaults* before any Settings is built.
# ``override=False`` keeps the precedence the PowerShell launchers rely on: an
# explicitly exported process variable (or a user-level variable such as
# OPENAI_COMPAT_API_KEY) always beats the file.
ai_env_store.load_env_file()


@dataclass(frozen=True, slots=True)
class Settings:
    """Process settings sourced from environment variables only.

    Defaults resolve at instantiation time (default_factory, not class-body
    evaluation) so callers that set DATABASE_URL right before create_app()
    are honoured regardless of module import order.
    """

    database_url: str = field(
        default_factory=lambda: os.getenv(
            # Must match `.env.example`, `scripts/start-brain.ps1` and the CI
            # workflow, which all use the `admin` password created by
            # `scripts/bootstrap.ps1`.  This fallback previously carried
            # `audit_app` and was the only place in the repository with that
            # value: running the API without `start-brain.ps1` (a bare
            # `uvicorn apps.api.main:app`, an IDE run configuration, or a
            # `.env` without DATABASE_URL) authenticated against a
            # non-existent password, so every database-backed route returned
            # 500 while the process itself looked healthy.
            "DATABASE_URL", "postgresql://audit_app:admin@localhost:5432/audit_network"
        )
    )
    app_env: str = field(default_factory=lambda: os.getenv("APP_ENV", "development"))
    api_version: str = field(default_factory=lambda: os.getenv("API_VERSION", "v1"))
    ui_shell_version: str = field(default_factory=lambda: os.getenv("UI_SHELL_VERSION", "0.1.0"))
    policy_auto_enabled: bool = field(
        default_factory=lambda: os.getenv("POLICY_AUTO_ENABLED", "false").lower() == "true"
    )
    # Read roots for plugin data access.  The default is deliberately empty:
    # it used to be the author's own `G:\数据`, which meant a fresh checkout
    # silently advertised a drive that does not exist on any other machine.
    # Operators opt in explicitly via AUDIT_PLUGIN_READ_ROOTS (`;`-separated).
    plugin_read_roots: tuple[Path, ...] = field(
        default_factory=lambda: tuple(
            Path(item).resolve()
            for item in os.getenv("AUDIT_PLUGIN_READ_ROOTS", "").split(os.pathsep)
            if item.strip()
        )
    )


class HealthResponse(BaseModel):
    """Liveness response.  Database readiness is intentionally separate."""

    model_config = ConfigDict(extra="forbid")

    status: str = "ok"
    service: str = "audit-network-api"
    version: str
    environment: str
    database_configured: bool
    trace_id: UUID


class HealthReadyResponse(BaseModel):
    """Readiness probe consumed by the watchdog and the desktop.

    Returns only operational facts (no credentials, no tenant rows): the API is
    ready when the database answers, the migration head matches, at least one
    worker has a fresh heartbeat, and no subsystem metric is critical.
    """

    model_config = ConfigDict(extra="forbid")

    status: Literal["ok", "degraded"]
    database: Literal["ok", "error"]
    migration_head: str
    migration_expected: str
    worker_heartbeats: int
    worker_stale: int
    outbox_backlog: int
    stuck_tasks: int
    checks: list[str]
    trace_id: UUID


class CodeMapEntry(BaseModel):
    """One API route located in the source tree, with the tables it touches."""

    model_config = ConfigDict(extra="forbid")

    method: str
    path: str
    file: str
    line: int
    tables: list[str]
    code_sha256: str


class CodeMapResponse(BaseModel):
    """Code-layer drill-down index: file -> line -> endpoint -> tables."""

    model_config = ConfigDict(extra="forbid")

    generated_at: str
    code_sha256: str
    api_file: str
    endpoint_count: int
    endpoints: list[CodeMapEntry]
    tenant_id: UUID
    trace_id: UUID


class TraceDataRow(BaseModel):
    """One data row associated with a trace id (L7 last hop)."""

    model_config = ConfigDict(extra="forbid")

    kind: str
    table: str
    id: str
    detail: str
    status: str | None = None


class LogLocation(BaseModel):
    """A concrete file + grep pattern for the operator to open."""

    model_config = ConfigDict(extra="forbid")

    file: str
    query: str


class LogEvent(BaseModel):
    """One structured log event from the CW2 spool (归档索引查询结果)."""

    model_config = ConfigDict(extra="forbid")

    event_id: str
    producer_id: str
    producer_epoch: int
    seq: int
    timestamp: str
    level: str
    source: str
    message: str
    stream: str
    trace_id: str | None = None
    run_id: str | None = None
    node_id: str | None = None


class LogQueryResult(BaseModel):
    """CW2: real event/archive index query result for one trace id.

    ``completeness`` distinguishes "no records" from "query unavailable":
    a sealed producer is complete; a missing seal is partial with
    ``unknown_tail``; a spool read failure is unavailable (方案 8.5).
    """

    model_config = ConfigDict(extra="forbid")

    events: list[LogEvent]
    completeness: Literal["complete", "partial", "unavailable"] = "complete"
    persisted_seq: int | None = None
    unknown_tail: bool = False
    archive_state: str = "archived"
    next_cursor: int | None = None
    missing_sources: list[str] = []


class TraceLocateResponse(BaseModel):
    """Given a trace_id: the data rows and log locations it links (L7)."""

    model_config = ConfigDict(extra="forbid")

    trace_id: str
    data_rows: list[TraceDataRow]
    log_locations: list[LogLocation]
    request_trace_id: UUID
    completeness: Literal["complete", "partial", "unavailable"] = "complete"
    log_query: LogQueryResult | None = None


class RendererDescriptor(BaseModel):
    """Renderer capability exposed to framework-independent GUI clients."""

    model_config = ConfigDict(extra="forbid")

    id: str
    version: str
    capabilities: list[str]


class UIBootstrapResponse(BaseModel):
    """Stable GUI shell contract for plugin clients."""

    model_config = ConfigDict(extra="forbid")

    api_version: str
    shell_version: str
    supported_ui_schema_versions: list[str]
    slots: list[str]
    renderers: list[RendererDescriptor]
    locales: list[str]
    design_tokens: dict[str, str]
    features: dict[str, bool]
    trace_id: UUID


class UIContributionSummary(BaseModel):
    """Public summary of one enabled UI contribution.

    The full declaration remains versioned by the plugin contract; this route
    returns summaries so a broken plugin cannot inject arbitrary UI code.
    """

    model_config = ConfigDict(extra="forbid")

    contribution_id: str = Field(pattern=r"^[a-z][a-z0-9._-]*$")
    plugin_id: str = Field(pattern=r"^[a-z][a-z0-9._-]*$")
    plugin_version: str
    schema_version: str
    enabled: bool
    navigation_count: int = Field(ge=0)
    view_count: int = Field(ge=0)
    action_count: int = Field(ge=0)


class UIContributionsResponse(BaseModel):
    """Paginated contribution list contract."""

    model_config = ConfigDict(extra="forbid")

    workspace_id: UUID | None
    items: list[UIContributionSummary]
    next_cursor: str | None
    trace_id: UUID


class UITenantContextResponse(BaseModel):
    """Development shell tenant lookup.

    The browser supplies a slug in ``X-Tenant-Slug``; API calls made by the
    shell continue to use the resolved UUID in ``X-Tenant-Id``.  This avoids
    baking an environment-specific UUID into the static GUI.
    """

    model_config = ConfigDict(extra="forbid")

    tenant_id: UUID
    tenant_slug: str
    trace_id: UUID


class OperationsSummaryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    counts: dict[str, int]
    recent_decisions: list[dict[str, Any]]
    trace_id: UUID


class AgentRunDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    role_key: str
    model_key: str
    status: str
    token_input: int
    token_output: int
    cost: float
    started_at: datetime | None
    finished_at: datetime | None
    error_detail: str | None


class TaskRunDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    node_key: str | None
    capability: str
    status: str
    attempt: int
    max_attempts: int
    started_at: datetime | None
    finished_at: datetime | None
    error_detail: str | None
    trace_id: str | None
    agents: list[AgentRunDetail]


class WorkflowRunDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    workflow_key: str
    workflow_version: str
    status: str
    started_at: datetime | None
    finished_at: datetime | None
    trace_id: str | None
    tasks: list[TaskRunDetail]


class MissionRunDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    title: str
    domain: str
    autonomy_mode: str
    status: str
    created_at: datetime
    workflows: list[WorkflowRunDetail]


class OperationsDetailResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    missions: list[MissionRunDetail]
    trace_id: UUID


class PolicyEvaluateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    capability: str = Field(min_length=1, max_length=255)
    arguments: dict[str, Any] = Field(default_factory=dict)
    risk_class: Literal["read_only", "low", "medium", "high", "critical"] = "medium"
    side_effects: str | None = None


class PluginArtifactRequest(BaseModel):
    """A caller-owned immutable local artifact reference for a read-only plugin."""

    model_config = ConfigDict(extra="forbid")

    artifact_id: UUID
    tenant_id: UUID
    uri: str = Field(min_length=1, max_length=2048)
    media_type: str = Field(min_length=1, max_length=255)
    sha256: str = Field(pattern=r"^[A-Fa-f0-9]{64}$")
    size_bytes: int = Field(ge=0, le=50 * 1024 * 1024)
    classification: Literal["public", "internal", "audit_confidential", "restricted"]


class LedgerArtifactRefRequest(BaseModel):
    """Typed ledger-snapshot reference for the audit.ledger-quality plugin."""

    model_config = ConfigDict(extra="forbid")

    artifact: PluginArtifactRequest
    schema_mapping_version: str = Field(pattern=r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
    period: str = Field(pattern=r"^[0-9]{4}-(0[1-9]|1[0-2])$")


class JournalArtifactRefRequest(BaseModel):
    """Typed journal-snapshot reference for the audit.journal-anomaly plugin."""

    model_config = ConfigDict(extra="forbid")

    artifact: PluginArtifactRequest
    schema_mapping_version: str = Field(pattern=r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
    period: str = Field(pattern=r"^[0-9]{4}-(0[1-9]|1[0-2])$")


class DocumentContentRefRequest(BaseModel):
    """Typed document reference for the knowledge.entity-relation-candidate plugin."""

    model_config = ConfigDict(extra="forbid")

    artifact: PluginArtifactRequest
    language: Literal["zh", "en", "auto"] = "auto"


class SnapshotRefRequest(BaseModel):
    """Typed market-snapshot reference for the quant.snapshot-guard plugin."""

    model_config = ConfigDict(extra="forbid")

    artifact: PluginArtifactRequest
    reference_time: str = Field(min_length=1, max_length=64)
    max_freshness_hours: int = Field(default=24, ge=1, le=8760)
    point_in_time: bool = True
    expected_columns: list[str] | None = Field(default=None, max_length=64)


class FactorDatasetRequest(BaseModel):
    """Typed market dataset reference for the quant.factor-compute plugin."""

    model_config = ConfigDict(extra="forbid")

    artifact: PluginArtifactRequest
    reference_time: str = Field(min_length=1, max_length=64)
    factors: list[str] = Field(min_length=1, max_length=8)
    lookback: int = Field(ge=2, le=10000)


class AlertsRefRequest(BaseModel):
    """Typed alert-event reference for the aiops.alert-correlation plugin."""

    model_config = ConfigDict(extra="forbid")

    artifact: PluginArtifactRequest
    window_minutes: int = Field(ge=1, le=10080)
    max_candidates: int = Field(default=50, ge=1, le=1000)
    grouping_keys: list[str] | None = Field(default=None, max_length=4)


class AiPlanningRequest(BaseModel):
    """CW5 AI auto-networking request (plan-only; no execution permission)."""

    model_config = ConfigDict(extra="forbid")

    domain: str = Field(default="audit", pattern=r"^[a-z][a-z0-9_-]{0,31}$")
    goal: str = Field(min_length=4, max_length=2000)
    data_sources: list[list[str]] = Field(default_factory=list, max_length=64)
    budget: dict[str, int] | None = None
    template_keys: list[str] | None = Field(default=None, max_length=16)


class AiPlanningResponse(BaseModel):
    """CW5 outcome: either a compiled draft (needs CW3 gate to run) or a gap report."""

    model_config = ConfigDict(extra="forbid")

    status: str
    plan_key: str
    revisions: int
    draft: dict[str, Any] | None = None
    issues: list[dict[str, Any]] = Field(default_factory=list)


class ChatTurn(BaseModel):
    """One prior chat turn carried by the client (stateless backend)."""

    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=2000)


class CanvasChatRequest(BaseModel):
    """Canvas AI chat: a message (with optional history and an existing draft)
    plans a graph flow — plan-only, never execution permission."""

    model_config = ConfigDict(extra="forbid")

    domain: str = Field(default="audit", pattern=r"^[a-z][a-z0-9_-]{0,31}$")
    message: str = Field(min_length=1, max_length=2000)
    session_id: str | None = Field(default=None, min_length=8, max_length=64)
    history: list[ChatTurn] = Field(default_factory=list, max_length=24)
    base_draft: dict[str, Any] | None = None
    data_sources: list[list[str]] = Field(default_factory=list, max_length=64)
    budget: dict[str, int] | None = None
    template_keys: list[str] | None = Field(default=None, max_length=16)
    idempotency_key: str = Field(min_length=8, max_length=128)


class CanvasChatResponse(BaseModel):
    """Canvas chat outcome: compiled draft (renders on the canvas) or gap report."""

    model_config = ConfigDict(extra="forbid")

    status: str
    plan_key: str
    revisions: int
    draft: dict[str, Any] | None = None
    issues: list[dict[str, Any]] = Field(default_factory=list)
    session_id: str
    reply_text: str
    intent_id: str | None = None
    backend: str


class TriageInputRequest(BaseModel):
    """Typed single alert-event reference for the aiops.alert-triage plugin."""

    model_config = ConfigDict(extra="forbid")

    alert_event: PluginArtifactRequest


class BudgetRequest(BaseModel):
    """Caller-declared read budget for a released-graph lineage query."""

    model_config = ConfigDict(extra="forbid")

    max_nodes: int = Field(ge=1, le=100_000)
    max_edges: int = Field(ge=1, le=500_000)
    max_hops: int = Field(ge=1, le=8)
    timeout_seconds: int = Field(ge=1, le=300)


class ReleasedGraphRefRequest(BaseModel):
    """Typed released-graph reference for the audit.evidence-lineage plugin."""

    model_config = ConfigDict(extra="forbid")

    artifact: PluginArtifactRequest
    space_level: Literal["L0", "L1", "L2", "L3", "L4"]
    release_sha256: str = Field(pattern=r"^[A-Fa-f0-9]{64}$")
    release_time: str | None = Field(default=None, min_length=1, max_length=64)
    budget: BudgetRequest


class LineageInputRequest(BaseModel):
    """Typed released-graph reference wrapper for the audit.evidence-lineage plugin."""

    model_config = ConfigDict(extra="forbid")

    graph_ref: ReleasedGraphRefRequest


class MarketSnapshotRefRequest(BaseModel):
    """Typed market-snapshot reference for the quant.simulated-backtest plugin."""

    model_config = ConfigDict(extra="forbid")

    artifact: PluginArtifactRequest
    reference_time: str = Field(min_length=1, max_length=64)
    max_freshness_hours: int = Field(ge=1, le=8760)
    point_in_time: bool = True
    data_frequency: Literal["minute", "hour", "day"] = "minute"
    expected_columns: list[str] | None = Field(default=None, max_length=64)


class BacktestStrategyRequest(BaseModel):
    """Typed strategy declaration for the quant.simulated-backtest plugin."""

    model_config = ConfigDict(extra="forbid")

    strategy_key: str = Field(min_length=1, max_length=255)
    strategy_version: str = Field(
        pattern=r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
    )
    code_sha256: str = Field(pattern=r"^[A-Fa-f0-9]{64}$")
    parameters: dict[str, Any] = Field(default_factory=dict)


class BacktestInputRequest(BaseModel):
    """Typed market-snapshot + strategy wrapper for the quant.simulated-backtest plugin."""

    model_config = ConfigDict(extra="forbid")

    snapshot_ref: MarketSnapshotRefRequest
    strategy: BacktestStrategyRequest
    max_periods: int = Field(default=100_000, ge=1, le=100_000)


class RcaInputRequest(BaseModel):
    """Typed incident + topology reference for the aiops.rca-ranker plugin."""

    model_config = ConfigDict(extra="forbid")

    incident_set: PluginArtifactRequest
    topology_graph: PluginArtifactRequest
    max_candidates: int = Field(default=10, ge=1, le=100)
    max_hops: int = Field(default=2, ge=1, le=3)


class ProposalInputRequest(BaseModel):
    """Typed candidate-set (+ optional snapshot) reference for the graph-proposal-builder plugin."""

    model_config = ConfigDict(extra="forbid")

    candidate_set: PluginArtifactRequest
    graph_snapshot: PluginArtifactRequest | None = None
    space_level: Literal["L0", "L1", "L2", "L3", "L4"] = "L1"
    max_proposals: int = Field(default=1000, ge=1, le=100_000)


class InvestigationInputRequest(BaseModel):
    """Typed anomaly-candidates (+ optional evidence index) reference for the investigation-plan plugin."""

    model_config = ConfigDict(extra="forbid")

    anomaly_candidates: PluginArtifactRequest
    evidence_index: PluginArtifactRequest | None = None
    max_steps: int = Field(default=8, ge=1, le=64)


class FindingInputRequest(BaseModel):
    """Typed anomaly-candidates (+ optional investigation-plan draft) reference for the finding-draft plugin."""

    model_config = ConfigDict(extra="forbid")

    anomaly_candidates: PluginArtifactRequest
    investigation_plan: PluginArtifactRequest | None = None


class ExperimentInputRequest(BaseModel):
    """Typed backtest-report (+ optional baseline) reference for the experiment-evaluator plugin."""

    model_config = ConfigDict(extra="forbid")

    report: PluginArtifactRequest
    baseline: PluginArtifactRequest | None = None


class RemediationInputRequest(BaseModel):
    """Typed rca-candidates (+ scope controls) reference for the playbook-proposer plugin."""

    model_config = ConfigDict(extra="forbid")

    rca_candidates: PluginArtifactRequest
    max_playbooks: int = Field(default=5, ge=1, le=20)
    canary_scope: Literal["single_node", "subset_10pct", "low_traffic"] = "single_node"


class RecoveryInputRequest(BaseModel):
    """Typed baseline/observed series (+ optional proposal) reference for the recovery-verifier plugin."""

    model_config = ConfigDict(extra="forbid")

    baseline: PluginArtifactRequest
    observed: PluginArtifactRequest
    proposal: PluginArtifactRequest | None = None
    availability_target: float = Field(default=0.99, ge=0.0, le=1.0)
    threshold_ratio: float = Field(default=1.5, ge=0.0, le=10.0)
    probes: list[dict[str, object]] = Field(default_factory=list, max_length=64)


class WorkpaperInputRequest(BaseModel):
    """Typed confirmed finding-set reference for the audit.workpaper-export plugin."""

    model_config = ConfigDict(extra="forbid")

    finding_set: PluginArtifactRequest
    max_findings: int = Field(default=500, ge=1, le=500)


class ReportInputRequest(BaseModel):
    """Typed confirmed finding-set + template reference for the audit.report-draft plugin."""

    model_config = ConfigDict(extra="forbid")

    finding_set: PluginArtifactRequest
    template_version: str = Field(
        pattern=r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
    )


class TicketInputRequest(BaseModel):
    """Typed confirmed incident-proposal + target system reference for the aiops.ticket-draft plugin."""

    model_config = ConfigDict(extra="forbid")

    incident_proposal: PluginArtifactRequest
    target_system: str = Field(
        default="jira",
        pattern=r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$",
    )


class RetentionInputRequest(BaseModel):
    """Typed immutable document-content + retention metadata for the knowledge.retention-recommendation plugin."""

    model_config = ConfigDict(extra="forbid")

    document: PluginArtifactRequest
    last_access_days: int = Field(default=365, ge=0, le=10_000)
    ref_count: int = Field(default=0, ge=0, le=10_000)
    classification: str = Field(default="internal", min_length=1, max_length=64)
    archive_after_days: int = Field(default=365, ge=30, le=10_000)
    purge_candidate_after_days: int = Field(default=730, ge=30, le=10_000)
    min_refs_to_retain: int = Field(default=1, ge=0, le=100)


class PostmortemInputRequest(BaseModel):
    """Typed confirmed incident-proposal + optional recovery-verification for the aiops.postmortem-draft plugin."""

    model_config = ConfigDict(extra="forbid")

    incident_proposal: PluginArtifactRequest
    verification: PluginArtifactRequest | None = None


class ResearchNoteInputRequest(BaseModel):
    """Typed confirmed experiment-evaluation artifact for the quant.research-note-draft plugin."""

    model_config = ConfigDict(extra="forbid")

    evaluation: PluginArtifactRequest


class PluginInvokeRequest(BaseModel):
    """Typed request for a verified local read-only plugin.

    Exactly one typed input must be present:
      - ``artifact`` for knowledge.document-ingestion
      - ``ledger`` for audit.ledger-quality
      - ``journal`` for audit.journal-anomaly
      - ``document`` for knowledge.entity-relation-candidate
      - ``snapshot`` for quant.snapshot-guard
      - ``dataset`` for quant.factor-compute
      - ``alerts`` for aiops.alert-correlation
      - ``rca`` for aiops.rca-ranker
      - ``triage`` for aiops.alert-triage
      - ``lineage`` for audit.evidence-lineage
      - ``backtest`` for quant.simulated-backtest
      - ``proposal`` for knowledge.graph-proposal-builder
      - ``investigation`` for audit.investigation-plan
      - ``finding`` for audit.finding-draft
      - ``experiment`` for quant.experiment-evaluator
      - ``remediation`` for aiops.playbook-proposer
      - ``recovery`` for aiops.recovery-verifier
      - ``workpaper`` for audit.workpaper-export
      - ``report`` for audit.report-draft
      - ``ticket`` for aiops.ticket-draft
      - ``retention`` for knowledge.retention-recommendation
      - ``postmortem`` for aiops.postmortem-draft
      - ``research_note`` for quant.research-note-draft
    """

    model_config = ConfigDict(extra="forbid")

    plugin_id: str = Field(pattern=r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)+$")
    capability: str = Field(pattern=r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)+$")
    artifact: PluginArtifactRequest | None = None
    ledger: LedgerArtifactRefRequest | None = None
    journal: JournalArtifactRefRequest | None = None
    document: DocumentContentRefRequest | None = None
    snapshot: SnapshotRefRequest | None = None
    dataset: FactorDatasetRequest | None = None
    alerts: AlertsRefRequest | None = None
    triage: TriageInputRequest | None = None
    lineage: LineageInputRequest | None = None
    backtest: BacktestInputRequest | None = None
    rca: RcaInputRequest | None = None
    proposal: ProposalInputRequest | None = None
    investigation: InvestigationInputRequest | None = None
    finding: FindingInputRequest | None = None
    experiment: ExperimentInputRequest | None = None
    remediation: RemediationInputRequest | None = None
    recovery: RecoveryInputRequest | None = None
    workpaper: WorkpaperInputRequest | None = None
    report: ReportInputRequest | None = None
    ticket: TicketInputRequest | None = None
    retention: RetentionInputRequest | None = None
    postmortem: PostmortemInputRequest | None = None
    research_note: ResearchNoteInputRequest | None = None
    max_characters: int = Field(default=20_000, ge=1, le=250_000)

    @model_validator(mode="after")
    def require_exactly_one_input(self) -> "PluginInvokeRequest":
        present = sum(
            1
            for value in (
                self.artifact,
                self.ledger,
                self.journal,
                self.document,
                self.snapshot,
                self.dataset,
                self.alerts,
                self.rca,
                self.triage,
                self.lineage,
                self.backtest,
                self.proposal,
                self.investigation,
                self.finding,
                self.experiment,
                self.remediation,
                self.recovery,
                self.workpaper,
                self.report,
                self.ticket,
                self.retention,
                self.postmortem,
                self.research_note,
            )
            if value is not None
        )
        if present != 1:
            raise ValueError("exactly one typed plugin input is required")
        return self


class PluginInvokeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plugin_id: str
    plugin_version: str
    capability: str
    policy_decision_id: UUID
    input_sha256: str
    runtime_code_sha256: str
    document: dict[str, Any]
    trace_id: UUID


class VerifiedPluginSummary(BaseModel):
    """Safe desktop-facing state for one hard-coded verified builtin."""

    model_config = ConfigDict(extra="forbid")

    plugin_id: str
    plugin_version: str
    capability: str
    execution_mode: Literal["isolated_subprocess"]
    catalog_registered: bool
    side_effects: Literal["read_only"]
    trace_id: UUID


class VerifiedPluginsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[VerifiedPluginSummary]
    trace_id: UUID


class PluginLifecycleItem(BaseModel):
    """One plugin: what the file declares, what the code permits, what was published."""

    model_config = ConfigDict(extra="forbid")

    plugin_id: str
    name: str
    capability: str
    domain: str | None
    domains: list[str]
    protocol_lifecycle: str
    executable: bool
    conflict: bool
    version: str | None
    version_status: str | None
    descriptor_sha256: str | None
    runtime: str | None
    entrypoint: str | None
    side_effect_class: str | None
    published_at: str | None


class PluginLifecycleSummary(BaseModel):
    """Corpus-wide counts; a filtered view must never shrink these."""

    model_config = ConfigDict(extra="forbid")

    plugins: int
    protocol_verified: int
    protocol_contract_only: int
    executable: int
    conflict: int
    version_registered: int
    descriptor_sha256_present: int
    allow_list_size: int
    allow_list_unmatched: list[str]
    by_domain: dict[str, int]


class PluginLifecycleResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: PluginLifecycleSummary
    items: list[PluginLifecycleItem]
    trace_id: UUID


class RuleItem(BaseModel):
    """One rule-registry entry: a contract, a skill or a report template."""

    model_config = ConfigDict(extra="forbid")

    name: str
    kind: str
    path: str
    modified_at: str
    status: str


class PolicySetItem(BaseModel):
    """One active policy set row from ``policy.policy_sets``; the DB is truth."""

    model_config = ConfigDict(extra="forbid")

    name: str
    version: str
    status: str
    rules_count: int
    created_at: str | None


class RulesRegistrySummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contracts: int
    skills: int
    report_templates: int
    policy_sets: int
    missing_roots: list[str]


class RulesRegistryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: RulesRegistrySummary
    contracts: list[RuleItem]
    policies: list[PolicySetItem]
    report_templates: list[RuleItem]
    skills: list[RuleItem]
    trace_id: UUID


class ConnectivityScopeModel(BaseModel):
    """口径标注：不同口径下的连通性数字**不可比**，所以它必须随结果一起返回。"""

    model_config = ConfigDict(extra="forbid")

    lifecycle: str | None
    include_invokes: bool
    label: str


class ConnectivityMetricsModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    edge_set: str
    plugins: int
    edges: int
    isolated_nodes: int
    no_incoming: int
    no_outgoing: int
    weakly_connected_components: int
    component_sizes: list[int]
    dead_end_outputs: int
    unfillable_inputs: int
    reusable_contracts: int
    total_input_ports: int
    unfillable_input_ports: int
    seeds: int
    pure_seed_nodes: int


class ConnectivityIslandCategory(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: str
    label: str
    count: int


class ConnectivityIsland(BaseModel):
    """一个悬空插件，以及它为什么悬空（每一条都有分类依据，不是"没连上"了事）。"""

    model_config = ConfigDict(extra="forbid")

    plugin_id: str
    role: str
    category: str
    category_zh: str
    reason: str
    detail: str


class ConnectivityReportResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope: ConnectivityScopeModel
    metrics: ConnectivityMetricsModel
    islands_total: int
    islands_present: list[ConnectivityIslandCategory]
    islands: list[ConnectivityIsland]
    trace_id: UUID


class PluginVersionRow(BaseModel):
    """One published version row for a plugin (lineage history)."""

    model_config = ConfigDict(extra="forbid")

    version: str | None
    version_status: str | None
    descriptor_sha256: str | None
    runtime: str | None
    entrypoint: str | None
    side_effect_class: str | None
    published_at: str | None


class PluginVersionsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plugin_id: str
    items: list[PluginVersionRow]
    trace_id: UUID


class RunBundleInfo(BaseModel):
    """The on-disk half of a run: readable metadata, never a verification claim."""

    model_config = ConfigDict(extra="forbid")

    path: str
    size_bytes: int
    modified_at: str
    exported_at: str | None
    members: int
    readable: bool
    verified: bool | None


class RunItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    plan_key: str | None
    trace_id: str | None
    execution_hash: str | None
    attempts: int
    succeeded: int
    failed: int
    plugins: int
    versioned_attempts: int
    started_at: str | None
    finished_at: str | None
    bundle: RunBundleInfo | None
    project_id: str | None
    archive_state: str
    archived_at: str | None


class RunIndexSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    runs_listed: int
    bundles_on_disk: int
    bundles_unreadable: int
    runs_with_bundle: int
    runs_without_bundle: int
    orphan_bundles: int
    linked_to_project: int
    unlinked: int
    missing_roots: list[str]


class RunIndexResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: RunIndexSummary
    items: list[RunItem]
    orphan_bundles: list[str]
    trace_id: UUID


class FailureSample(BaseModel):
    model_config = ConfigDict(extra="forbid")

    attempt_id: str
    run_id: str
    trace_id: str | None
    node_instance_id: str
    plugin_id: str
    plugin_version: str | None
    capability: str
    attempt_seq: int
    status: str
    plan_key: str | None
    occurred_at: str | None
    message: str


class FailureLocate(BaseModel):
    """Read-only pointers to where the evidence lives; nothing re-runs."""

    model_config = ConfigDict(extra="forbid")

    trace_endpoint: str | None
    bundle_endpoint: str | None
    note: str


class FailureGroup(BaseModel):
    model_config = ConfigDict(extra="forbid")

    error_kind: str
    signature: str
    occurrences: int
    recent_occurrences: int
    runs: list[str]
    run_count: int
    node_count: int
    plugins: list[tuple[str, int]]
    capabilities: list[tuple[str, int]]
    first_seen: str | None
    last_seen: str | None
    spans_days: int
    recurrent: bool
    samples: list[FailureSample]
    trace_ids: list[str]
    locate: FailureLocate


class FailureTotals(BaseModel):
    model_config = ConfigDict(extra="forbid")

    attempts: int
    succeeded: int
    failed: int
    failure_rate: float
    runs: int
    runs_with_failure: int


class FailureSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scanned: int
    groups: int
    recurrent_groups: int
    by_error_kind: dict[str, int]
    recent_days: int
    truncated: bool


class FailureDiagnosticsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    totals: FailureTotals
    summary: FailureSummary
    items: list[FailureGroup]
    trace_id: UUID


class BundleVerifyResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    found: bool
    reason: str | None = None
    path: str | None = None
    size_bytes: int | None = None
    verification: dict[str, Any] | None = None
    trace_id: UUID


class EvolutionRing(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ring: int
    name: str
    status: str
    evidence: dict[str, Any]
    detail: str
    checked: str


class EvolutionSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: int
    partial: int
    broken: int
    archive_links: int
    loop_closed: bool


class EvolutionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rings: list[EvolutionRing]
    weakest: str | None
    summary: EvolutionSummary
    trace_id: UUID


class SearchItem(BaseModel):
    """One fused cross-kind hit; ``anchor`` points at the row or file that is truth."""

    model_config = ConfigDict(extra="forbid")

    kind: str
    item_id: str
    title: str
    subtitle: str | None = None
    anchor: dict[str, Any]
    score: float
    matched_by: list[str]


class SearchDegraded(BaseModel):
    """Honest vector availability: ``True`` means results are keyword-only."""

    model_config = ConfigDict(extra="forbid")

    vector: bool


class SearchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[SearchItem]
    degraded: SearchDegraded
    trace_id: UUID


class LibraryItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    title: str
    kind: str
    project: str | None
    size_bytes: int
    modified_at: str
    related_run: str | None
    related_case: str | None


class LibrarySummary(BaseModel):
    """Corpus-wide counts; a filtered view must never shrink these."""

    model_config = ConfigDict(extra="forbid")

    total: int
    by_kind: dict[str, int]
    by_project: dict[str, int]


class LibraryIndexResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: LibrarySummary
    items: list[LibraryItem]
    trace_id: UUID


class CaseStage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    name: str
    count: int


class CaseReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    lines: int
    bytes: int
    modified_at: str
    m7_9_share: float
    v11_ok: bool


class CaseInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case: str
    stages: list[CaseStage]
    file_count: int
    report_count: int
    reports: list[CaseReport]


class CasesResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cases: list[CaseInfo]
    trace_id: UUID


class PolicyDecisionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision_id: UUID
    tenant_id: UUID
    tool_call_id: UUID
    policy_version: str
    decision: Literal["ALLOW", "DENY", "REQUIRE_APPROVAL", "FREEZE"]
    risk_score: float = Field(ge=0, le=100)
    reason: str
    matched_rule_ids: list[UUID] = Field(default_factory=list)
    reviewer_type: Literal["deterministic", "human", "hybrid"] = "deterministic"
    authorization_lease_id: UUID | None = None
    simulation: bool = False
    decided_at: datetime
    trace_id: UUID


class ApprovalDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["approved", "rejected"]
    reason: str = Field(min_length=1, max_length=4000)
    ttl_seconds: int = Field(default=3600, ge=60, le=86400)


class ApprovalSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    tool_call_id: UUID
    capability: str
    argument_hash: str
    risk_class: str
    status: str
    reason: str | None
    expires_at: datetime
    created_at: datetime
    decided_at: datetime | None
    authorization_lease_id: UUID | None = None
    trace_id: UUID


class ApprovalListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[ApprovalSummary]
    trace_id: UUID


class MissionCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=200)
    objective: str = Field(min_length=1, max_length=4000)
    domain: str = Field(min_length=1, max_length=100)
    project_slug: str = "default"
    autonomy_mode: Literal["approval_required", "auto_low_risk", "manual"] = "approval_required"
    requested_by: UUID | None = None


class MissionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    trace_id: UUID


class WorkflowCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(pattern=r"^[a-z][a-z0-9._-]*$")
    name: str = Field(min_length=1, max_length=200)
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    graph: dict[str, Any]
    created_by: UUID | None = None


class WorkflowRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mission_id: UUID
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    input_payload: dict[str, Any] = Field(default_factory=dict)


class WorkflowRunResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    trace_id: UUID


class RunnableTaskResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    node_key: str
    capability: str
    status: str
    attempt: int
    max_attempts: int


class RunnableTasksResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[RunnableTaskResponse]
    trace_id: UUID


class TaskClaimRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role_key: str = Field(min_length=1, max_length=100)
    model_key: str = Field(min_length=1, max_length=100)
    principal_id: UUID | None = None


class AgentRunResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    task_run_id: UUID
    status: str
    trace_id: UUID


class AgentCompleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    output: dict[str, Any] = Field(default_factory=dict)


class KnowledgeIngestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_dir: str | None = None
    dry_run: bool = False
    max_chars: int = Field(default=1600, ge=128, le=10000)


class KnowledgeIngestResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    batch_id: str | None
    scanned: int
    accepted: int
    skipped: int
    deferred: int
    files: list[dict[str, object]]
    trace_id: UUID


class KnowledgeSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=2000)
    mode: Literal["keyword", "vector", "hybrid"] = "hybrid"
    limit: int = Field(default=10, ge=1, le=50)


class KnowledgeSearchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: str
    degraded: bool
    items: list[dict[str, Any]]
    trace_id: UUID


class KnowledgeStatsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    documents: int
    chunks: int
    embedding_chunks: int
    embedding_model: str | None
    embedding_coverage: float
    embedding_models: dict[str, int]
    waiting_extractor: int
    failed_files: int
    vector_ready: bool
    trace_id: UUID


class KnowledgeItemsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[dict[str, Any]]
    trace_id: UUID


class KnowledgeAdapterResponse(BaseModel):
    """Read-only declaration of an optional local rich-file processor."""

    model_config = ConfigDict(extra="forbid")

    key: str
    label: str
    supported_suffixes: list[str]
    status: str
    execution_mode: str
    next_step: str


class KnowledgeAdaptersResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[KnowledgeAdapterResponse]
    trace_id: UUID


class KnowledgeEmbedRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    batch_id: UUID | None = None
    limit: int = Field(default=32, ge=1, le=256)


class KnowledgeEmbedResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    embedded: int
    model: str
    trace_id: UUID


class KnowledgeDocumentLifecycleRequest(BaseModel):
    """Human-readable audit reason for a reversible document action."""

    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=1000)


class KnowledgeDocumentLifecycleResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: UUID
    recycle_bin_id: UUID
    status: str
    trace_id: UUID


class KnowledgeRichMediaExtractRequest(BaseModel):
    """Submit one pending rich-media ingest file to the local MinerU adapter."""

    model_config = ConfigDict(extra="forbid")

    ingest_file_id: UUID
    method: Literal["auto", "txt", "ocr"] = "auto"


class KnowledgeRichMediaExtractResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: UUID
    ingest_file_id: UUID
    status: Literal["queued", "processing", "completed", "failed"]
    attempt_count: int
    method: Literal["auto", "txt", "ocr"]
    trace_id: UUID


class GraphSpaceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(pattern=r"^[a-z][a-z0-9._-]*$")
    level: str = Field(pattern=r"^L[0-4]$")
    name: str = Field(min_length=1, max_length=200)
    cluster_key: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9._-]*$")
    description: str = Field(default="", max_length=1_000)


class GraphNodeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    space_key: str
    canonical_key: str
    node_type: str
    label: str
    properties: dict[str, object] = Field(default_factory=dict)


class GraphEdgeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    space_key: str
    source_node_id: UUID
    target_node_id: UUID
    relation_type: str
    weight: float = 1.0


class GraphBridgeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_space_key: str = Field(pattern=r"^[a-z][a-z0-9._-]*$")
    target_space_key: str = Field(pattern=r"^[a-z][a-z0-9._-]*$")
    source_node_id: UUID
    target_node_id: UUID
    relation_type: Literal["artifact_ref", "capability_contract", "released_graph_ref", "health_signal"]
    weight: float = Field(default=1.0, ge=0, le=1)


class GraphNodeRetireRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=1_000)


class GraphRevisionRollbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=1_000)


class GraphPartModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_key: str
    node_type: str
    label: str = Field(min_length=1, max_length=200)
    redirect_relations: list[str] = Field(default_factory=list)
    properties: dict[str, object] = Field(default_factory=dict)


class GraphMergeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    space_key: str = Field(pattern=r"^[a-z][a-z0-9._-]*$")
    target_key: str
    source_keys: list[str] = Field(min_length=1, max_length=100)
    expected_revision: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=1_000)


class GraphSplitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    space_key: str = Field(pattern=r"^[a-z][a-z0-9._-]*$")
    source_key: str
    parts: list[GraphPartModel] = Field(min_length=1, max_length=50)
    expected_revision: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=1_000)


class GraphArbitrationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: UUID
    decision: Literal["merge", "keep_source", "reject_claim", "resolve"]
    reason: str = Field(min_length=1, max_length=1_000)
    merge: dict[str, object] | None = None


class GraphMergeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    merge_id: str
    space_key: str
    target_key: str
    source_keys: list[str]
    redirected_edges: int
    status: str
    reason: str | None
    trace_id: UUID


class GraphSplitResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    split_id: str
    space_key: str
    source_key: str
    parts: list[str]
    status: str
    reason: str | None = None
    trace_id: UUID


class GraphArbitrationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision_id: str
    case_id: str
    decision: str
    status: str
    resolution_merge_id: str | None
    resolved: bool
    trace_id: UUID


class GraphMergeRecordsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[dict[str, object]]
    trace_id: UUID


class GraphSplitRecordsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[dict[str, object]]
    trace_id: UUID


class GraphNeighborsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nodes: list[str]
    edges: list[dict[str, object]]
    partial: bool
    trace_id: UUID


class GraphVisualizationNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    label: str
    node_type: str
    # 解释"这段关系为什么存在"的证据（取自节点的 properties，缺省即不返回）。
    description: str | None = None
    inputs: list[str] = Field(default_factory=list)
    outputs: list[str] = Field(default_factory=list)
    family: str | None = None
    stage: str | None = None
    lifecycle: str | None = None


class GraphVisualizationEdge(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str
    target: str
    relation: str
    weight: float
    #: 权重的依据（经验实测 / 契约衔接 / 声明层级）。界面必须显示它。
    basis: str = ""


class GraphVisualizationSpace(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    level: str
    name: str
    node_count: int
    edge_count: int


class GraphVisualizationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    space_key: str | None
    spaces: list[GraphVisualizationSpace]
    nodes: list[GraphVisualizationNode]
    edges: list[GraphVisualizationEdge]
    partial: bool
    trace_id: UUID


class GraphRouteResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nodes: list[str]
    edges: list[dict[str, object]]
    visited_space_keys: list[str]
    partial: bool
    truncation_reasons: list[str]
    budget: dict[str, object]
    trace_id: UUID


class GraphGovernanceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    spaces: list[dict[str, object]]
    active_bridge_count: int
    open_conflict_count: int
    trace_id: UUID


class GraphNodeRevisionsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[dict[str, object]]
    trace_id: UUID


class GraphConflictsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[dict[str, object]]
    trace_id: UUID


class GraphExtractionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: UUID | None = None
    space_key: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9._-]*$")
    limit_documents: int = Field(default=10, ge=1, le=200)


class GraphExtractionCandidateModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: str
    source_uri: str
    space_key: str
    node_key: str
    node_type: str
    label: str
    property_source: dict[str, object]
    relation_type: str | None = None
    target_node_key: str | None = None
    properties: dict[str, object] = Field(default_factory=dict)
    confidence: float = Field(ge=0, le=1)


class GraphExtractionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidates: list[dict[str, object]]
    conflicts: list[dict[str, object]]
    rejected: list[str]
    staged_as_changeset: str | None
    changeset_status: str | None
    trace_id: UUID


class GraphProposalNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_key: str
    node_type: str
    label: str
    space_key: str
    operation: str


class GraphProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    status: str
    risk_class: str
    graph_space_key: str | None
    candidate_count: int
    nodes: list[GraphProposalNode]
    submitted_at: str | None = None
    approved_at: str | None = None
    applied_at: str | None = None


class GraphProposalListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proposals: list[GraphProposal]
    trace_id: UUID


class GraphProposalDecisionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proposal_id: str
    status: str
    release_id: str | None = None
    trace_id: UUID


class AuditLedgerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    csv_path: str
    engagement_name: str = "Ledger Review"
    amount_threshold: float = Field(default=1_000_000, ge=0)


class AuditLedgerResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    engagement_id: str
    rows: int
    anomalies: int
    duplicate_rows: int
    missing_amounts: int
    trace_id: UUID


class AuditEngagementsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[dict[str, object]]
    trace_id: UUID


class AuditLineageResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    engagement_id: str
    engagement: dict[str, object]
    evidence: list[dict[str, object]]
    anomalies: list[dict[str, object]]
    findings: list[dict[str, object]]
    trace_id: UUID


class AuditConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: UUID
    reviewer_label: str


class AuditConfirmResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    finding_id: str
    candidate_id: str
    status: str
    trace_id: UUID


class QuantBacktestsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[dict[str, object]]
    trace_id: UUID


class QuantLineageResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    backtest_id: str
    backtest: dict[str, object]
    dataset: dict[str, object] | None
    trace_id: UUID


class AIOpsIncidentsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[dict[str, object]]
    trace_id: UUID


class AIOpsLineageResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["aiops_governance_chain"]
    incident_id: str
    simulated_only: Literal[True]
    incident: dict[str, object]
    alerts: list[dict[str, object]]
    proposals: list[dict[str, object]]
    change_requests: list[dict[str, object]]
    executions: list[dict[str, object]]
    verifications: list[dict[str, object]]
    trace_id: UUID


class AIOpsVerifyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome: Literal["healthy", "rollback"]
    reviewer_label: str = Field(min_length=2, max_length=120)


class AIOpsVerifyResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    execution_id: str
    verification_id: str
    status: Literal["verified", "rolled_back"]
    idempotent: bool
    trace_id: UUID


class TopologyClustersResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[dict[str, object]]
    trace_id: UUID


class TopologyBlueprintsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[dict[str, object]]
    trace_id: UUID


class NebulaStage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    name: str
    color: str
    order: int


class NebulaNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    capability: str
    layer: Literal["gov", "base", "biz"]
    stage: str | None
    lifecycle: str
    ctrl: bool
    inputs: list[str] = []
    outputs: list[str] = []
    invokes: list[str] = []
    #: Declared plugin version, and the plugin it was derived from (blank when
    #: the plugin declares no ``provenance.derived_from``).
    version: str = ""
    derived_from: str = ""


class NebulaEdge(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str
    target: str
    type: str
    contract: str = ""


class NebulaLayer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    name: str
    kind: str


class PluginNebulaResponse(BaseModel):
    """Dynamic knowledge-nebula graph; reflects newly added on-disk plugins."""

    model_config = ConfigDict(extra="forbid")

    stages: list[NebulaStage]
    nodes: list[NebulaNode]
    edges: list[NebulaEdge]
    trunk: list[NebulaEdge]
    layers: list[NebulaLayer]
    stats: dict[str, object]
    trace_id: UUID


class ExperienceEdgeStat(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str
    target: str
    contract: str
    success_count: int
    fail_count: int
    total_latency_ms: int
    declared: bool
    confidence: float
    base_weight: float
    weight: float
    first_used_at: str | None
    last_used_at: str | None
    evidence_run_ids: list[str] = []


class ExperienceNodeStat(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plugin_id: str
    capability: str
    use_count: int
    success_count: int
    fail_count: int
    total_latency_ms: int
    error_kind_counts: dict[str, int] = {}
    confidence: float
    base_weight: float
    weight: float
    first_used_at: str | None
    last_used_at: str | None


class ExperienceSuggestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    source: str
    target: str
    contract: str
    status: Literal["proposed", "accepted", "dismissed"]
    evidence_count: int
    success_count: int
    fail_count: int
    confidence: float
    weight: float
    last_evidence_at: str | None
    created_at: str | None
    decided_by: str | None
    decided_at: str | None
    evidence_run_ids: list[str] = []
    # L2 light-solidification governance links (populated once an accepted
    # relation has been published through a knowledge release).
    changeset_id: str | None = None
    release_id: str | None = None
    released_at: str | None = None


class ExperienceUsedIn(BaseModel):
    """One plugin version proven in one business project (from archived runs)."""

    model_config = ConfigDict(extra="forbid")

    plugin_id: str
    plugin_version: str
    project_id: UUID
    project_slug: str
    project_name: str
    use_count: int
    success_count: int
    last_used_at: str | None = None


class NebulaExperienceResponse(BaseModel):
    """Design-time nebula graph plus the run-accumulated experience overlay."""

    model_config = ConfigDict(extra="forbid")

    graph: PluginNebulaResponse
    edge_stats: list[ExperienceEdgeStat]
    node_stats: list[ExperienceNodeStat]
    suggestions: list[ExperienceSuggestion]
    used_in: list[ExperienceUsedIn]
    trace_id: UUID


class ExperienceSuggestionListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[ExperienceSuggestion]
    trace_id: UUID


class ArchiveRunRequest(BaseModel):
    """Anchor a run to the business project it was performed for."""

    model_config = ConfigDict(extra="forbid")

    project_id: UUID
    note: str = Field(default="", max_length=500)


class ArchiveRunResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: UUID
    project_id: UUID
    archive_link_id: UUID
    archived_at: str
    idempotent: bool
    trace_id: UUID


class ExperienceSuggestionDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["accepted", "dismissed"]
    reason: str | None = None


class ExperienceSuggestionDecisionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item: ExperienceSuggestion
    trace_id: UUID


class ExperienceRebuildResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    edges: int
    nodes: int
    trace_id: UUID


class TopologyReleasesResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[dict[str, object]]
    trace_id: UUID


class TopologyPlansResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[dict[str, object]]
    trace_id: UUID


class TopologyBridgesResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[dict[str, object]]
    trace_id: UUID


class TopologyChainsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[dict[str, object]]
    trace_id: UUID


class TopologyIntentsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[dict[str, object]]
    trace_id: UUID


class TopologyChainMaterializeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_key: str = Field(pattern=r"^plan-[a-z0-9]{16}$")
    idempotency_key: str = Field(min_length=1, max_length=255)
    reason: str = Field(default="", max_length=500)


class TopologyChainMaterializeResponse(BaseModel):
    """Idempotent materialization result; ``already_materialized`` marks a
    deterministic replay of a plan that already has a chain (same inputs,
    same chain_key/checksum)."""

    model_config = ConfigDict(extra="forbid")

    chain_key: str
    plan_key: str
    mode: str
    planner_version: str
    checksum: str
    release_locked: bool = False
    node_count: int = 0
    binding_count: int = 0
    intent_count: int = 0
    chain_id: str | None = None
    already_materialized: bool = False
    idempotent: bool = False
    trace_id: UUID


class TopologyApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chain_key: str = Field(pattern=r"^chain-[a-z0-9]{16}$")
    slot_key: str = Field(pattern=r"^[a-z][a-z0-9_]*(?:[._-][a-z0-9]+)*$")
    decision: Literal["approve", "reject"]
    idempotency_key: str = Field(min_length=1, max_length=255)
    reason: str = Field(default="", max_length=500)


class TopologyApprovalResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chain_key: str
    slot_key: str
    decision: str
    intent_status: str
    approval_ref: str
    approver: str
    idempotent: bool = False
    trace_id: UUID


class TopologyApprovalsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[dict[str, object]]
    trace_id: UUID


class TopologyExecutionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chain_key: str = Field(pattern=r"^chain-[a-z0-9]{16}$")
    mode: Literal["simulated", "isolated"]
    idempotency_key: str = Field(min_length=1, max_length=255)
    reason: str = Field(default="", max_length=500)


class TopologyExecutionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chain_key: str
    mode: str
    status: str
    planner_version: str = ""
    chain_checksum: str = ""
    node_count: int = 0
    entries: list[dict[str, object]] = Field(default_factory=list)
    already_executed: bool = False
    idempotent: bool = False
    trace_id: UUID


class TopologyExecutionsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[dict[str, object]]
    trace_id: UUID


class TopologyRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chain_key: str = Field(pattern=r"^chain-[a-z0-9]{16}$")
    mode: Literal["isolated"]
    idempotency_key: str = Field(min_length=1, max_length=255)
    reason: str = Field(min_length=1, max_length=500)


class TopologyRunResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: UUID
    chain_key: str
    mode: str
    status: str
    node_total: int = 0
    node_succeeded: int = 0
    node_failed: int = 0
    reason: str = ""
    trace_id: UUID
    started_at: datetime
    finished_at: datetime | None = None
    chain_checksum: str = ""
    planner_version: str = ""
    entries: list[dict[str, object]] = Field(default_factory=list)
    idempotent: bool = False


class TopologyRunsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[dict[str, object]]
    trace_id: UUID


class TopologyVerificationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reference_run_id: UUID | None = None
    reason: str = Field(min_length=1, max_length=500)
    idempotency_key: str = Field(min_length=1, max_length=255)


class TopologyRunVerificationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verification_id: UUID
    run_id: UUID
    chain_key: str
    reference_run_id: UUID | None = None
    status: str
    node_total: int = 0
    node_matched: int = 0
    node_mismatched: int = 0
    node_ref_missing: int = 0
    reason: str = ""
    rollback_verdict: dict[str, object] | None = None
    trace_id: UUID
    created_at: datetime | None = None
    idempotent: bool = False


class TopologyVerificationsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[dict[str, object]]
    trace_id: UUID


class TopologyRemediationProposalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["re-verify", "re-run-locked-release", "escalate-human"]
    reason: str = Field(min_length=1, max_length=500)
    idempotency_key: str = Field(min_length=1, max_length=255)


class TopologyRemediationProposalResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proposal_id: UUID
    verification_id: UUID
    run_id: UUID
    chain_key: str
    status: str
    action: str
    affected_slots: list[str] = Field(default_factory=list)
    baseline: dict[str, object] = Field(default_factory=dict)
    remediation_run_id: UUID | None = None
    reason: str = ""
    trace_id: UUID
    created_at: datetime | None = None
    decision: str | None = None
    idempotent: bool = False


class TopologyRemediationDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["approve", "reject", "close"]
    approver: str = Field(min_length=2, max_length=120)
    reason: str = Field(min_length=1, max_length=500)
    idempotency_key: str = Field(min_length=1, max_length=255)


class TopologyRemediationRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=500)
    idempotency_key: str = Field(min_length=1, max_length=255)


class TopologyRemediationRunResponse(TopologyRunResponse):
    """Run projection plus the proposal -> run lineage ids (M8)."""

    proposal_id: UUID | None = None
    remediation_run_id: UUID | None = None


class TopologyRemediationProposalsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[dict[str, object]]
    trace_id: UUID


class TopologyEvidenceAnchorRequest(BaseModel):
    """M9: begin one append-only anchor batch for the tenant evidence chain."""

    model_config = ConfigDict(extra="forbid")

    scope: Literal["full", "topology", "chain", "execution", "verification", "remediation"]
    idempotency_key: str = Field(min_length=1, max_length=128)


class TopologyEvidenceAnchorResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope: str
    idempotent: bool
    entries: list[dict[str, object]] = Field(default_factory=list)
    trace_id: UUID


class TopologyEvidenceProofResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chain_key: str
    scope: str
    total_anchors: int
    tail_hash: str
    verified: bool
    first_mismatch: dict[str, object] | None = None
    checked_at: str
    trace_id: UUID


class TopologyEvidenceExportResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chain_key: str
    scope: str
    entries: list[dict[str, object]] = Field(default_factory=list)
    sha256: str
    proof_ref: dict[str, object] = Field(default_factory=dict)
    exported_at: str
    trace_id: UUID


class TopologyEvidenceStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chain_key: str
    total_anchors: int
    tail_seq: int
    tail_hash: str
    last_anchored_at: str | None = None
    checked_at: str
    trace_id: UUID


class TopologyPlanningIntentRequest(BaseModel):
    """M10 graph-driven planning request (contract: topology-planning-intent)."""

    model_config = ConfigDict(extra="forbid")

    intent: str = Field(min_length=1, max_length=512)
    budget: dict[str, int] = Field(default_factory=lambda: {"max_matches": 4, "expand_hops": 1})
    idempotency_key: str = Field(min_length=1, max_length=255)
    reason: str = Field(min_length=1, max_length=500)
    trace_id: UUID | None = None


class TopologyPlanningIntentResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent_id: UUID
    intent_text: str
    matched_nodes: list[dict[str, object]] = Field(default_factory=list)
    capability_requirements: list[str] = Field(default_factory=list)
    plan_key: str
    mode: str
    plan_checksum: str
    reused_plan: bool = False
    node_count: int = 0
    edge_count: int = 0
    created_at: str
    trace_id: UUID
    idempotent: bool = False


class TopologyPlanningIntentsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[dict[str, object]]
    trace_id: UUID


def _project_root() -> Path:
    """Repository root, shared by the read-only views that scan real artifacts."""

    return Path(__file__).resolve().parents[2]


def _trace_id(request: Request) -> UUID:
    """Return the request trace ID installed by middleware."""

    value = getattr(request.state, "trace_id", None)
    if isinstance(value, UUID):
        return value
    return uuid4()


def _sha256_of_file(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _spool_root(data_dir: Path | None = None) -> Path:
    """The segmented log-spool location, relocatable via the environment.

    ``AUDIT_NETWORK_SPOOL_ROOT`` overrides the default ``<data>/.data/isolated/logs``.
    Two reasons: an operator may want the spool on a different volume, and a
    test that exercises trace-locate needs a spool of its own — writing into the
    shared application spool made the suite fail as soon as a previous run's
    segment was still on disk, because a reused segment id failed content
    verification.
    """
    override = os.getenv("AUDIT_NETWORK_SPOOL_ROOT", "").strip()
    if override:
        return Path(override)
    base = data_dir or (Path(__file__).resolve().parents[2] / ".data")
    return base / "isolated" / "logs"


def _query_spool_logs(data_dir: Path, trace_id: str) -> LogQueryResult:
    """CW2: query the persistent spool's event/archive index for one trace id.

    A spool read failure is ``unavailable`` (distinct from "no records",
    which stays complete); a producer without a seal is ``partial`` with
    ``unknown_tail`` (方案 8.5)."""
    spool_root = _spool_root(data_dir)
    try:
        result = SegmentedSpool(spool_root).query(trace_id=trace_id, limit=100)
        events = [
            LogEvent(**{key: value for key, value in item.items() if key in LogEvent.model_fields})
            for item in result["events"]
        ]
        return LogQueryResult(
            events=events,
            completeness="complete" if result["completeness"] == "complete" else "partial",
            persisted_seq=result["persisted_seq"],
            unknown_tail=result["unknown_tail"],
            archive_state=result["archive_state"],
            next_cursor=result["next_cursor"],
            missing_sources=result["missing_sources"],
        )
    except OSError as exc:
        logger.warning("spool log query unavailable for trace_id=%s: %s", trace_id, exc)
        return LogQueryResult(events=[], completeness="unavailable")


def _run_extraction(database_url: str, tenant_slug: str, payload: GraphExtractionRequest) -> tuple[list[Any], list[dict[str, Any]]]:
    """Load recent (or a specific) ingested text documents and produce extraction candidates.

    Reads only ``semantic.documents`` + ``semantic.chunks``; never writes to the graph.
    """
    import psycopg2 as _pg

    from packages.knowledge.graph_extraction import GraphExtractionCandidate

    candidates: list[GraphExtractionCandidate] = []
    with _pg.connect(database_url) as connection, connection.cursor() as cur:
        cur.execute("SELECT id FROM iam.tenants WHERE slug=%s", (tenant_slug,))
        tenant = cur.fetchone()
        if tenant is None:
            raise ValueError(f"tenant not found: {tenant_slug}")
        tenant_id = tenant[0]
        cur.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),))
        if payload.document_id is not None:
            cur.execute(
                "SELECT id,title,source_uri FROM semantic.documents WHERE tenant_id=%s AND id=%s AND status='staged'",
                (tenant_id, payload.document_id),
            )
            docs = cur.fetchall()
        else:
            cur.execute(
                """SELECT id,title,source_uri FROM semantic.documents
                WHERE tenant_id=%s AND status='staged' ORDER BY updated_at DESC LIMIT %s""",
                (tenant_id, payload.limit_documents),
            )
            docs = cur.fetchall()
        for doc_id, doc_title, source_uri in docs:
            cur.execute(
                "SELECT content FROM semantic.chunks WHERE tenant_id=%s AND document_id=%s ORDER BY ordinal",
                (tenant_id, doc_id),
            )
            body = "\n\n".join(row[0] for row in cur.fetchall())
            space_key = payload.space_key or "audit-l1"
            for candidate in extract_text(
                doc_title or "(untitled)", body, document_id=UUID(str(doc_id)), source_uri=str(source_uri), space_key=space_key
            ):
                candidates.append(candidate)
    return candidates, []


def _argument_hash(arguments: dict[str, Any]) -> str:
    canonical = json.dumps(arguments, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _safe_upload_relative(raw_path: str, fallback_name: str, index: int) -> Path:
    """Accept a browser folder-relative path without allowing traversal."""
    source = (raw_path or fallback_name).replace("\\", "/")
    parts = [part for part in source.split("/") if part not in {"", "."}]
    if not parts or any(part == ".." or ":" in part for part in parts):
        raise HTTPException(status_code=400, detail=f"invalid upload path at index {index}")
    return Path(*parts)


@dataclass(frozen=True, slots=True)
class _KnowledgeUploadDescriptor:
    """Validated, content-addressed metadata for a single request file."""

    relative_path: Path
    filename: str
    sha256: str
    byte_size: int


async def _describe_knowledge_uploads(
    files: list[UploadFile],
    relative_paths: list[str],
    *,
    max_bytes: int,
) -> tuple[list[_KnowledgeUploadDescriptor], str]:
    """Hash request bodies without retaining them in memory or executing them."""

    if len(relative_paths) > len(files):
        raise HTTPException(status_code=422, detail="relative_paths has more entries than files")
    descriptors: list[_KnowledgeUploadDescriptor] = []
    seen_paths: set[str] = set()
    for index, upload in enumerate(files):
        filename = upload.filename or "upload"
        relative_path = _safe_upload_relative(
            relative_paths[index] if index < len(relative_paths) else "",
            filename,
            index,
        )
        relative_key = relative_path.as_posix()
        if relative_key in seen_paths:
            raise HTTPException(status_code=409, detail=f"duplicate upload path at index {index}")
        seen_paths.add(relative_key)
        digest = hashlib.sha256()
        byte_size = 0
        while chunk := await upload.read(1024 * 1024):
            byte_size += len(chunk)
            if byte_size > max_bytes:
                raise HTTPException(status_code=413, detail=f"file exceeds {max_bytes} byte limit")
            digest.update(chunk)
        await upload.seek(0)
        descriptors.append(
            _KnowledgeUploadDescriptor(
                relative_path=relative_path,
                filename=filename,
                sha256=digest.hexdigest(),
                byte_size=byte_size,
            )
        )
    request_hash = _argument_hash(
        {
            "files": [
                {
                    "filename": descriptor.filename,
                    "relative_path": descriptor.relative_path.as_posix(),
                    "sha256": descriptor.sha256,
                    "byte_size": descriptor.byte_size,
                }
                for descriptor in descriptors
            ]
        }
    )
    return descriptors, request_hash


async def _stage_knowledge_uploads(
    files: list[UploadFile],
    descriptors: list[_KnowledgeUploadDescriptor],
    upload_root: Path,
) -> None:
    """Stream previously fingerprinted bodies into one controlled local root."""

    for upload, descriptor in zip(files, descriptors, strict=True):
        destination = (upload_root / descriptor.relative_path).resolve()
        try:
            destination.relative_to(upload_root)
        except ValueError as exc:  # pragma: no cover - descriptor is already validated
            raise HTTPException(status_code=400, detail="upload path escapes staging root") from exc
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            with destination.open("xb") as stream:
                while chunk := await upload.read(1024 * 1024):
                    stream.write(chunk)
        except FileExistsError as exc:  # pragma: no cover - upload root is unique per owned attempt
            raise HTTPException(status_code=409, detail="duplicate upload path") from exc


def _tenant_context(connection: Any, tenant_id: UUID) -> tuple[Any, Any]:
    """Set transaction-local tenant context and return a cursor pair.

    The caller owns the connection and must keep all tenant-scoped statements
    in this transaction.  RLS remains the final enforcement boundary.
    """

    cursor = connection.cursor()
    cursor.execute("SELECT id FROM iam.tenants WHERE id=%s", (tenant_id,))
    if cursor.fetchone() is None:
        cursor.close()
        raise HTTPException(status_code=404, detail="tenant not found")
    cursor.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),))
    return connection, cursor


def _canvas_capability_prior(database_url: str, tenant_id: UUID) -> dict[str, float] | None:
    """环 7 读路径：把经验层的实测权重交给召回排序。

    开关 ``AI_PLANNER_EXPERIENCE_PRIOR`` 未开时返回 ``None``，提示词与从前**逐字节
    一致**；经验层不可达时也返回 ``None``（读路径不得成为规划的新故障点）。
    该先验只重排能力清单的顺序，不改变清单成员，也不参与任何策略判定。
    """
    return ai_experience_prior_module.resolve_capability_prior(database_url, tenant_id)


def _record_canvas_chat_intent(
    database_url: str,
    tenant_id: UUID,
    payload: CanvasChatRequest,
    outcome: Any | None,
    trace_id: UUID,
    *,
    failure_reason: str | None = None,
) -> str:
    """Append one accepted canvas-planning request to the evidence ledger.

    Replaying the same ``Idempotency-Key`` returns the recorded row instead of
    inserting a duplicate (``UNIQUE(tenant_id, idempotency_key)``). This also
    records a model/compiler failure: an accepted request must not disappear
    merely because it did not produce a draft. No UPDATE/DELETE is ever issued
    against planning intents.
    """
    capabilities: list[str] = []
    if outcome is not None and isinstance(outcome.draft, dict):
        for node in outcome.draft.get("nodes") or []:
            if isinstance(node, dict) and node.get("capability"):
                capabilities.append(str(node["capability"]))
    plan_key = (
        str(outcome.plan_key)
        if outcome is not None
        else f"canvas-failed-{hashlib.sha256(payload.idempotency_key.encode('utf-8')).hexdigest()[:20]}"
    )
    reason = "canvas-chat"
    if failure_reason:
        # Keep the append-only audit row useful without turning it into an
        # unbounded dump of an upstream response body.
        reason = f"canvas-chat:failed:{failure_reason.strip()[:480]}"
    with psycopg2.connect(database_url) as connection:
        with connection.cursor() as cur:
            cur.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),))
            cur.fetchone()
            cur.execute(
                """INSERT INTO topology.planning_intents
                (tenant_id,intent_text,matched_nodes,capability_requirements,plan_key,trace_id,idempotency_key,reason)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (tenant_id, idempotency_key) DO NOTHING
                RETURNING id""",
                (
                    str(tenant_id),
                    payload.message,
                    Json([]),
                    Json(list(dict.fromkeys(capabilities))),
                    plan_key,
                    str(trace_id),
                    payload.idempotency_key,
                    reason,
                ),
            )
            row = cur.fetchone()
            if row is not None:
                return str(row[0])
            cur.execute(
                "SELECT id FROM topology.planning_intents "
                "WHERE tenant_id=%s AND idempotency_key=%s",
                (str(tenant_id), payload.idempotency_key),
            )
            replay = cur.fetchone()
            if replay is None:
                raise RuntimeError("chat intent evidence replay missing")
            return str(replay[0])


def _record_canvas_chat_failure(
    database_url: str,
    tenant_id: UUID,
    payload: CanvasChatRequest,
    trace_id: UUID,
    detail: str,
) -> str | None:
    """Best-effort durable reference for an accepted planning failure.

    The caller still returns the original planning failure. If the database
    itself is unavailable, there is no safe alternate ledger to claim.
    """
    try:
        return _record_canvas_chat_intent(
            database_url,
            tenant_id,
            payload,
            None,
            trace_id,
            failure_reason=detail,
        )
    except psycopg2.Error:
        logger.exception("unable to persist canvas chat failure evidence")
        return None


def _canvas_chat_failure_detail(detail: str, intent_id: str | None) -> str:
    """User-safe error text which points to the durable failure row."""
    if intent_id:
        return f"{detail}；失败记录已保存：{intent_id}"
    return detail


def _load_policy(cur: Any, tenant_id: UUID) -> tuple[list[dict[str, Any]], str]:
    """Combine all active policy sets for a tenant.

    Policy sets are additive: a team may publish a baseline deny list and a
    separate capability allow list.  Selecting only the numerically largest
    version made an unrelated scheduler policy hide a newer audit rule.  The
    deterministic engine retains deny/freeze precedence after this merge.
    """
    cur.execute(
        """
        SELECT rules, version::text FROM policy.policy_sets
        WHERE tenant_id=%s AND status='active'
        ORDER BY created_at ASC, id ASC
        """,
        (tenant_id,),
    )
    rows = cur.fetchall()
    if not rows:
        return [], "none"
    rules: list[dict[str, Any]] = []
    versions: list[str] = []
    for row_rules, version in rows:
        if isinstance(row_rules, list):
            rules.extend(rule for rule in row_rules if isinstance(rule, dict))
        versions.append(str(version))
    return rules, ",".join(versions)


def _matched_rule_uuid(result: PolicyResult) -> list[UUID]:
    if not result.matched_rule:
        return []
    try:
        return [UUID(result.matched_rule)]
    except ValueError:
        return []


def _decision_name(decision: str) -> Literal["ALLOW", "DENY", "REQUIRE_APPROVAL", "FREEZE"]:
    return {
        "allow": "ALLOW",
        "deny": "DENY",
        "approval_required": "REQUIRE_APPROVAL",
        "freeze": "FREEZE",
    }.get(decision, "DENY")  # type: ignore[return-value]


def _configure_file_logging() -> None:
    """Persist service logs to .data/api.log with size rotation + trace fields.

    R3 (L5): the previous single unbounded FileHandler is replaced by the
    observability rotating handler (10 MB x 30 backups ~ 300 MB ceiling) and
    every record carries trace_id + code_sha for the drill-down contract.

    Logging is a side channel: a locked or unwritable log file (a second
    instance, an antivirus hold, a read-only checkout) must not stop the service
    from starting, nor make ``apps.api.main`` unimportable.  Failures degrade to
    stderr with a warning, the same stance the experience projector takes.
    """
    data_dir = Path(__file__).resolve().parents[2] / ".data"
    try:
        data_dir.mkdir(exist_ok=True)
        handler = configure_rotating_file_logging(data_dir / "api.log")
    except OSError as exc:
        logging.getLogger(__name__).warning("file logging unavailable, using stderr only: %s", exc)
        return
    for logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(logger_name)
        if not any(isinstance(h, logging.handlers.RotatingFileHandler) for h in logger.handlers):
            logger.addHandler(handler)
    # CW2 wiring: every API log record also persists into the spool
    # (``.data/isolated/logs`` unless AUDIT_NETWORK_SPOOL_ROOT relocates it), so
    # trace-locate can query real events.
    try:
        attach_spool_logging(_spool_root(data_dir), producer_id="api")
    except OSError as exc:
        logging.getLogger(__name__).warning("spool logging unavailable: %s", exc)


class AIChatSettingsView(BaseModel):
    """Effective chat/planning channel.  The API key is never included."""

    model_config = ConfigDict(extra="forbid")

    provider: str
    base_url: str
    model: str
    timeout: float
    max_tokens: int | None = None
    reasoning_effort: str | None = None
    proxy: str | None = None
    num_ctx: int
    api_key_set: bool
    api_key_masked: str | None = None


class AIEmbeddingSettingsView(BaseModel):
    """Effective embedding channel."""

    model_config = ConfigDict(extra="forbid")

    provider: str
    base_url: str
    model: str
    timeout: float
    dimensions: int


class AISettingsResponse(BaseModel):
    """Settings page payload — masked secrets only, never a plaintext key."""

    model_config = ConfigDict(extra="forbid")

    chat: AIChatSettingsView
    embedding: AIEmbeddingSettingsView
    sources: dict[str, str]
    env_file: str
    env_file_exists: bool
    managed_keys: list[str]
    legacy_keys: list[str]
    supported_chat_providers: list[str]
    supported_embedding_providers: list[str]


class AISettingsUpdateRequest(BaseModel):
    """Partial update: an omitted (``None``) field is left untouched.

    An **empty string** clears the field, so resolution falls back to the
    legacy variable and then the built-in default.  ``api_key`` is write-only:
    it is accepted here and never echoed back by any response.
    """

    model_config = ConfigDict(extra="forbid")

    provider: str | None = None
    base_url: str | None = None
    model: str | None = None
    api_key: str | None = None
    timeout: float | None = Field(default=None, gt=0)
    max_tokens: int | None = Field(default=None, gt=0)
    reasoning_effort: str | None = None
    proxy: str | None = None
    user_agent: str | None = None
    num_ctx: int | None = Field(default=None, gt=0)
    embed_provider: str | None = None
    embed_base_url: str | None = None
    embed_model: str | None = None
    embed_timeout: float | None = Field(default=None, gt=0)
    embed_dimensions: int | None = Field(default=None, gt=0)


class AITestRequest(BaseModel):
    """Connectivity probe request for the settings page."""

    model_config = ConfigDict(extra="forbid")

    include_embedding: bool = False


class AITestResponse(BaseModel):
    """Probe outcome.  A failed probe is a 200 with ``ok=false``, not an error:
    it is a diagnostic result the UI must be able to render."""

    model_config = ConfigDict(extra="forbid")

    ok: bool
    provider: str
    model: str
    base_url: str
    detail: str
    latency_ms: int | None = None
    embedding_ok: bool | None = None
    embedding_detail: str | None = None
    embedding_model: str | None = None


def _ai_settings_updates(payload: AISettingsUpdateRequest) -> dict[str, str | None]:
    """Map a partial settings request onto the managed environment variables.

    ``None`` means "not supplied, leave unchanged"; ``""`` means "clear".
    Numeric values are rendered without a trailing ``.0`` so ``.env`` stays
    readable (``AI_TIMEOUT=600``, not ``600.0``).
    """
    mapping: list[tuple[str, object]] = [
        ("AI_PROVIDER", payload.provider),
        ("AI_BASE_URL", payload.base_url),
        ("AI_MODEL", payload.model),
        ("AI_API_KEY", payload.api_key),
        ("AI_TIMEOUT", payload.timeout),
        ("AI_MAX_TOKENS", payload.max_tokens),
        ("AI_REASONING_EFFORT", payload.reasoning_effort),
        ("AI_PROXY", payload.proxy),
        ("AI_USER_AGENT", payload.user_agent),
        ("AI_NUM_CTX", payload.num_ctx),
        ("AI_EMBED_PROVIDER", payload.embed_provider),
        ("AI_EMBED_BASE_URL", payload.embed_base_url),
        ("AI_EMBED_MODEL", payload.embed_model),
        ("AI_EMBED_TIMEOUT", payload.embed_timeout),
        ("AI_EMBED_DIMENSIONS", payload.embed_dimensions),
    ]
    updates: dict[str, str | None] = {}
    for key, value in mapping:
        if value is None:
            continue
        updates[key] = f"{value:g}" if isinstance(value, float) else str(value).strip()
    return updates


def _ai_settings_response() -> AISettingsResponse:
    """Build the settings view: effective values + where each one came from."""
    chat = ai_config.resolve_chat_config()
    embedding = ai_config.resolve_embedding_config()
    chat_view = ai_config.describe_chat_config(chat)
    chat_view.pop("sources", None)
    embedding_view = ai_config.describe_embedding_config(embedding)
    embedding_view.pop("sources", None)
    path = ai_env_store.env_file_path()
    return AISettingsResponse(
        chat=AIChatSettingsView(**chat_view),
        embedding=AIEmbeddingSettingsView(**embedding_view),
        sources={
            **chat.sources,
            **{f"embedding.{key}": value for key, value in embedding.sources.items()},
        },
        env_file=str(path),
        env_file_exists=path.exists(),
        managed_keys=list(ai_config.MANAGED_ENV),
        legacy_keys=list(ai_config.LEGACY_ENV),
        supported_chat_providers=list(ai_config.SUPPORTED_CHAT_PROVIDERS),
        supported_embedding_providers=list(ai_config.SUPPORTED_EMBED_PROVIDERS),
    )


def _reject_unknown_provider(value: str | None, supported: tuple[str, ...], field: str) -> None:
    """Fail before writing an unusable provider into ``.env``."""
    if value is None:
        return
    candidate = value.strip().lower()
    if candidate and candidate not in supported:
        raise HTTPException(
            status_code=422, detail=f"{field} must be one of {', '.join(supported)}"
        )


def _payload_intent(payload: Any) -> str | None:
    """The user's own words in this request, for graph recall to work on.

    Two request models feed the same directory assembly and name that text
    differently — ``goal`` for planning, ``message`` for canvas chat — so the
    difference is absorbed here rather than at each call site.
    """
    return getattr(payload, "goal", None) or getattr(payload, "message", None)


def _planning_directory(
    database_url: str,
    tenant_id: UUID,
    domain: str,
    *,
    intent_text: str | None = None,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """The recall catalog and port-contract registry, built as one pair.

    Two generations of directory coexist in this codebase, and the planner
    needs both:

    * the **DB blueprint directory** (``capability_catalog_from_db``) — the
      legacy cross-domain slots plus the verified runtime plugins.  The CW5
      demo chain runs on it, and its ports exist only under the legacy names
      (``ledger`` / ``candidates`` / ``experiment`` / ``evaluation``).
    * the **domain plugin directory** (``workbench.planning_directory``) — the
      real on-disk network (107 audit plugins, 161 ports), whose ports carry
      the shipped names (``ledger-artifact-ref`` / ``audit-quality-candidates``).

    They are merged, not swapped.  Replacing the legacy one would strand every
    existing template and draft, whose ports are only registered under the
    legacy names; keeping only the legacy one is the original defect — five
    recallable capabilities and seven contracts, so a draft of a real audit
    flow is rejected with ``contract_mismatch`` before the compiler is reached.
    On a port id both define, the legacy entry wins because it pins the fuller
    field set.

    The catalog and the registry are returned together and must be handed to
    ``AiPlanner.plan`` together: the prompt is built from the registry, and
    ``validate_draft`` judges the draft against it.  A prompt built from one
    registry and validated against another is a draft that cannot succeed
    whatever the model emits.

    Raises ``FileNotFoundError`` for an unknown domain and ``ValueError`` for a
    conflicting port contract; both are the caller's to map onto 422.

    With ``intent_text`` the **domain half is recalled instead of loaded whole**
    (S4).  The audit directory is 107 plugins / 161 ports and renders to ~81 000
    characters, which does not scale to the 100 000-plugin graph this system
    targets; the graph already answers "which capabilities does this phrase name"
    offline and deterministically.  Only the recallable subset of the catalog and
    of its contracts is handed over.

    The **legacy half is never narrowed**.  It is the DB blueprint directory the
    CW5 demo chain and every template run on, and its ports live only under the
    legacy names (``ledger``/``candidates``); dropping entries there strands
    existing drafts.  It is also small next to what dominates the prompt — the
    contract lines, which come from the domain registry.

    An intent that recalls nothing falls back to the full directory rather than
    planning against an empty one: recall failing is a signal to widen, not to
    hand the model no options at all.
    """
    legacy_catalog = capability_catalog_from_db(database_url, tenant_id)
    if intent_text:
        try:
            recalled_catalog, recalled_contracts, recalled = (
                ai_workbench_module.recall_planning_directory(
                    database_url, domain, intent_text,
                )
            )
        except psycopg2.Error:
            # The graph is an optimisation, not a precondition: if it is
            # unreachable the planner still works on the full directory.
            recalled = ()
        else:
            if recalled:
                return (
                    {**recalled_catalog, **legacy_catalog},
                    {**recalled_contracts, **PORT_CONTRACTS},
                )
    domain_catalog, domain_contracts = ai_workbench_module.planning_directory(domain)
    return {**domain_catalog, **legacy_catalog}, {**domain_contracts, **PORT_CONTRACTS}


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create the API app without opening a database connection."""

    _configure_file_logging()
    config = settings or Settings()
    app = FastAPI(
        title="Audit Network Control Plane",
        version=config.ui_shell_version,
        docs_url="/docs",
        redoc_url="/redoc",
    )

    @app.middleware("http")
    async def trace_middleware(request: Request, call_next: object) -> Response:
        # ``call_next`` is typed by Starlette; keeping this adapter narrow avoids
        # coupling the contract package to framework internals.
        trace_header = request.headers.get("x-trace-id")
        try:
            trace_id = UUID(trace_header) if trace_header else uuid4()
        except ValueError:
            trace_id = uuid4()
        request.state.trace_id = trace_id
        # CW0: install the resolved tenant context once so every handler (and the
        # global exception handler) reads the same source of truth instead of
        # re-parsing raw header spellings (x-tenant-slug vs X-Tenant-Id).
        tenant_header = request.headers.get("x-tenant-id")
        if not tenant_header:
            tenant_header = request.headers.get("x-tenant-slug")
        try:
            request.state.tenant_id = UUID(tenant_header) if tenant_header else None
        except ValueError:
            request.state.tenant_id = tenant_header or None
        with trace_context(str(trace_id)):
            response = cast(Response, await call_next(request))  # type: ignore[operator]
            response.headers["X-Trace-Id"] = str(trace_id)
            # CW2 wiring: request entry log inside the trace context — every API
            # call lands in the spool (attach_spool_logging) under its
            # trace_id, so trace-locate can drill from a data row to the real
            # request line.
            logger.info("http %s %s -> %s", request.method, request.url.path, response.status_code)
        return response

    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        """L3: structured 500 — trace_id, tenant, route, idempotency key.

        The stable error envelope lets an unattended operator correlate a 500
        with its log lines and data rows; the response never leaks internals.
        Tenant/idempotency come from the middleware-resolved request state
        (falling back to canonical header spellings) so the log line matches
        what the desktop actually sent.
        """
        trace_id = _trace_id(request)
        state_tenant = getattr(request.state, "tenant_id", None)
        if state_tenant is not None:
            tenant = str(state_tenant)
        else:
            tenant = request.headers.get("x-tenant-id") or request.headers.get("x-tenant-slug", "-")
        idem = request.headers.get("idempotency-key") or request.headers.get("x-idempotency-key", "-")
        logger.exception(
            "unhandled error trace_id=%s route=%s tenant=%s idempotency_key=%s",
            trace_id, request.url.path, tenant, idem,
        )
        return JSONResponse(
            status_code=500,
            headers={"X-Trace-Id": str(trace_id)},
            content={"detail": "internal error", "error_code": "INTERNAL_ERROR", "trace_id": str(trace_id)},
        )

    @app.get("/health", response_model=HealthResponse, tags=["system"])
    async def health(request: Request) -> HealthResponse:
        return HealthResponse(
            version=config.api_version,
            environment=config.app_env,
            # Configuration is reported without exposing credentials or making
            # a network call.  Readiness/database checks belong to Phase 1 DB.
            database_configured=bool(config.database_url.strip()),
            trace_id=_trace_id(request),
        )

    @app.get("/api/v1/health/ready", response_model=HealthReadyResponse, tags=["system"])
    async def health_ready(request: Request) -> HealthReadyResponse:
        """Readiness: DB connectivity, migration head, worker heartbeat, backlog.

        Every probe is read-only; a failed probe degrades the status instead of
        raising so the watchdog always receives a structured answer.
        """
        trace_id = _trace_id(request)
        database: Literal["ok", "error"] = "error"
        migration_head = "(unknown)"
        outbox_backlog = -1
        worker_heartbeats = 0
        worker_stale = 0
        stuck_tasks = -1
        try:
            with psycopg2.connect(config.database_url, connect_timeout=3) as connection, connection.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
                database = "ok"
                cur.execute("SELECT version_num FROM public.alembic_version")
                row = cur.fetchone()
                migration_head = str(row[0]) if row is not None else "(none)"
                cur.execute("SELECT count(*) FROM event.outbox WHERE published_at IS NULL")
                outbox_row = cur.fetchone()
                assert outbox_row is not None
                outbox_backlog = int(outbox_row[0])
                cur.execute(
                    "SELECT count(*), count(*) FILTER (WHERE last_seen < now() - interval '90 seconds') "
                    "FROM ops.worker_heartbeats"
                )
                heartbeat_row = cur.fetchone()
                assert heartbeat_row is not None
                worker_heartbeats, worker_stale = (int(v) for v in heartbeat_row)
                cur.execute("SELECT id FROM iam.tenants WHERE slug='local-dev'")
                tenant_row = cur.fetchone()
                if tenant_row is not None:
                    cur.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant_row[0]),))
                    cur.execute(
                        "SELECT count(*) FROM control.task_runs "
                        "WHERE status='running' AND lease_expires_at IS NOT NULL AND lease_expires_at < now()",
                    )
                    stuck_row = cur.fetchone()
                    assert stuck_row is not None
                    stuck_tasks = int(stuck_row[0])
        except Exception:  # noqa: BLE001 - readiness degrades, never raises
            logger.exception("readiness probe failed")
        checks: list[str] = []
        if database != "ok":
            checks.append("database unreachable")
        if migration_head != EXPECTED_MIGRATION_HEAD:
            checks.append(f"migration head {migration_head} != {EXPECTED_MIGRATION_HEAD}")
        if worker_heartbeats == 0 or worker_stale == worker_heartbeats:
            checks.append("no live worker heartbeat")
        if outbox_backlog > 500:
            checks.append(f"outbox backlog {outbox_backlog}")
        if stuck_tasks > 10:
            checks.append(f"stuck tasks {stuck_tasks}")
        return HealthReadyResponse(
            status="degraded" if checks else "ok",
            database=database,
            migration_head=migration_head,
            migration_expected=EXPECTED_MIGRATION_HEAD,
            worker_heartbeats=worker_heartbeats,
            worker_stale=worker_stale,
            outbox_backlog=outbox_backlog,
            stuck_tasks=stuck_tasks,
            checks=checks,
            trace_id=trace_id,
        )

    @app.get("/api/v1/observability/code-map", response_model=CodeMapResponse, tags=["system"])
    async def observability_code_map(
        request: Request,
        tenant_id: Annotated[UUID, Header(alias="X-Tenant-Id")],
    ) -> CodeMapResponse:
        """Code-layer drill-down index (L2): file -> line -> endpoint -> tables.

        Serves the generated docs/code-map.json when present, otherwise builds
        it on the fly.  Read-only and policy-gated (CW0); the desktop can offer
        a "view source" entry from this index.
        """
        require_policy(
            "observability.code-map.read",
            tenant_id=tenant_id,
            trace_id=_trace_id(request),
            risk_class="read_only",
            side_effects="read_only",
        )
        project_root = Path(__file__).resolve().parents[2]
        payload = load_code_map(project_root / "docs" / "code-map.json")
        if payload is None:
            payload = build_code_map(project_root)
        return CodeMapResponse(
            generated_at=payload["generated_at"],
            code_sha256=payload["code_sha256"],
            api_file=payload["api_file"],
            endpoint_count=payload["endpoint_count"],
            endpoints=payload["endpoints"],
            tenant_id=tenant_id,
            trace_id=_trace_id(request),
        )

    @app.get("/api/v1/observability/trace/{trace_id}", response_model=TraceLocateResponse, tags=["system"])
    async def observability_trace_locate(
        trace_id: str,
        request: Request,
        tenant_id: Annotated[UUID, Header(alias="X-Tenant-Id")],
    ) -> TraceLocateResponse:
        """L7 last hop: data rows + log locations for one trace id.

        Answers "which data rows were produced under this trace_id, and where
        in the logs are its lines" so an operator can drill from a lineage
        response straight into the log layer.  Read-only and tenant-scoped
        (CW0: the caller tenant is mandatory and policy-gated; a database
        failure is surfaced as 503/unavailable, never as an empty success).
        """
        require_policy(
            "observability.trace.read",
            tenant_id=tenant_id,
            trace_id=_trace_id(request),
            risk_class="read_only",
            side_effects="read_only",
        )
        data_rows: list[TraceDataRow] = []
        try:
            with psycopg2.connect(config.database_url, connect_timeout=3) as connection, connection.cursor() as cur:
                cur.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant_id),))
                cur.execute(
                    """SELECT id,node_key,capability,status FROM control.task_runs
                    WHERE tenant_id=%s AND trace_id=%s ORDER BY scheduled_at NULLS LAST, started_at NULLS LAST""",
                    (str(tenant_id), trace_id),
                )
                for task_id, node_key, capability, status in cur.fetchall():
                    data_rows.append(
                        TraceDataRow(
                            kind="task_run", table="control.task_runs", id=str(task_id),
                            detail=f"{node_key} ({capability})", status=str(status),
                        )
                    )
                cur.execute(
                    """SELECT id,workflow_key,status FROM control.workflow_runs
                    WHERE tenant_id=%s AND trace_id=%s ORDER BY started_at NULLS LAST""",
                    (str(tenant_id), trace_id),
                )
                for run_id, workflow_key, status in cur.fetchall():
                    data_rows.append(
                        TraceDataRow(
                            kind="workflow_run", table="control.workflow_runs", id=str(run_id),
                            detail=str(workflow_key), status=str(status),
                        )
                    )
                cur.execute(
                    """SELECT id,capability FROM policy.tool_calls
                    WHERE tenant_id=%s AND trace_id=%s ORDER BY requested_at""",
                    (str(tenant_id), trace_id),
                )
                for call_id, capability in cur.fetchall():
                    data_rows.append(
                        TraceDataRow(
                            kind="policy_decision", table="policy.tool_calls", id=str(call_id),
                            detail=str(capability),
                        )
                    )
        except HTTPException:
            raise
        except (psycopg2.Error, OSError) as exc:
            # CW0: a database failure must never look like "no results".
            logger.exception("trace locate unavailable for trace_id=%s tenant_id=%s", trace_id, tenant_id)
            raise HTTPException(
                status_code=503,
                detail={
                    "message": "trace lookup unavailable",
                    "completeness": "unavailable",
                    "trace_id": trace_id,
                },
            ) from exc
        data_dir = Path(__file__).resolve().parents[2] / ".data"
        log_locations = [
            LogLocation(file=str(data_dir / "api.log"), query=f"trace_id={trace_id}"),
            LogLocation(file=str(data_dir / "worker.log"), query=f"trace_id={trace_id}"),
        ]
        log_query = _query_spool_logs(data_dir, trace_id)
        return TraceLocateResponse(
            trace_id=trace_id,
            data_rows=data_rows,
            log_locations=log_locations,
            request_trace_id=_trace_id(request),
            completeness="complete",
            log_query=log_query,
        )

    @app.get("/api/v1/ui/bootstrap", response_model=UIBootstrapResponse, tags=["ui"])
    async def ui_bootstrap(request: Request) -> UIBootstrapResponse:
        return UIBootstrapResponse(
            api_version=config.api_version,
            shell_version=config.ui_shell_version,
            supported_ui_schema_versions=["1.0.0"],
            slots=[
                "global.navigation",
                "workspace.navigation",
                "workspace.tools",
                "dashboard.cards",
                "entity.tabs",
                "detail.actions",
                "settings.sections",
            ],
            renderers=[
                RendererDescriptor(
                    id=renderer,
                    version="1.0.0",
                    capabilities=["declarative", "schema-validated"],
                )
                for renderer in [
                    "markdown",
                    "data-table",
                    "form",
                    "chart",
                    "graph",
                    "card",
                    "json",
                ]
            ],
            locales=["zh-CN", "en-US"],
            design_tokens={"theme": "system", "density": "comfortable"},
            features={
                "declarative_plugins": True,
                "custom_iframe_plugins": False,
                "policy_gated_actions": True,
            },
            trace_id=_trace_id(request),
        )

    @app.get(
        "/api/v1/ui/contributions",
        response_model=UIContributionsResponse,
        tags=["ui"],
    )
    async def ui_contributions(
        request: Request,
        workspace_id: Annotated[UUID | None, Query()] = None,
        _workspace_header: Annotated[str | None, Header(alias="X-Workspace-Id")] = None,
    ) -> UIContributionsResponse:
        # Phase 1 skeleton intentionally has no catalog repository.  Returning
        # an empty, valid page gives GUI clients a safe first render and keeps
        # this route independent from private database tables.
        return UIContributionsResponse(
            workspace_id=workspace_id,
            items=[],
            next_cursor=None,
            trace_id=_trace_id(request),
        )

    @app.get("/api/v1/ui/tenant-context", response_model=UITenantContextResponse, tags=["ui"])
    async def ui_tenant_context(
        request: Request,
        tenant_slug: Annotated[str, Header(alias="X-Tenant-Slug", min_length=1, max_length=120)] = "local-dev",
    ) -> UITenantContextResponse:
        """Resolve an explicitly selected development tenant for the GUI shell."""

        try:
            with psycopg2.connect(config.database_url) as connection:
                with connection.cursor() as cur:
                    cur.execute("SELECT id, slug FROM iam.tenants WHERE slug=%s", (tenant_slug,))
                    row = cur.fetchone()
                    if row is None:
                        raise HTTPException(status_code=404, detail="tenant not found")
        except HTTPException:
            raise
        except psycopg2.Error as exc:
            raise HTTPException(status_code=503, detail="tenant store unavailable") from exc
        return UITenantContextResponse(tenant_id=row[0], tenant_slug=row[1], trace_id=_trace_id(request))

    def evaluate_policy(
        payload: PolicyEvaluateRequest,
        tenant_id: UUID,
        trace_id: UUID,
        *,
        persist: bool,
        idempotency_key: str | None = None,
    ) -> PolicyDecisionResponse:
        """Evaluate through the DB-backed policy snapshot.

        This is the only API helper allowed to create a tool-call decision.
        Plugin routes must call this gateway instead of executing capabilities
        directly.  A transaction-local RLS tenant context is set before all
        tenant-scoped reads/writes.
        """

        argument_hash = _argument_hash(payload.arguments)
        try:
            with psycopg2.connect(config.database_url) as connection:
                _, cur = _tenant_context(connection, tenant_id)
                if persist and idempotency_key is not None:
                    cur.execute(
                        "SELECT request_hash,response_status,response_json FROM control.idempotency_records "
                        "WHERE tenant_id=%s AND idempotency_key=%s FOR UPDATE",
                        (tenant_id, idempotency_key),
                    )
                    previous = cur.fetchone()
                    request_hash = hashlib.sha256(
                        f"{payload.model_dump_json()}:{tenant_id}".encode("utf-8")
                    ).hexdigest()
                    if previous is not None:
                        if previous[0] != request_hash:
                            raise HTTPException(status_code=409, detail="Idempotency-Key reused with different request")
                        # Gateway decisions are stored with response_status=200
                        # and a PolicyDecisionResponse body.  The business layer
                        # (e.g. the topology service) records its completed
                        # response under the same key with status 201 and a
                        # payload that is NOT a PolicyDecisionResponse; replaying
                        # that projection here would be a type confusion.  For a
                        # 201 row, evaluate a fresh decision but do not persist a
                        # new tool call and leave the shared record untouched so
                        # business-layer replay keeps returning the same batch.
                        if previous[1] == 200 and previous[2] is not None:
                            return PolicyDecisionResponse.model_validate(previous[2])
                        if previous[1] == 201:
                            persist = False
                    else:
                        cur.execute(
                            "INSERT INTO control.idempotency_records(tenant_id,idempotency_key,request_hash) VALUES(%s,%s,%s)",
                            (tenant_id, idempotency_key, request_hash),
                        )

                rules, policy_version = _load_policy(cur, tenant_id)
                result = PolicyEngine(rules=rules, auto=config.policy_auto_enabled).evaluate(
                    payload.capability,
                    payload.arguments,
                    payload.risk_class,
                    side_effects=payload.side_effects,
                )
                now = datetime.now(timezone.utc)
                tool_call_id = uuid4()
                decision_id = uuid4()
                matched_ids = _matched_rule_uuid(result)
                decision_name = _decision_name(result.decision)
                if persist:
                    cur.execute(
                        """
                        INSERT INTO policy.tool_calls
                          (id,tenant_id,capability,arguments,argument_hash,trace_id)
                        VALUES(%s,%s,%s,%s,%s,%s)
                        """,
                        (tool_call_id, tenant_id, payload.capability, Json(payload.arguments), argument_hash, str(trace_id)),
                    )
                    cur.execute(
                        """
                        INSERT INTO policy.decisions
                          (id,tenant_id,tool_call_id,decision,risk_score,reason,matched_rule_ids)
                        VALUES(%s,%s,%s,%s,%s,%s,%s)
                        """,
                        (decision_id, tenant_id, tool_call_id, decision_name, result.risk_score, result.reason, matched_ids),
                    )
                    if decision_name == "REQUIRE_APPROVAL":
                        cur.execute(
                            """
                            INSERT INTO control.approvals
                              (tenant_id,tool_call_id,capability,argument_hash,risk_class)
                            VALUES(%s,%s,%s,%s,%s)
                            ON CONFLICT (tenant_id,tool_call_id) DO NOTHING
                            """,
                            (tenant_id, tool_call_id, payload.capability, argument_hash, payload.risk_class),
                        )
                response = PolicyDecisionResponse(
                    decision_id=decision_id,
                    tenant_id=tenant_id,
                    tool_call_id=tool_call_id,
                    policy_version=policy_version,
                    decision=decision_name,
                    risk_score=result.risk_score,
                    reason=result.reason,
                    matched_rule_ids=matched_ids,
                    simulation=not persist,
                    decided_at=now,
                    trace_id=trace_id,
                )
                if persist and idempotency_key is not None:
                    cur.execute(
                        "UPDATE control.idempotency_records SET response_status=200,response_json=%s WHERE tenant_id=%s AND idempotency_key=%s",
                        (Json(response.model_dump(mode="json")), tenant_id, idempotency_key),
                    )
                cur.close()
                return response
        except HTTPException:
            raise
        except psycopg2.Error as exc:
            raise HTTPException(status_code=503, detail="policy store unavailable") from exc

    def require_policy(
        capability: str,
        tenant_id: UUID,
        trace_id: UUID,
        *,
        risk_class: Literal["read_only", "low", "medium", "high", "critical"] = "high",
        side_effects: str = "write_data",
        arguments: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> PolicyDecisionResponse:
        """Fail closed for mutating routes that are not policy-approved.

        When a caller supplies an ``Idempotency-Key``, the durable gateway
        decision is bound to that key so a replayed request with different
        arguments is rejected with 409 instead of silently re-executing.
        """

        decision = evaluate_policy(
            PolicyEvaluateRequest(
                capability=capability,
                arguments=arguments or {},
                risk_class=risk_class,
                side_effects=side_effects,
            ),
            tenant_id,
            trace_id,
            # Route-level checks guard real operations.  Persist their tool
            # call and decision so an ALLOW is as auditable as a denial or an
            # approval request; only /policy/simulate remains non-durable.
            persist=True,
            idempotency_key=idempotency_key,
        )
        if decision.decision != "ALLOW":
            status = 409 if decision.decision == "REQUIRE_APPROVAL" else 403
            raise HTTPException(
                status_code=status,
                detail={"message": "policy gateway blocked capability", "decision": decision.model_dump(mode="json")},
            )
        return decision

    def tenant_slug(tenant_id: UUID) -> str:
        try:
            with psycopg2.connect(config.database_url) as connection:
                with connection.cursor() as cur:
                    cur.execute("SELECT slug FROM iam.tenants WHERE id=%s", (tenant_id,))
                    row = cur.fetchone()
                    if row is None:
                        raise HTTPException(status_code=404, detail="tenant not found")
                    return str(row[0])
        except HTTPException:
            raise
        except psycopg2.Error as exc:
            raise HTTPException(status_code=503, detail="tenant store unavailable") from exc

    def rich_media_read_roots(tenant_id: UUID) -> tuple[Path, ...]:
        """Declared roots the MinerU adapter may read: the drop root plus registered sources."""

        roots: set[Path] = {default_drop_root().resolve()}
        try:
            with psycopg2.connect(config.database_url) as connection:
                with connection.cursor() as cur:
                    cur.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),))
                    cur.execute(
                        "SELECT root_uri FROM knowledge.ingest_sources WHERE tenant_id=%s AND status='active'",
                        (tenant_id,),
                    )
                    for (root_uri,) in cur.fetchall():
                        parsed = urlparse(str(root_uri))
                        if parsed.scheme != "file":
                            continue
                        raw = unquote(parsed.path)
                        if raw.startswith("/") and len(raw) >= 3 and raw[2] == ":":
                            raw = raw[1:]
                        roots.add(Path(raw).resolve())
        except psycopg2.Error:
            pass
        return tuple(sorted(roots))

    def verified_catalog_version_exists(
        *, tenant_id: UUID, plugin_id: str, version: str, entrypoint: str
    ) -> bool:
        """Require explicit catalog publication before a verified binary can run.

        The filesystem binding establishes which code is trusted; this catalog
        lookup establishes that the tenant has deliberately published that
        exact read-only runtime.  It is intentionally not an auto-register
        path, so an API request can never activate a plugin by itself.
        """

        try:
            with psycopg2.connect(config.database_url) as connection:
                _, cur = _tenant_context(connection, tenant_id)
                cur.execute(
                    """
                    SELECT 1
                    FROM catalog.plugin_versions plugin_version
                    JOIN catalog.plugins plugin ON plugin.id=plugin_version.plugin_id
                    WHERE plugin.tenant_id=%s
                      AND plugin.key=%s
                      AND plugin.status='active'
                      AND plugin_version.version=%s
                      AND plugin_version.status='published'
                      AND plugin_version.runtime='python'
                      AND plugin_version.entrypoint=%s
                      AND plugin_version.side_effect_class='read_only'
                    """,
                    (tenant_id, plugin_id, version, entrypoint),
                )
                registered = cur.fetchone() is not None
                cur.close()
                return registered
        except HTTPException:
            raise
        except psycopg2.Error as exc:
            raise HTTPException(status_code=503, detail="plugin catalog unavailable") from exc

    @app.post("/api/v1/policy/evaluate", response_model=PolicyDecisionResponse, tags=["policy"])
    async def policy_evaluate(
        payload: PolicyEvaluateRequest,
        request: Request,
        tenant_id: Annotated[UUID, Header(alias="X-Tenant-Id")],
        idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=255)],
    ) -> PolicyDecisionResponse:
        return evaluate_policy(payload, tenant_id, _trace_id(request), persist=True, idempotency_key=idempotency_key)

    @app.post("/api/v1/policy/simulate", response_model=PolicyDecisionResponse, tags=["policy"])
    async def policy_simulate(
        payload: PolicyEvaluateRequest,
        request: Request,
        tenant_id: Annotated[UUID, Header(alias="X-Tenant-Id")],
    ) -> PolicyDecisionResponse:
        return evaluate_policy(payload, tenant_id, _trace_id(request), persist=False)

    @app.post("/api/v1/plugins/invoke", response_model=PluginInvokeResponse, tags=["plugins"])
    async def invoke_verified_plugin(
        payload: PluginInvokeRequest,
        request: Request,
        tenant_id: Annotated[UUID, Header(alias="X-Tenant-Id")],
        idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=255)],
    ) -> PluginInvokeResponse:
        """Invoke the first verified builtin through catalog, policy and isolation gates.

        This endpoint intentionally has no arbitrary command, path or
        entrypoint parameter.  It first verifies a hard-coded builtin binding,
        checks explicit tenant publication, writes an auditable policy decision,
        then starts a one-shot local child process with only declared roots.
        """

        trace_id = _trace_id(request)
        if payload.artifact is not None:
            input_artifact = payload.artifact
        elif payload.rca is not None:
            if (
                payload.rca.incident_set.tenant_id != tenant_id
                or payload.rca.topology_graph.tenant_id != tenant_id
            ):
                raise HTTPException(status_code=403, detail="artifact tenant does not match request tenant")
            input_artifact = payload.rca.incident_set
        elif payload.ledger is not None:
            input_artifact = payload.ledger.artifact
        elif payload.journal is not None:
            input_artifact = payload.journal.artifact
        elif payload.document is not None:
            input_artifact = payload.document.artifact
        elif payload.snapshot is not None:
            input_artifact = payload.snapshot.artifact
        elif payload.dataset is not None:
            input_artifact = payload.dataset.artifact
        elif payload.proposal is not None:
            input_artifact = payload.proposal.candidate_set
            if payload.proposal.graph_snapshot is not None and payload.proposal.graph_snapshot.tenant_id != tenant_id:
                raise HTTPException(status_code=403, detail="graph snapshot tenant does not match request tenant")
        elif payload.investigation is not None:
            input_artifact = payload.investigation.anomaly_candidates
            if payload.investigation.evidence_index is not None and payload.investigation.evidence_index.tenant_id != tenant_id:
                raise HTTPException(status_code=403, detail="evidence index tenant does not match request tenant")
        elif payload.finding is not None:
            input_artifact = payload.finding.anomaly_candidates
            if payload.finding.investigation_plan is not None and payload.finding.investigation_plan.tenant_id != tenant_id:
                raise HTTPException(status_code=403, detail="investigation plan tenant does not match request tenant")
        elif payload.experiment is not None:
            input_artifact = payload.experiment.report
            if payload.experiment.baseline is not None and payload.experiment.baseline.tenant_id != tenant_id:
                raise HTTPException(status_code=403, detail="baseline report tenant does not match request tenant")
        elif payload.remediation is not None:
            input_artifact = payload.remediation.rca_candidates
        elif payload.recovery is not None:
            input_artifact = payload.recovery.observed
            if payload.recovery.baseline.tenant_id != tenant_id:
                raise HTTPException(status_code=403, detail="baseline series tenant does not match request tenant")
            if payload.recovery.proposal is not None and payload.recovery.proposal.tenant_id != tenant_id:
                raise HTTPException(status_code=403, detail="proposal tenant does not match request tenant")
        elif payload.triage is not None:
            input_artifact = payload.triage.alert_event
        elif payload.lineage is not None:
            input_artifact = payload.lineage.graph_ref.artifact
        elif payload.backtest is not None:
            input_artifact = payload.backtest.snapshot_ref.artifact
        elif payload.workpaper is not None:
            input_artifact = payload.workpaper.finding_set
        elif payload.report is not None:
            input_artifact = payload.report.finding_set
        elif payload.ticket is not None:
            input_artifact = payload.ticket.incident_proposal
        elif payload.retention is not None:
            input_artifact = payload.retention.document
        elif payload.postmortem is not None:
            input_artifact = payload.postmortem.incident_proposal
            if payload.postmortem.verification is not None and payload.postmortem.verification.tenant_id != tenant_id:
                raise HTTPException(status_code=403, detail="verification artifact tenant does not match request tenant")
        elif payload.research_note is not None:
            input_artifact = payload.research_note.evaluation
        else:
            assert payload.alerts is not None
            input_artifact = payload.alerts.artifact
        if input_artifact.tenant_id != tenant_id:
            raise HTTPException(status_code=403, detail="artifact tenant does not match request tenant")
        try:
            binding = load_verified_binding(payload.plugin_id)
        except PluginRuntimeError as exc:
            raise HTTPException(status_code=404, detail="verified builtin plugin not found") from exc
        if payload.capability != binding.capability:
            raise HTTPException(status_code=400, detail="capability is not declared by the verified plugin")
        if not verified_catalog_version_exists(
            tenant_id=tenant_id,
            plugin_id=binding.plugin_id,
            version=binding.version,
            entrypoint=binding.entrypoint,
        ):
            raise HTTPException(status_code=409, detail="verified plugin has not been registered and published")

        decision = evaluate_policy(
            PolicyEvaluateRequest(
                capability=payload.capability,
                arguments={
                    "plugin_id": binding.plugin_id,
                    "plugin_version": binding.version,
                    "artifact_sha256": input_artifact.sha256.lower(),
                    "max_characters": payload.max_characters,
                },
                risk_class="read_only",
                side_effects="read_only",
            ),
            tenant_id,
            trace_id,
            persist=True,
            idempotency_key=idempotency_key,
        )
        if decision.decision != "ALLOW":
            status = 409 if decision.decision == "REQUIRE_APPROVAL" else 403
            raise HTTPException(
                status_code=status,
                detail={"message": "policy gateway blocked capability", "decision": decision.model_dump(mode="json")},
            )

        artifact = ArtifactInput(
            artifact_id=input_artifact.artifact_id,
            tenant_id=input_artifact.tenant_id,
            uri=input_artifact.uri,
            media_type=input_artifact.media_type,
            sha256=input_artifact.sha256.lower(),
            size_bytes=input_artifact.size_bytes,
            classification=input_artifact.classification,
        )
        if payload.artifact is not None:
            runtime_payload: dict[str, Any] = {
                "artifact": artifact.as_payload(),
                "max_characters": payload.max_characters,
            }
        elif payload.ledger is not None:
            runtime_payload = {"ledger": payload.ledger.model_dump(mode="json")}
        elif payload.journal is not None:
            runtime_payload = {"journal": payload.journal.model_dump(mode="json")}
        elif payload.document is not None:
            runtime_payload = {"document": payload.document.model_dump(mode="json")}
        elif payload.snapshot is not None:
            runtime_payload = {"snapshot": payload.snapshot.model_dump(mode="json")}
        elif payload.dataset is not None:
            runtime_payload = {"dataset": payload.dataset.model_dump(mode="json")}
        elif payload.alerts is not None:
            runtime_payload = {"alerts": payload.alerts.model_dump(mode="json")}
        elif payload.triage is not None:
            runtime_payload = {"triage": payload.triage.model_dump(mode="json")}
        elif payload.lineage is not None:
            runtime_payload = {"lineage": payload.lineage.model_dump(mode="json")}
        elif payload.backtest is not None:
            runtime_payload = {"backtest": payload.backtest.model_dump(mode="json")}
        elif payload.proposal is not None:
            runtime_payload = {"proposal": payload.proposal.model_dump(mode="json")}
        elif payload.investigation is not None:
            runtime_payload = {"investigation": payload.investigation.model_dump(mode="json")}
        elif payload.finding is not None:
            runtime_payload = {"finding": payload.finding.model_dump(mode="json")}
        elif payload.experiment is not None:
            runtime_payload = {"experiment": payload.experiment.model_dump(mode="json")}
        elif payload.remediation is not None:
            runtime_payload = {"remediation": payload.remediation.model_dump(mode="json")}
        elif payload.recovery is not None:
            runtime_payload = {"recovery": payload.recovery.model_dump(mode="json")}
        elif payload.workpaper is not None:
            runtime_payload = {"workpaper": payload.workpaper.model_dump(mode="json")}
        elif payload.report is not None:
            runtime_payload = {"report": payload.report.model_dump(mode="json")}
        elif payload.ticket is not None:
            runtime_payload = {"ticket": payload.ticket.model_dump(mode="json")}
        elif payload.retention is not None:
            runtime_payload = {"retention": payload.retention.model_dump(mode="json")}
        elif payload.postmortem is not None:
            runtime_payload = {"postmortem": payload.postmortem.model_dump(mode="json")}
        elif payload.research_note is not None:
            runtime_payload = {"research_note": payload.research_note.model_dump(mode="json")}
        else:
            assert payload.rca is not None
            runtime_payload = {"rca": payload.rca.model_dump(mode="json")}
        try:
            result = IsolatedPluginRuntime(allowed_roots=config.plugin_read_roots).invoke(
                PluginInvocation(
                    tenant_id=tenant_id,
                    trace_id=trace_id,
                    idempotency_key=idempotency_key,
                    plugin_id=binding.plugin_id,
                    capability=payload.capability,
                    payload=runtime_payload,
                ),
                # The durable database decision above is the authoritative
                # gateway.  This second deterministic gate preserves the
                # runtime invariant that no direct caller can start a child
                # without an explicit allow decision.
                PolicyEngine(allow=[payload.capability]),
            )
        except PluginPolicyDenied as exc:
            raise HTTPException(status_code=403, detail="policy gateway blocked capability") from exc
        except PluginRuntimeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return PluginInvokeResponse(
            plugin_id=result.plugin_id,
            plugin_version=result.plugin_version,
            capability=result.capability,
            policy_decision_id=decision.decision_id,
            input_sha256=result.input_sha256,
            runtime_code_sha256=result.runtime_code_sha256,
            document=result.output,
            trace_id=trace_id,
        )

    @app.get("/api/v1/plugins/verified", response_model=VerifiedPluginsResponse, tags=["plugins"])
    async def verified_plugins(
        request: Request,
        tenant_id: Annotated[UUID, Header(alias="X-Tenant-Id")],
    ) -> VerifiedPluginsResponse:
        """Expose verified-runtime metadata for the desktop; never an executor.

        Only the fixed verified builtins are listed.  This lets the desktop
        distinguish the real constrained runtimes from the M0 planning
        blueprints without enumerating arbitrary filesystem plugins.
        """

        trace_id = _trace_id(request)
        items: list[VerifiedPluginSummary] = []
        try:
            for plugin_id in verified_builtin_ids():
                binding = load_verified_binding(plugin_id)
                registered = verified_catalog_version_exists(
                    tenant_id=tenant_id,
                    plugin_id=binding.plugin_id,
                    version=binding.version,
                    entrypoint=binding.entrypoint,
                )
                items.append(
                    VerifiedPluginSummary(
                        plugin_id=binding.plugin_id,
                        plugin_version=binding.version,
                        capability=binding.capability,
                        execution_mode="isolated_subprocess",
                        catalog_registered=registered,
                        side_effects="read_only",
                        trace_id=trace_id,
                    )
                )
        except PluginRuntimeError as exc:
            raise HTTPException(status_code=503, detail="verified plugin binding is unavailable") from exc
        return VerifiedPluginsResponse(items=items, trace_id=trace_id)

    @app.get("/api/v1/plugins/lifecycle", response_model=PluginLifecycleResponse, tags=["plugins"])
    async def plugins_lifecycle(
        request: Request,
        tenant_id: Annotated[UUID, Header(alias="X-Tenant-Id")],
        domain: Annotated[str | None, Query(min_length=1, max_length=64)] = None,
        lifecycle: Annotated[str | None, Query(pattern=r"^(verified|contract_only)$")] = None,
        conflict_only: bool = False,
        limit: Annotated[int, Query(ge=1, le=500)] = 200,
    ) -> PluginLifecycleResponse:
        """Declared lifecycle vs the executable allow list; read-only.

        The two are supposed to agree.  Where they do not, the disagreement is
        returned as ``conflict`` rather than resolved silently, and ``summary``
        always counts the whole corpus so a filtered view cannot make the
        directory look smaller than it is.  This route never changes a lifecycle:
        the allow list is code, not data, and the UI must not imply otherwise.
        """

        trace_id = _trace_id(request)
        require_policy(
            "plugin.lifecycle.read", tenant_id, trace_id, risk_class="read_only", side_effects="read_only"
        )
        try:
            view = plugin_lifecycle(
                config.database_url,
                tenant_slug=tenant_slug(tenant_id),
                domain=domain,
                lifecycle=lifecycle,
                conflict_only=conflict_only,
            )
        except psycopg2.Error as exc:
            raise HTTPException(status_code=503, detail="plugin catalog unavailable") from exc
        return PluginLifecycleResponse(
            summary=PluginLifecycleSummary(**view["summary"]),
            items=[PluginLifecycleItem(**item) for item in view["items"][:limit]],
            trace_id=trace_id,
        )

    @app.get("/api/v1/connectivity/report", response_model=ConnectivityReportResponse, tags=["topology"])
    async def connectivity_report_view(
        request: Request,
        tenant_id: Annotated[UUID, Header(alias="X-Tenant-Id")],
        lifecycle: Annotated[str | None, Query()] = None,
        include_invokes: Annotated[bool, Query()] = False,
    ) -> ConnectivityReportResponse:
        """Plugin-network connectivity and supply/demand closure; read-only.

        Answers "is the network actually wired, and who is dangling".  The
        numbers come from ``packages/catalog/connectivity_view.py`` — the *same*
        implementation the offline script uses, so the dashboard and the CI
        baseline gate can never disagree about the same network.

        ``lifecycle`` / ``include_invokes`` are **口径 switches, not filters**:
        figures from different scopes are not comparable, so the scope travels
        with the result and the UI is expected to show it.  This route executes
        no plugin, writes nothing and edits no plan; it is a filesystem
        projection (plugin directory + reviewed semantics catalog).  A read is
        still an access, so it goes through the policy gateway like every other
        route.
        """

        trace_id = _trace_id(request)
        require_policy(
            "connectivity.report.read", tenant_id, trace_id,
            risk_class="read_only", side_effects="read_only",
        )
        try:
            payload = connectivity_report(lifecycle=lifecycle, include_invokes=include_invokes)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        islands = payload["islands"]
        return ConnectivityReportResponse(
            scope=ConnectivityScopeModel(**payload["scope"]),
            metrics=ConnectivityMetricsModel(**payload["metrics"]),
            islands_total=islands["total"],
            islands_present=[ConnectivityIslandCategory(**entry) for entry in islands["present"]],
            islands=[ConnectivityIsland(**item) for item in islands["items"]],
            trace_id=trace_id,
        )

    @app.get("/api/v1/rules/registry", response_model=RulesRegistryResponse, tags=["rules"])
    async def rules_registry_view(
        request: Request,
        tenant_id: Annotated[UUID, Header(alias="X-Tenant-Id")],
    ) -> RulesRegistryResponse:
        """What the system runs by: contracts, policy sets, templates, skills; read-only.

        Contracts, report templates and skills are a filesystem projection of
        ``contracts/`` and ``plugins/builtin/``; policy sets are read straight
        from ``policy.policy_sets`` so the database stays their single source of
        truth.  A missing on-disk root is named in ``missing_roots`` rather than
        hidden.  This route never edits a contract, policy, template or skill.
        """

        trace_id = _trace_id(request)
        require_policy(
            "rules.registry.read", tenant_id, trace_id, risk_class="read_only", side_effects="read_only"
        )
        view = rules_registry(_project_root())

        policy_items: list[PolicySetItem] = []
        try:
            with psycopg2.connect(config.database_url) as connection:
                _, cur = _tenant_context(connection, tenant_id)
                cur.execute(
                    "SELECT name, version::text, status, "
                    "COALESCE(jsonb_array_length(rules), 0), created_at "
                    "FROM policy.policy_sets WHERE tenant_id=%s AND status='active' "
                    "ORDER BY created_at ASC, id ASC",
                    (tenant_id,),
                )
                for row_name, row_version, row_status, row_rules, row_created in cur.fetchall():
                    policy_items.append(
                        PolicySetItem(
                            name=str(row_name),
                            version=str(row_version),
                            status=str(row_status),
                            rules_count=int(row_rules),
                            created_at=row_created.isoformat() if row_created is not None else None,
                        )
                    )
        except psycopg2.Error as exc:
            raise HTTPException(status_code=503, detail="policy set store unavailable") from exc

        summary = RulesRegistrySummary(
            contracts=view["summary"]["contracts"],
            skills=view["summary"]["skills"],
            report_templates=view["summary"]["report_templates"],
            policy_sets=len(policy_items),
            missing_roots=view["summary"]["missing_roots"],
        )
        return RulesRegistryResponse(
            summary=summary,
            contracts=[RuleItem(**item) for item in view["contracts"]],
            policies=policy_items,
            report_templates=[RuleItem(**item) for item in view["report_templates"]],
            skills=[RuleItem(**item) for item in view["skills"]],
            trace_id=trace_id,
        )

    @app.get("/api/v1/plugins/versions", response_model=PluginVersionsResponse, tags=["plugins"])
    async def plugin_versions_view(
        request: Request,
        tenant_id: Annotated[UUID, Header(alias="X-Tenant-Id")],
        plugin_id: Annotated[str, Query(min_length=1, max_length=128)],
    ) -> PluginVersionsResponse:
        """Every published version row for one plugin, newest first; read-only.

        ``/plugins/lifecycle`` collapses to the newest row because that is what a
        new run resolves; this route exists for the lineage view, where the
        question is exactly "how did this plugin's descriptors accumulate".  The
        dotted id is resolved against ``catalog.plugins.key``; an unknown plugin
        returns an empty list, never an exception.
        """

        trace_id = _trace_id(request)
        require_policy(
            "plugin.versions.read", tenant_id, trace_id, risk_class="read_only", side_effects="read_only",
            arguments={"plugin_id": plugin_id},
        )
        try:
            rows = plugin_version_history(
                config.database_url,
                tenant_slug=tenant_slug(tenant_id),
                plugin_id=plugin_id,
            )
        except psycopg2.Error as exc:
            raise HTTPException(status_code=503, detail="plugin catalog unavailable") from exc
        return PluginVersionsResponse(
            plugin_id=plugin_id,
            items=[PluginVersionRow(**row) for row in rows],
            trace_id=trace_id,
        )

    @app.get("/api/v1/observability/runs", response_model=RunIndexResponse, tags=["observability"])
    async def observability_runs(
        request: Request,
        tenant_id: Annotated[UUID, Header(alias="X-Tenant-Id")],
        limit: Annotated[int, Query(ge=1, le=1000)] = 200,
    ) -> RunIndexResponse:
        """Evidence bundles on disk joined to their run records; read-only.

        Listing never re-hashes a bundle: verification is a separate, explicit
        action (``/observability/bundles/{run_id}/verify``) because it is the
        claim being made and it should cost what it costs.  A run with no project
        anchor reports ``archive_state: unlinked`` — blank would read as a
        different, false statement.
        """

        trace_id = _trace_id(request)
        require_policy(
            "observability.runs.read", tenant_id, trace_id, risk_class="read_only", side_effects="read_only"
        )
        view = build_run_index(
            config.database_url,
            tenant_slug(tenant_id),
            _project_root(),
            limit=limit,
        )
        return RunIndexResponse(
            summary=RunIndexSummary(**view["summary"]),
            items=[RunItem(**item) for item in view["items"]],
            orphan_bundles=view["orphan_bundles"],
            trace_id=trace_id,
        )

    @app.get(
        "/api/v1/observability/bundles/{run_id}/verify",
        response_model=BundleVerifyResponse,
        tags=["observability"],
    )
    async def observability_bundle_verify(
        run_id: str,
        request: Request,
        tenant_id: Annotated[UUID, Header(alias="X-Tenant-Id")],
    ) -> BundleVerifyResponse:
        """Recompute every manifest sha256 of one bundle; the explicit action.

        ``run_id`` is untrusted input: it must parse as a UUID and the resolved
        path must stay inside the declared roots.  A missing or corrupt bundle is
        an explicit not-found/not-ok record, never an exception the caller could
        read as "probably fine".
        """

        trace_id = _trace_id(request)
        require_policy(
            "observability.evidence.verify",
            tenant_id,
            trace_id,
            risk_class="read_only",
            side_effects="read_only",
            arguments={"run_id": run_id},
        )
        result = verify_bundle(run_id, _project_root())
        return BundleVerifyResponse(trace_id=trace_id, **result)

    @app.get("/api/v1/observability/failures", response_model=FailureDiagnosticsResponse, tags=["observability"])
    async def observability_failures(
        request: Request,
        tenant_id: Annotated[UUID, Header(alias="X-Tenant-Id")],
        recent_days: Annotated[int, Query(ge=0, le=3650)] = 7,
        limit: Annotated[int, Query(ge=1, le=2000)] = 200,
    ) -> FailureDiagnosticsResponse:
        """Failed attempts clustered by normalised signature; read-only.

        ``error_kind`` alone is too coarse to act on; the unit here is the
        signature — the first message line with paths, uuids and hashes erased —
        because "this keeps happening" is only answerable at that grain.  Sample
        messages are control-character-stripped and truncated; the interface
        escapes what remains.
        """

        trace_id = _trace_id(request)
        require_policy(
            "observability.failures.read", tenant_id, trace_id, risk_class="read_only", side_effects="read_only"
        )
        view = failure_diagnostics(
            config.database_url,
            tenant_slug(tenant_id),
            recent_days=recent_days,
            limit=limit,
        )
        return FailureDiagnosticsResponse(
            totals=FailureTotals(**view["totals"]),
            summary=FailureSummary(**view["summary"]),
            items=[FailureGroup(**item) for item in view["items"]],
            trace_id=trace_id,
        )

    @app.get("/api/v1/experience/evolution", response_model=EvolutionResponse, tags=["experience"])
    async def experience_evolution(
        request: Request,
        tenant_id: Annotated[UUID, Header(alias="X-Tenant-Id")],
    ) -> EvolutionResponse:
        """The seven-ring learning loop with its deciding evidence; read-only.

        Ring 2 (projector wiring) and ring 7 (planner read path) are code facts,
        checked by parsing the real modules rather than by searching text — a
        docstring mentioning "knowledge" is not integration.  Every status names
        the query or parse that produced it; the loop is only as strong as its
        weakest ring and the response says which one that is.
        """

        trace_id = _trace_id(request)
        require_policy(
            "experience.evolution.read", tenant_id, trace_id, risk_class="read_only", side_effects="read_only"
        )
        view = evolution_status(config.database_url, tenant_slug(tenant_id), _project_root())
        return EvolutionResponse(
            rings=[EvolutionRing(**ring) for ring in view["rings"]],
            weakest=view["weakest"],
            summary=EvolutionSummary(**view["summary"]),
            trace_id=trace_id,
        )

    @app.get("/api/v1/search", response_model=SearchResponse, tags=["search"])
    async def unified_search(
        request: Request,
        tenant_id: Annotated[UUID, Header(alias="X-Tenant-Id")],
        q: Annotated[str, Query(min_length=1, max_length=200)],
        kinds: Annotated[str | None, Query(description="comma-separated subset of document,run,artifact,suggestion")] = None,
        limit: Annotated[int, Query(ge=1, le=50)] = 20,
    ) -> SearchResponse:
        """Unified cross-kind search over documents, runs, artifacts and suggestions; read-only.

        Each source is ranked on its own and the rankings are fused with the
        same reciprocal-rank formula used inside the knowledge layer -- no new
        weights.  When the embedder cannot be reached the document group falls
        back to keyword ranking and ``degraded.vector`` is ``True``; the response
        never pretends vector search ran.  An empty hit list is a normal answer.
        """

        trace_id = _trace_id(request)
        require_policy("search.read", tenant_id, trace_id, risk_class="read_only", side_effects="read_only")
        parsed_kinds: set[str] | None = None
        if kinds:
            parsed_kinds = {piece.strip() for piece in kinds.split(",") if piece.strip()}
        try:
            view = search_all(
                config.database_url,
                tenant_slug(tenant_id),
                _project_root(),
                q,
                kinds=parsed_kinds,
                limit=limit,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return SearchResponse(
            items=[SearchItem(**item) for item in view["items"]],
            degraded=SearchDegraded(**view["degraded"]),
            trace_id=trace_id,
        )

    @app.get("/api/v1/library/index", response_model=LibraryIndexResponse, tags=["library"])
    async def library_index_view(
        request: Request,
        tenant_id: Annotated[UUID, Header(alias="X-Tenant-Id")],
        kind: Annotated[
            str | None,
            Query(pattern=r"^(方案|报告|评估|清单|契约|技能|案例资料)$"),
        ] = None,
        project: Annotated[str | None, Query(min_length=1, max_length=128)] = None,
        since: Annotated[str | None, Query(description="ISO date or datetime; inclusive lower bound")] = None,
        q: Annotated[str | None, Query(min_length=1, max_length=200)] = None,
    ) -> LibraryIndexResponse:
        """Path/title/kind/size index of the markdown assets; read-only.

        This is a filesystem projection, not a second source of truth: it never
        opens document bodies for full-text search (that is the knowledge layer's
        job) and never moves or renames a file.  ``summary`` always counts the
        whole corpus, so a filter cannot make the library look smaller.
        """

        trace_id = _trace_id(request)
        require_policy("library.index.read", tenant_id, trace_id, risk_class="read_only", side_effects="read_only")
        view = library_index(_project_root(), kind=kind, project=project, since=since, q=q)
        return LibraryIndexResponse(
            summary=LibrarySummary(**view["summary"]),
            items=[LibraryItem(**item) for item in view["items"]],
            trace_id=trace_id,
        )

    @app.get("/api/v1/cases", response_model=CasesResponse, tags=["cases"])
    async def cases_matrix_view(
        request: Request,
        tenant_id: Annotated[UUID, Header(alias="X-Tenant-Id")],
    ) -> CasesResponse:
        """Case x stage matrix over 审计项目案例报告效果展示/; read-only.

        The nine stages are the on-disk ``00_``…``08_`` directories and every
        case reports all nine, including zero-count stages.  The audit reports
        under each case are listed with their line/byte counts, the share of the
        result chapters and the v1.1 mandatory-section gate.
        """

        trace_id = _trace_id(request)
        require_policy("cases.read", tenant_id, trace_id, risk_class="read_only", side_effects="read_only")
        view = case_matrix(_project_root())
        return CasesResponse(
            cases=[CaseInfo(**case) for case in view["cases"]],
            trace_id=trace_id,
        )

    @app.get("/api/v1/approvals", response_model=ApprovalListResponse, tags=["policy"])
    async def approvals(
        request: Request,
        tenant_id: Annotated[UUID, Header(alias="X-Tenant-Id")],
        status: Annotated[str | None, Query(pattern=r"^(pending|approved|rejected|expired)$")] = None,
    ) -> ApprovalListResponse:
        try:
            with psycopg2.connect(config.database_url) as connection:
                _, cur = _tenant_context(connection, tenant_id)
                if status is None:
                    cur.execute(
                        "SELECT id,tool_call_id,capability,argument_hash,risk_class,status,reason,expires_at,created_at,decided_at "
                        "FROM control.approvals WHERE tenant_id=%s ORDER BY created_at DESC LIMIT 200",
                        (tenant_id,),
                    )
                else:
                    cur.execute(
                        "SELECT id,tool_call_id,capability,argument_hash,risk_class,status,reason,expires_at,created_at,decided_at "
                        "FROM control.approvals WHERE tenant_id=%s AND status=%s ORDER BY created_at DESC LIMIT 200",
                        (tenant_id, status),
                    )
                rows = cur.fetchall()
                cur.close()
        except psycopg2.Error as exc:
            raise HTTPException(status_code=503, detail="approval store unavailable") from exc
        return ApprovalListResponse(
            items=[
                ApprovalSummary(
                    id=row[0], tool_call_id=row[1], capability=row[2], argument_hash=row[3], risk_class=row[4],
                    status=row[5], reason=row[6], expires_at=row[7], created_at=row[8], decided_at=row[9],
                    trace_id=_trace_id(request),
                )
                for row in rows
            ],
            trace_id=_trace_id(request),
        )

    @app.post("/api/v1/approvals/{approval_id}/decide", response_model=ApprovalSummary, tags=["policy"])
    async def decide_approval(
        approval_id: UUID,
        payload: ApprovalDecisionRequest,
        request: Request,
        tenant_id: Annotated[UUID, Header(alias="X-Tenant-Id")],
        actor_id: Annotated[UUID, Header(alias="X-Actor-Id")],
        idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=255)],
    ) -> ApprovalSummary:
        try:
            with psycopg2.connect(config.database_url) as connection:
                _, cur = _tenant_context(connection, tenant_id)
                cur.execute(
                    "SELECT id FROM iam.principals WHERE tenant_id=%s AND id=%s AND status='active'",
                    (tenant_id, actor_id),
                )
                if cur.fetchone() is None:
                    raise HTTPException(status_code=403, detail="actor is not an active tenant principal")
                request_hash = hashlib.sha256(
                    f"{approval_id}:{payload.model_dump_json()}:{tenant_id}".encode("utf-8")
                ).hexdigest()
                cur.execute(
                    "SELECT request_hash,response_json FROM control.idempotency_records "
                    "WHERE tenant_id=%s AND idempotency_key=%s FOR UPDATE",
                    (tenant_id, idempotency_key),
                )
                previous = cur.fetchone()
                if previous is not None:
                    if previous[0] != request_hash:
                        raise HTTPException(status_code=409, detail="Idempotency-Key reused with different request")
                    if previous[1] is not None:
                        return ApprovalSummary.model_validate(previous[1])
                else:
                    cur.execute(
                        "INSERT INTO control.idempotency_records(tenant_id,idempotency_key,request_hash) VALUES(%s,%s,%s)",
                        (tenant_id, idempotency_key, request_hash),
                    )
                cur.execute(
                    "SELECT id,tool_call_id,capability,argument_hash,risk_class,status,reason,expires_at,created_at,decided_at "
                    "FROM control.approvals WHERE tenant_id=%s AND id=%s FOR UPDATE",
                    (tenant_id, approval_id),
                )
                row = cur.fetchone()
                if row is None:
                    raise HTTPException(status_code=404, detail="approval not found")
                if row[5] != "pending" or row[7] <= datetime.now(timezone.utc):
                    raise HTTPException(status_code=409, detail="approval is no longer pending")
                now = datetime.now(timezone.utc)
                new_status = payload.decision
                cur.execute(
                    "UPDATE control.approvals SET status=%s,reason=%s,decided_by=%s,decided_at=%s WHERE tenant_id=%s AND id=%s",
                    (new_status, payload.reason, actor_id, now, tenant_id, approval_id),
                )
                lease_id = None
                if new_status == "approved":
                    cur.execute(
                        """
                        INSERT INTO control.authorization_leases
                          (tenant_id,approval_id,tool_call_id,capability,argument_hash,expires_at)
                        VALUES(%s,%s,%s,%s,%s,%s) RETURNING id
                        """,
                        (tenant_id, approval_id, row[1], row[2], row[3], now + timedelta(seconds=payload.ttl_seconds)),
                    )
                    lease_id = cur.fetchone()[0]
                response = ApprovalSummary(
                    id=row[0], tool_call_id=row[1], capability=row[2], argument_hash=row[3], risk_class=row[4],
                    status=new_status, reason=payload.reason, expires_at=row[7], created_at=row[8], decided_at=now,
                    authorization_lease_id=lease_id, trace_id=_trace_id(request),
                )
                cur.execute(
                    "UPDATE control.idempotency_records SET response_status=200,response_json=%s "
                    "WHERE tenant_id=%s AND idempotency_key=%s",
                    (Json(response.model_dump(mode="json")), tenant_id, idempotency_key),
                )
                cur.close()
                return response
        except HTTPException:
            raise
        except psycopg2.Error as exc:
            raise HTTPException(status_code=503, detail="approval store unavailable") from exc

    @app.post("/api/v1/missions", response_model=MissionResponse, tags=["control"])
    async def create_mission(
        payload: MissionCreateRequest,
        request: Request,
        tenant_id: Annotated[UUID, Header(alias="X-Tenant-Id")],
        idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=255)],
    ) -> MissionResponse:
        require_policy("control.mission.create", tenant_id, _trace_id(request))
        try:
            mission_id = Scheduler(config.database_url, tenant_slug(tenant_id)).create_mission(
                payload.title, payload.objective, payload.domain, payload.project_slug,
                payload.autonomy_mode, payload.requested_by, idempotency_key,
            )
        except (ValueError, PermissionError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return MissionResponse(id=mission_id, trace_id=_trace_id(request))

    @app.post("/api/v1/workflows", response_model=WorkflowRunResponse, tags=["control"])
    async def create_workflow(
        payload: WorkflowCreateRequest,
        request: Request,
        tenant_id: Annotated[UUID, Header(alias="X-Tenant-Id")],
    ) -> WorkflowRunResponse:
        require_policy("control.workflow.create", tenant_id, _trace_id(request))
        try:
            workflow_id = Scheduler(config.database_url, tenant_slug(tenant_id)).create_workflow(
                payload.key, payload.name, payload.version, payload.graph, payload.created_by
            )
        except (ValueError, PermissionError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return WorkflowRunResponse(id=workflow_id, trace_id=_trace_id(request))

    @app.post("/api/v1/workflows/{workflow_key}/run", response_model=WorkflowRunResponse, tags=["control"])
    async def start_workflow_run(
        workflow_key: str,
        payload: WorkflowRunRequest,
        request: Request,
        tenant_id: Annotated[UUID, Header(alias="X-Tenant-Id")],
        idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=255)],
    ) -> WorkflowRunResponse:
        require_policy("control.workflow.run", tenant_id, _trace_id(request))
        try:
            run_id = Scheduler(config.database_url, tenant_slug(tenant_id)).start_run(
                payload.mission_id, workflow_key, payload.version, payload.input_payload,
                idempotency_key, _trace_id(request),
            )
        except (ValueError, PermissionError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return WorkflowRunResponse(id=run_id, trace_id=_trace_id(request))

    @app.get("/api/v1/workflow-runs/{workflow_run_id}/runnable-tasks", response_model=RunnableTasksResponse, tags=["control"])
    async def runnable_tasks(
        workflow_run_id: UUID,
        request: Request,
        tenant_id: Annotated[UUID, Header(alias="X-Tenant-Id")],
    ) -> RunnableTasksResponse:
        require_policy("control.task.read", tenant_id, _trace_id(request), risk_class="read_only", side_effects="read_only")
        try:
            tasks = Scheduler(config.database_url, tenant_slug(tenant_id)).runnable_tasks(workflow_run_id)
        except (ValueError, PermissionError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return RunnableTasksResponse(
            items=[RunnableTaskResponse(id=item.id, node_key=item.node_key, capability=item.capability,
                                        status=item.status, attempt=item.attempt, max_attempts=item.max_attempts)
                   for item in tasks],
            trace_id=_trace_id(request),
        )

    @app.post("/api/v1/task-runs/{task_run_id}/claim", response_model=AgentRunResponse, tags=["control"])
    async def claim_task(
        task_run_id: UUID,
        payload: TaskClaimRequest,
        request: Request,
        tenant_id: Annotated[UUID, Header(alias="X-Tenant-Id")],
        idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=255)],
    ) -> AgentRunResponse:
        require_policy("control.task.claim", tenant_id, _trace_id(request))
        try:
            agent = Scheduler(config.database_url, tenant_slug(tenant_id)).claim_task(
                task_run_id, payload.role_key, payload.model_key, idempotency_key, payload.principal_id
            )
        except (ValueError, PermissionError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return AgentRunResponse(id=agent.id, task_run_id=agent.task_run_id, status=agent.status, trace_id=_trace_id(request))

    @app.post("/api/v1/agent-runs/{agent_run_id}/complete", response_model=AgentRunResponse, tags=["control"])
    async def complete_agent(
        agent_run_id: UUID,
        payload: AgentCompleteRequest,
        request: Request,
        tenant_id: Annotated[UUID, Header(alias="X-Tenant-Id")],
        idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=255)],
    ) -> AgentRunResponse:
        require_policy("control.agent.complete", tenant_id, _trace_id(request))
        request_hash = hashlib.sha256(
            f"{agent_run_id}:{payload.model_dump_json()}:{tenant_id}".encode("utf-8")
        ).hexdigest()
        try:
            with psycopg2.connect(config.database_url) as connection:
                _, cur = _tenant_context(connection, tenant_id)
                cur.execute(
                    "SELECT request_hash,response_json FROM control.idempotency_records WHERE tenant_id=%s AND idempotency_key=%s FOR UPDATE",
                    (tenant_id, idempotency_key),
                )
                previous = cur.fetchone()
                if previous is not None:
                    if previous[0] != request_hash:
                        raise HTTPException(status_code=409, detail="Idempotency-Key reused with different request")
                    if previous[1] is not None:
                        return AgentRunResponse.model_validate(previous[1])
                else:
                    cur.execute(
                        "INSERT INTO control.idempotency_records(tenant_id,idempotency_key,request_hash) VALUES(%s,%s,%s)",
                        (tenant_id, idempotency_key, request_hash),
                    )
                cur.execute("SELECT task_run_id FROM control.agent_runs WHERE tenant_id=%s AND id=%s", (tenant_id, agent_run_id))
                agent_row = cur.fetchone()
                if agent_row is None:
                    raise HTTPException(status_code=404, detail="agent run not found")
                task_run_id = UUID(str(agent_row[0]))
            scheduler = Scheduler(config.database_url, tenant_slug(tenant_id))
            task_run_id = scheduler.complete_agent(agent_run_id, payload.output)
            response = AgentRunResponse(id=agent_run_id, task_run_id=task_run_id, status="completed", trace_id=_trace_id(request))
            with psycopg2.connect(config.database_url) as connection:
                _, cur = _tenant_context(connection, tenant_id)
                cur.execute(
                    "UPDATE control.idempotency_records SET response_status=200,response_json=%s WHERE tenant_id=%s AND idempotency_key=%s",
                    (Json(response.model_dump(mode="json")), tenant_id, idempotency_key),
                )
                cur.close()
            return response
        except (ValueError, PermissionError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except psycopg2.Error as exc:
            raise HTTPException(status_code=503, detail="control store unavailable") from exc

    @app.post("/api/v1/knowledge/ingest", response_model=KnowledgeIngestResponse, tags=["knowledge"])
    async def knowledge_ingest(
        payload: KnowledgeIngestRequest,
        request: Request,
        tenant_id: Annotated[UUID | None, Header(alias="X-Tenant-Id")] = None,
    ) -> KnowledgeIngestResponse:
        configured_root = default_drop_root().resolve()
        source = Path(payload.source_dir).resolve() if payload.source_dir else configured_root
        try:
            source.relative_to(configured_root)
        except ValueError:
            raise HTTPException(status_code=400, detail="source_dir must be inside KNOWLEDGE_DROP_ROOT")
        if tenant_id is None:
            raise HTTPException(status_code=422, detail="X-Tenant-Id header is required")
        require_policy(
            "knowledge.ingest",
            tenant_id,
            _trace_id(request),
            risk_class="low" if payload.dry_run else "high",
            side_effects="read_only" if payload.dry_run else "write_data",
        )
        current_tenant_slug = tenant_slug(tenant_id)
        result = ingest_directory(
            source,
            database_url=config.database_url,
            max_chars=payload.max_chars,
            dry_run=payload.dry_run,
            tenant_slug=current_tenant_slug,
        )
        return KnowledgeIngestResponse(
            batch_id=result.batch_id,
            scanned=result.scanned,
            accepted=result.accepted,
            skipped=result.skipped,
            deferred=result.deferred,
            files=[
                {
                    "source_uri": item.source_uri,
                    "sha256": item.sha256,
                    "byte_size": item.byte_size,
                    "chunks": item.chunks,
                    "status": item.status,
                    "extractor_key": item.extractor_key,
                }
                for item in result.files
            ],
            trace_id=_trace_id(request),
        )

    @app.post("/api/v1/knowledge/upload", response_model=KnowledgeIngestResponse, tags=["knowledge"])
    async def knowledge_upload(
        files: Annotated[list[UploadFile], File(description="Files selected from a local folder")],
        request: Request,
        tenant_id: Annotated[UUID, Header(alias="X-Tenant-Id")],
        idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=255)],
        relative_paths: Annotated[list[str] | None, Form()] = None,
    ) -> KnowledgeIngestResponse:
        """Stage browser uploads inside the controlled drop root then ingest them.

        The browser never supplies an executable command.  Rich files are
        retained locally and deferred to explicitly configured local workers.
        The gateway decision and the persisted business result are both bound
        to the selected relative paths and file bytes.  An exact retry returns
        the first result; a changed body or a concurrent duplicate fails
        closed instead of invoking a second ingestion.
        """
        if not files:
            raise HTTPException(status_code=422, detail="at least one file is required")
        selected_paths = relative_paths or []
        max_bytes = int(os.getenv("KNOWLEDGE_UPLOAD_MAX_BYTES", str(100 * 1024 * 1024)))
        reservation_owned = False
        request_hash: str | None = None
        try:
            descriptors, request_hash = await _describe_knowledge_uploads(
                files,
                selected_paths,
                max_bytes=max_bytes,
            )
            require_policy(
                "knowledge.upload",
                tenant_id,
                _trace_id(request),
                risk_class="high",
                side_effects="write_data",
                arguments={"upload_fingerprint": request_hash},
                idempotency_key=idempotency_key,
            )
            store = knowledge_upload_idempotency(tenant_id)
            try:
                reservation = store.reserve(idempotency_key=idempotency_key, request_hash=request_hash)
            except UploadIdempotencyConflict as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            except UploadInProgressError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            if reservation.is_replay:
                if reservation.cached_response is None:  # pragma: no cover - guarded by the store invariant
                    raise HTTPException(status_code=409, detail="upload replay is not finalized")
                return KnowledgeIngestResponse(**reservation.cached_response, trace_id=_trace_id(request))
            reservation_owned = True
            tenant = tenant_slug(tenant_id)
            upload_root = (default_drop_root() / "uploads" / tenant / str(uuid4())).resolve()
            drop_root = default_drop_root().resolve()
            try:
                upload_root.relative_to(drop_root)
            except ValueError as exc:  # pragma: no cover - defensive configuration check
                raise HTTPException(status_code=500, detail="invalid knowledge drop root") from exc
            await _stage_knowledge_uploads(files, descriptors, upload_root)
            result = ingest_directory(upload_root, database_url=config.database_url, tenant_slug=tenant)
            response = KnowledgeIngestResponse(
                batch_id=result.batch_id,
                scanned=result.scanned,
                accepted=result.accepted,
                skipped=result.skipped,
                deferred=result.deferred,
                files=[
                    {"source_uri": item.source_uri, "sha256": item.sha256, "byte_size": item.byte_size,
                     "chunks": item.chunks, "status": item.status, "extractor_key": item.extractor_key}
                    for item in result.files
                ],
                trace_id=_trace_id(request),
            )
            store.complete(
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                response=response.model_dump(mode="json", exclude={"trace_id"}),
            )
            reservation_owned = False
            return response
        except HTTPException as exc:
            if reservation_owned and request_hash is not None:
                knowledge_upload_idempotency(tenant_id).fail(
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                    detail=str(exc.detail),
                )
            raise
        except (OSError, ValueError, psycopg2.Error) as exc:
            if reservation_owned and request_hash is not None:
                knowledge_upload_idempotency(tenant_id).fail(
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                    detail=str(exc),
                )
            raise HTTPException(status_code=503, detail="knowledge upload failed") from exc
        finally:
            for upload in files:
                await upload.close()

    def knowledge_service(tenant_id: UUID) -> KnowledgeRetrievalService:
        return KnowledgeRetrievalService(config.database_url, tenant_slug(tenant_id))

    def knowledge_library(tenant_id: UUID) -> KnowledgeLibraryService:
        return KnowledgeLibraryService(config.database_url, tenant_slug(tenant_id))

    def knowledge_upload_idempotency(tenant_id: UUID) -> KnowledgeUploadIdempotencyStore:
        return KnowledgeUploadIdempotencyStore(config.database_url, tenant_slug(tenant_id))

    @app.get("/api/v1/knowledge/stats", response_model=KnowledgeStatsResponse, tags=["knowledge"])
    async def knowledge_stats(
        request: Request,
        tenant_id: Annotated[UUID, Header(alias="X-Tenant-Id")],
    ) -> KnowledgeStatsResponse:
        require_policy(
            "knowledge.read", tenant_id, _trace_id(request), risk_class="read_only", side_effects="read_only"
        )
        try:
            return KnowledgeStatsResponse(**knowledge_service(tenant_id).stats(), trace_id=_trace_id(request))
        except psycopg2.Error as exc:
            raise HTTPException(status_code=503, detail="knowledge store unavailable") from exc

    @app.get("/api/v1/knowledge/documents", response_model=KnowledgeItemsResponse, tags=["knowledge"])
    async def knowledge_documents(
        request: Request,
        tenant_id: Annotated[UUID, Header(alias="X-Tenant-Id")],
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
    ) -> KnowledgeItemsResponse:
        require_policy(
            "knowledge.read", tenant_id, _trace_id(request), risk_class="read_only", side_effects="read_only"
        )
        try:
            items = knowledge_service(tenant_id).list_documents(limit=limit)
        except psycopg2.Error as exc:
            raise HTTPException(status_code=503, detail="knowledge store unavailable") from exc
        return KnowledgeItemsResponse(items=items, trace_id=_trace_id(request))

    @app.get("/api/v1/knowledge/adapters", response_model=KnowledgeAdaptersResponse, tags=["knowledge"])
    async def knowledge_adapters(
        request: Request,
        tenant_id: Annotated[UUID, Header(alias="X-Tenant-Id")],
    ) -> KnowledgeAdaptersResponse:
        """Describe optional local processor seams without probing or running them."""

        require_policy(
            "knowledge.read", tenant_id, _trace_id(request), risk_class="read_only", side_effects="read_only"
        )
        return KnowledgeAdaptersResponse(
            items=[
                KnowledgeAdapterResponse(
                    key=adapter.key,
                    label=adapter.label,
                    supported_suffixes=list(adapter.supported_suffixes),
                    status=adapter.status,
                    execution_mode=adapter.execution_mode,
                    next_step=adapter.next_step,
                )
                for adapter in local_adapter_catalog()
            ],
            trace_id=_trace_id(request),
        )

    @app.post(
        "/api/v1/knowledge/documents/{document_id}/retire",
        response_model=KnowledgeDocumentLifecycleResponse,
        tags=["knowledge"],
    )
    async def knowledge_document_retire(
        document_id: UUID,
        payload: KnowledgeDocumentLifecycleRequest,
        request: Request,
        tenant_id: Annotated[UUID, Header(alias="X-Tenant-Id")],
        idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=255)],
    ) -> KnowledgeDocumentLifecycleResponse:
        """Move a document to the recycle bin; this is never a hard delete."""

        trace_id = _trace_id(request)
        require_policy(
            "knowledge.document.retire",
            tenant_id,
            trace_id,
            risk_class="high",
            side_effects="write_data",
            arguments={"document_id": str(document_id), "reason": payload.reason},
            idempotency_key=idempotency_key,
        )
        try:
            result = knowledge_library(tenant_id).retire_document(document_id, reason=payload.reason)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except psycopg2.Error as exc:
            raise HTTPException(status_code=503, detail="knowledge store unavailable") from exc
        return KnowledgeDocumentLifecycleResponse(
            document_id=result.document_id,
            recycle_bin_id=result.recycle_bin_id,
            status=result.status,
            trace_id=trace_id,
        )

    @app.get("/api/v1/knowledge/recycle-bin", response_model=KnowledgeItemsResponse, tags=["knowledge"])
    async def knowledge_recycle_bin(
        request: Request,
        tenant_id: Annotated[UUID, Header(alias="X-Tenant-Id")],
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
        include_restored: bool = False,
    ) -> KnowledgeItemsResponse:
        require_policy(
            "knowledge.recycle_bin.read",
            tenant_id,
            _trace_id(request),
            risk_class="read_only",
            side_effects="read_only",
        )
        try:
            items = knowledge_library(tenant_id).list_recycle_bin(
                limit=limit,
                include_restored=include_restored,
            )
        except (ValueError, psycopg2.Error) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return KnowledgeItemsResponse(items=items, trace_id=_trace_id(request))

    @app.post(
        "/api/v1/knowledge/recycle-bin/{recycle_bin_id}/restore",
        response_model=KnowledgeDocumentLifecycleResponse,
        tags=["knowledge"],
    )
    async def knowledge_document_restore(
        recycle_bin_id: UUID,
        payload: KnowledgeDocumentLifecycleRequest,
        request: Request,
        tenant_id: Annotated[UUID, Header(alias="X-Tenant-Id")],
        idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=255)],
    ) -> KnowledgeDocumentLifecycleResponse:
        """Restore only a tenant-owned document from an unreconciled recycle record."""

        trace_id = _trace_id(request)
        require_policy(
            "knowledge.document.restore",
            tenant_id,
            trace_id,
            risk_class="high",
            side_effects="write_data",
            arguments={"recycle_bin_id": str(recycle_bin_id), "reason": payload.reason},
            idempotency_key=idempotency_key,
        )
        try:
            result = knowledge_library(tenant_id).restore_document(recycle_bin_id, reason=payload.reason)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except psycopg2.Error as exc:
            raise HTTPException(status_code=503, detail="knowledge store unavailable") from exc
        return KnowledgeDocumentLifecycleResponse(
            document_id=result.document_id,
            recycle_bin_id=result.recycle_bin_id,
            status=result.status,
            trace_id=trace_id,
        )

    @app.get("/api/v1/knowledge/batches", response_model=KnowledgeItemsResponse, tags=["knowledge"])
    async def knowledge_batches(
        request: Request,
        tenant_id: Annotated[UUID, Header(alias="X-Tenant-Id")],
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
    ) -> KnowledgeItemsResponse:
        require_policy(
            "knowledge.read", tenant_id, _trace_id(request), risk_class="read_only", side_effects="read_only"
        )
        try:
            items = knowledge_service(tenant_id).list_batches(limit=limit)
        except psycopg2.Error as exc:
            raise HTTPException(status_code=503, detail="knowledge store unavailable") from exc
        return KnowledgeItemsResponse(items=items, trace_id=_trace_id(request))

    @app.post("/api/v1/knowledge/search", response_model=KnowledgeSearchResponse, tags=["knowledge"])
    async def knowledge_search(
        payload: KnowledgeSearchRequest,
        request: Request,
        tenant_id: Annotated[UUID, Header(alias="X-Tenant-Id")],
    ) -> KnowledgeSearchResponse:
        require_policy(
            "knowledge.search", tenant_id, _trace_id(request), risk_class="read_only", side_effects="read_only"
        )
        service = knowledge_service(tenant_id)
        try:
            items = service.search(payload.query, mode=payload.mode, limit=payload.limit)
            degraded = payload.mode == "hybrid" and bool(items) and all(
                item.get("retrieval_mode") == "keyword" for item in items
            )
        except RuntimeError as exc:
            if payload.mode == "vector":
                raise HTTPException(status_code=503, detail=str(exc)) from exc
            raise
        except (ValueError, psycopg2.Error) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return KnowledgeSearchResponse(
            mode=payload.mode, degraded=degraded, items=items, trace_id=_trace_id(request)
        )

    @app.post("/api/v1/knowledge/embed", response_model=KnowledgeEmbedResponse, tags=["knowledge"])
    async def knowledge_embed(
        payload: KnowledgeEmbedRequest,
        request: Request,
        tenant_id: Annotated[UUID, Header(alias="X-Tenant-Id")],
    ) -> KnowledgeEmbedResponse:
        require_policy("knowledge.embedding.write", tenant_id, _trace_id(request), risk_class="high")
        service = knowledge_service(tenant_id)
        try:
            embedded = service.embed_pending(
                limit=payload.limit, batch_id=str(payload.batch_id) if payload.batch_id else None
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except (ValueError, psycopg2.Error) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return KnowledgeEmbedResponse(
            embedded=embedded, model=service.embedder.model, trace_id=_trace_id(request)
        )

    @app.post(
        "/api/v1/knowledge/rich-media/extract",
        response_model=KnowledgeRichMediaExtractResponse,
        tags=["knowledge"],
    )
    async def knowledge_rich_media_extract(
        payload: KnowledgeRichMediaExtractRequest,
        request: Request,
        tenant_id: Annotated[UUID, Header(alias="X-Tenant-Id")],
        idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=255)],
    ) -> KnowledgeRichMediaExtractResponse:
        """Queue one policy-gated local MinerU extraction.

        The gateway decision is durable and bound to the supplied idempotency key.
        The external runtime is deliberately not run in the API process: the
        local Worker claims this persisted job under a single-consumer lease.
        """

        trace_id = _trace_id(request)
        require_policy(
            "knowledge.extract.rich_media",
            tenant_id,
            trace_id,
            risk_class="high",
            side_effects="write_data",
            arguments={"ingest_file_id": str(payload.ingest_file_id), "method": payload.method},
            idempotency_key=idempotency_key,
        )
        try:
            result = enqueue_rich_media_extraction(
                config.database_url,
                payload.ingest_file_id,
                method=payload.method,
                idempotency_key=idempotency_key,
                trace_id=trace_id,
                tenant_slug=tenant_slug(tenant_id),
            )
        except RichMediaQueueConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except RichMediaParseError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except psycopg2.Error as exc:
            raise HTTPException(status_code=503, detail="knowledge store unavailable") from exc
        return KnowledgeRichMediaExtractResponse(
            job_id=result.job_id,
            ingest_file_id=result.ingest_file_id,
            status=result.status,  # type: ignore[arg-type]
            attempt_count=result.attempt_count,
            method=result.method,  # type: ignore[arg-type]
            trace_id=trace_id,
        )

    @app.post(
        "/api/v1/knowledge/rich-media/retry",
        response_model=KnowledgeRichMediaExtractResponse,
        tags=["knowledge"],
    )
    async def knowledge_rich_media_retry(
        payload: KnowledgeRichMediaExtractRequest,
        request: Request,
        tenant_id: Annotated[UUID, Header(alias="X-Tenant-Id")],
        idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=255)],
    ) -> KnowledgeRichMediaExtractResponse:
        """Explicitly requeue one recorded failure after a fresh policy decision."""

        trace_id = _trace_id(request)
        require_policy(
            "knowledge.extract.rich_media",
            tenant_id,
            trace_id,
            risk_class="high",
            side_effects="write_data",
            arguments={"ingest_file_id": str(payload.ingest_file_id), "method": payload.method, "operation": "retry"},
            idempotency_key=idempotency_key,
        )
        try:
            result = enqueue_rich_media_extraction(
                config.database_url,
                payload.ingest_file_id,
                method=payload.method,
                idempotency_key=idempotency_key,
                trace_id=trace_id,
                tenant_slug=tenant_slug(tenant_id),
                retry=True,
            )
        except RichMediaQueueConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except RichMediaParseError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except psycopg2.Error as exc:
            raise HTTPException(status_code=503, detail="knowledge store unavailable") from exc
        return KnowledgeRichMediaExtractResponse(
            job_id=result.job_id,
            ingest_file_id=result.ingest_file_id,
            status=result.status,  # type: ignore[arg-type]
            attempt_count=result.attempt_count,
            method=result.method,  # type: ignore[arg-type]
            trace_id=trace_id,
        )

    @app.get("/api/v1/knowledge/rich-media/pending", response_model=KnowledgeItemsResponse, tags=["knowledge"])
    async def knowledge_rich_media_pending(
        request: Request,
        tenant_id: Annotated[UUID, Header(alias="X-Tenant-Id")],
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
    ) -> KnowledgeItemsResponse:
        """List rich-media files awaiting the policy-gated local MinerU worker."""

        require_policy(
            "knowledge.read", tenant_id, _trace_id(request), risk_class="read_only", side_effects="read_only"
        )
        try:
            items = list_pending_rich_media(
                config.database_url, tenant_slug=tenant_slug(tenant_id), limit=limit
            )
        except (ValueError, psycopg2.Error) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return KnowledgeItemsResponse(items=items, trace_id=_trace_id(request))

    @app.get("/api/v1/ui/operations", response_model=OperationsSummaryResponse, tags=["ui"])
    async def ui_operations(
        request: Request,
        tenant_id: Annotated[UUID, Header(alias="X-Tenant-Id")],
    ) -> OperationsSummaryResponse:
        require_policy(
            "platform.dashboard.read",
            tenant_id,
            _trace_id(request),
            risk_class="read_only",
            side_effects="read_only",
        )
        try:
            with psycopg2.connect(config.database_url) as connection:
                _, cur = _tenant_context(connection, tenant_id)
                cur.execute(
                    """SELECT
                    (SELECT count(*) FROM control.missions),
                    (SELECT count(*) FROM control.workflow_runs),
                    (SELECT count(*) FROM control.task_runs),
                    (SELECT count(*) FROM control.agent_runs),
                    (SELECT count(*) FROM catalog.plugins),
                    (SELECT count(*) FROM graph.spaces),
                    (SELECT count(*) FROM graph.nodes WHERE deleted_at IS NULL),
                    (SELECT count(*) FROM graph.edges WHERE deleted_at IS NULL),
                    (SELECT count(*) FROM audit.engagements),
                    (SELECT count(*) FROM audit.findings),
                    (SELECT count(*) FROM quant.backtests),
                    (SELECT count(*) FROM aiops.incidents),
                    (SELECT count(*) FROM aiops.executions)"""
                )
                count_row = cur.fetchone()
                if count_row is None:  # pragma: no cover - scalar query always returns one row
                    raise RuntimeError("operations summary returned no row")
                cur.execute(
                    """SELECT tool.capability,decision.decision,decision.risk_score,
                    decision.reason,tool.trace_id,decision.decided_at
                    FROM policy.decisions decision
                    JOIN policy.tool_calls tool ON tool.id=decision.tool_call_id
                    ORDER BY decision.decided_at DESC LIMIT 30"""
                )
                decisions = cur.fetchall()
                cur.close()
        except psycopg2.Error as exc:
            raise HTTPException(status_code=503, detail="operations store unavailable") from exc
        keys = (
            "missions", "workflow_runs", "task_runs", "agent_runs", "plugins", "graph_spaces",
            "graph_nodes", "graph_edges", "audit_engagements", "audit_findings", "quant_backtests",
            "aiops_incidents", "aiops_executions",
        )
        return OperationsSummaryResponse(
            counts={key: int(value) for key, value in zip(keys, count_row, strict=True)},
            recent_decisions=[
                {
                    "capability": row[0], "decision": row[1], "risk_score": float(row[2]),
                    "reason": row[3], "trace_id": row[4], "decided_at": row[5],
                }
                for row in decisions
            ],
            trace_id=_trace_id(request),
        )

    @app.get("/api/v1/ui/operations/detail", response_model=OperationsDetailResponse, tags=["ui"])
    async def ui_operations_detail(
        request: Request,
        tenant_id: Annotated[UUID, Header(alias="X-Tenant-Id")],
    ) -> OperationsDetailResponse:
        require_policy(
            "platform.dashboard.read",
            tenant_id,
            _trace_id(request),
            risk_class="read_only",
            side_effects="read_only",
        )
        try:
            with psycopg2.connect(config.database_url) as connection:
                _, cur = _tenant_context(connection, tenant_id)
                cur.execute(
                    """SELECT id,title,domain,autonomy_mode,status,created_at
                    FROM control.missions ORDER BY created_at DESC"""
                )
                missions = cur.fetchall()
                mission_ids = [row[0] for row in missions]
                workflows_by_mission: dict[UUID, list[tuple[Any, ...]]] = {}
                tasks_by_workflow: dict[UUID, list[tuple[Any, ...]]] = {}
                agents_by_task: dict[UUID, list[tuple[Any, ...]]] = {}
                if mission_ids:
                    cur.execute(
                        """SELECT id,mission_id,workflow_key,workflow_version,status,started_at,finished_at,trace_id
                        FROM control.workflow_runs WHERE mission_id = ANY(%s)
                        ORDER BY started_at NULLS LAST, id""",
                        (mission_ids,),
                    )
                    for row in cur.fetchall():
                        workflows_by_mission.setdefault(row[1], []).append(row)
                    workflow_ids = [row[0] for rows in workflows_by_mission.values() for row in rows]
                    if workflow_ids:
                        cur.execute(
                            """SELECT id,workflow_run_id,node_key,capability,status,attempt,max_attempts,
                            started_at,finished_at,error_detail,trace_id
                            FROM control.task_runs WHERE workflow_run_id = ANY(%s)
                            ORDER BY created_at, id""",
                            (workflow_ids,),
                        )
                        for row in cur.fetchall():
                            tasks_by_workflow.setdefault(row[1], []).append(row)
                        task_ids = [row[0] for rows in tasks_by_workflow.values() for row in rows]
                        if task_ids:
                            cur.execute(
                                """SELECT id,task_run_id,role_key,model_key,status,token_input,token_output,
                                cost,started_at,finished_at,error_detail
                                FROM control.agent_runs WHERE task_run_id = ANY(%s)
                                ORDER BY created_at, id""",
                                (task_ids,),
                            )
                            for row in cur.fetchall():
                                agents_by_task.setdefault(row[1], []).append(row)
                cur.close()
        except psycopg2.Error as exc:
            raise HTTPException(status_code=503, detail="operations store unavailable") from exc
        return OperationsDetailResponse(
            missions=[
                MissionRunDetail(
                    id=mission[0], title=mission[1], domain=mission[2],
                    autonomy_mode=mission[3], status=mission[4], created_at=mission[5],
                    workflows=[
                        WorkflowRunDetail(
                            id=wf[0], workflow_key=wf[2], workflow_version=wf[3], status=wf[4],
                            started_at=wf[5], finished_at=wf[6], trace_id=wf[7],
                            tasks=[
                                TaskRunDetail(
                                    id=task[0], node_key=task[2], capability=task[3], status=task[4],
                                    attempt=task[5], max_attempts=task[6], started_at=task[7],
                                    finished_at=task[8], error_detail=task[9], trace_id=task[10],
                                    agents=[
                                        AgentRunDetail(
                                            id=agent[0], role_key=agent[2], model_key=agent[3], status=agent[4],
                                            token_input=agent[5], token_output=agent[6], cost=float(agent[7]),
                                            started_at=agent[8], finished_at=agent[9], error_detail=agent[10],
                                        )
                                        for agent in agents_by_task.get(task[0], [])
                                    ],
                                )
                                for task in tasks_by_workflow.get(wf[0], [])
                            ],
                        )
                        for wf in workflows_by_mission.get(mission[0], [])
                    ],
                )
                for mission in missions
            ],
            trace_id=_trace_id(request),
        )

    @app.post("/api/v1/graph/spaces", tags=["graph"])
    async def graph_space(
        payload: GraphSpaceRequest,
        request: Request,
        tenant_id: Annotated[UUID, Header(alias="X-Tenant-Id")],
        idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=255)],
    ) -> dict[str, object]:
        require_policy(
            "graph.space.write", tenant_id, _trace_id(request),
            arguments=payload.model_dump(mode="json"), idempotency_key=idempotency_key,
        )
        try:
            space_id = GraphService(config.database_url, tenant_slug(tenant_id)).ensure_space(
                payload.key, payload.level, payload.name,
                cluster_key=payload.cluster_key, description=payload.description,
            )
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"id": str(space_id), "key": payload.key, "trace_id": _trace_id(request)}

    @app.post("/api/v1/graph/nodes", tags=["graph"])
    async def graph_node(
        payload: GraphNodeRequest,
        request: Request,
        tenant_id: Annotated[UUID, Header(alias="X-Tenant-Id")],
        idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=255)],
    ) -> dict[str, object]:
        require_policy(
            "graph.node.write", tenant_id, _trace_id(request),
            arguments=payload.model_dump(mode="json"), idempotency_key=idempotency_key,
        )
        try:
            node_id = GraphService(config.database_url, tenant_slug(tenant_id)).upsert_node(
                payload.space_key, payload.canonical_key, payload.node_type, payload.label, payload.properties
            )
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"id": str(node_id), "trace_id": _trace_id(request)}

    @app.post("/api/v1/graph/edges", tags=["graph"])
    async def graph_edge(
        payload: GraphEdgeRequest,
        request: Request,
        tenant_id: Annotated[UUID, Header(alias="X-Tenant-Id")],
        idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=255)],
    ) -> dict[str, object]:
        require_policy(
            "graph.edge.write", tenant_id, _trace_id(request),
            arguments=payload.model_dump(mode="json"), idempotency_key=idempotency_key,
        )
        try:
            edge_id = GraphService(config.database_url, tenant_slug(tenant_id)).upsert_edge(
                payload.space_key, payload.source_node_id, payload.target_node_id, payload.relation_type, payload.weight
            )
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"id": str(edge_id), "trace_id": _trace_id(request)}

    @app.post("/api/v1/graph/bridges", tags=["graph"])
    async def graph_bridge(
        payload: GraphBridgeRequest,
        request: Request,
        tenant_id: Annotated[UUID, Header(alias="X-Tenant-Id")],
        idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=255)],
    ) -> dict[str, object]:
        require_policy(
            "graph.bridge.write", tenant_id, _trace_id(request),
            arguments=payload.model_dump(mode="json"), idempotency_key=idempotency_key,
        )
        try:
            bridge_id = GraphService(config.database_url, tenant_slug(tenant_id)).register_bridge(
                payload.source_space_key, payload.target_space_key, payload.source_node_id,
                payload.target_node_id, payload.relation_type, payload.weight,
            )
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"id": str(bridge_id), "trace_id": _trace_id(request)}

    @app.get("/api/v1/graph/neighbors", response_model=GraphNeighborsResponse, tags=["graph"])
    async def graph_neighbors(
        space_key: str,
        start_node_id: UUID,
        request: Request,
        tenant_id: Annotated[UUID, Header(alias="X-Tenant-Id")],
        max_nodes: int = 100, max_edges: int = 200, max_hops: int = 2,
    ) -> GraphNeighborsResponse:
        require_policy(
            "graph.neighbors.read",
            tenant_id,
            _trace_id(request),
            risk_class="read_only",
            side_effects="read_only",
        )
        try:
            result = GraphService(config.database_url, tenant_slug(tenant_id)).bounded_neighbors(
                space_key, start_node_id, GraphBudget(max_nodes=max_nodes, max_edges=max_edges, max_hops=max_hops)
            )
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return GraphNeighborsResponse(**result, trace_id=_trace_id(request))

    @app.get("/api/v1/graph/routes", response_model=GraphRouteResponse, tags=["graph"])
    async def graph_route(
        space_key: str,
        start_node_id: UUID,
        request: Request,
        tenant_id: Annotated[UUID, Header(alias="X-Tenant-Id")],
        max_graphs: int = 2,
        max_hops: int = 2,
        max_frontier: int = 50,
        max_nodes: int = 100,
        max_edges: int = 200,
        max_bridge_hops: int = 1,
        max_latency_ms: int = 3_000,
        min_confidence: float = 0,
        allowed_relation_type: Annotated[list[str] | None, Query()] = None,
        allowed_graph_space_key: Annotated[list[str] | None, Query()] = None,
    ) -> GraphRouteResponse:
        arguments = {
            "space_key": space_key, "start_node_id": str(start_node_id), "max_graphs": max_graphs,
            "max_hops": max_hops, "max_frontier": max_frontier, "max_nodes": max_nodes,
            "max_edges": max_edges, "max_bridge_hops": max_bridge_hops, "max_latency_ms": max_latency_ms,
            "min_confidence": min_confidence, "allowed_relation_type": allowed_relation_type or [],
            "allowed_graph_space_key": allowed_graph_space_key or [],
        }
        require_policy(
            "graph.route.read", tenant_id, _trace_id(request), risk_class="read_only",
            side_effects="read_only", arguments=arguments,
        )
        try:
            result = GraphService(config.database_url, tenant_slug(tenant_id)).bounded_route(
                space_key,
                start_node_id,
                GraphBudget(
                    max_graphs=max_graphs, max_hops=max_hops, max_frontier=max_frontier,
                    max_nodes=max_nodes, max_edges=max_edges, max_bridge_hops=max_bridge_hops,
                    max_latency_ms=max_latency_ms, min_confidence=min_confidence,
                    allowed_relation_types=tuple(allowed_relation_type) if allowed_relation_type else None,
                    allowed_graph_space_keys=tuple(allowed_graph_space_key) if allowed_graph_space_key else None,
                ),
            )
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return GraphRouteResponse(**result, trace_id=_trace_id(request))

    @app.get("/api/v1/graph/governance", response_model=GraphGovernanceResponse, tags=["graph"])
    async def graph_governance(
        request: Request,
        tenant_id: Annotated[UUID, Header(alias="X-Tenant-Id")],
    ) -> GraphGovernanceResponse:
        require_policy(
            "graph.governance.read", tenant_id, _trace_id(request), risk_class="read_only", side_effects="read_only",
        )
        try:
            result = GraphService(config.database_url, tenant_slug(tenant_id)).governance_overview()
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return GraphGovernanceResponse(**result, trace_id=_trace_id(request))

    @app.get("/api/v1/graph/nodes/{node_id}/revisions", response_model=GraphNodeRevisionsResponse, tags=["graph"])
    async def graph_node_revisions(
        node_id: UUID,
        request: Request,
        tenant_id: Annotated[UUID, Header(alias="X-Tenant-Id")],
        limit: int = 30,
    ) -> GraphNodeRevisionsResponse:
        require_policy(
            "graph.governance.read", tenant_id, _trace_id(request), risk_class="read_only", side_effects="read_only",
            arguments={"node_id": str(node_id), "limit": limit},
        )
        try:
            items = GraphService(config.database_url, tenant_slug(tenant_id)).list_node_revisions(node_id, limit)
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return GraphNodeRevisionsResponse(items=items, trace_id=_trace_id(request))

    @app.post("/api/v1/graph/nodes/{node_id}/retire", tags=["graph"])
    async def graph_node_retire(
        node_id: UUID,
        payload: GraphNodeRetireRequest,
        request: Request,
        tenant_id: Annotated[UUID, Header(alias="X-Tenant-Id")],
        idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=255)],
    ) -> dict[str, object]:
        require_policy(
            "graph.node.retire", tenant_id, _trace_id(request),
            arguments={"node_id": str(node_id), **payload.model_dump(mode="json")}, idempotency_key=idempotency_key,
        )
        try:
            GraphService(config.database_url, tenant_slug(tenant_id)).soft_delete_node(node_id, {"reason": payload.reason})
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"node_id": str(node_id), "status": "recycled", "trace_id": _trace_id(request)}

    @app.post("/api/v1/graph/nodes/{node_id}/revisions/{revision_id}/rollback", tags=["graph"])
    async def graph_node_rollback(
        node_id: UUID,
        revision_id: UUID,
        payload: GraphRevisionRollbackRequest,
        request: Request,
        tenant_id: Annotated[UUID, Header(alias="X-Tenant-Id")],
        idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=255)],
    ) -> dict[str, object]:
        require_policy(
            "graph.node.rollback", tenant_id, _trace_id(request),
            arguments={"node_id": str(node_id), "revision_id": str(revision_id), **payload.model_dump(mode="json")},
            idempotency_key=idempotency_key,
        )
        try:
            restored = GraphService(config.database_url, tenant_slug(tenant_id)).rollback_node_revision(node_id, revision_id)
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"node_id": str(restored), "status": "rolled_back", "trace_id": _trace_id(request)}

    @app.get("/api/v1/graph/conflicts", response_model=GraphConflictsResponse, tags=["graph"])
    async def graph_conflicts(
        request: Request,
        tenant_id: Annotated[UUID, Header(alias="X-Tenant-Id")],
        limit: int = 100,
    ) -> GraphConflictsResponse:
        require_policy(
            "graph.governance.read", tenant_id, _trace_id(request), risk_class="read_only", side_effects="read_only",
            arguments={"limit": limit},
        )
        try:
            items = GraphService(config.database_url, tenant_slug(tenant_id)).list_conflicts(limit)
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return GraphConflictsResponse(items=items, trace_id=_trace_id(request))

    @app.post("/api/v1/graph/nodes/merge", response_model=GraphMergeResponse, tags=["graph"])
    async def graph_nodes_merge(
        payload: GraphMergeRequest,
        request: Request,
        tenant_id: Annotated[UUID, Header(alias="X-Tenant-Id")],
        idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=255)],
    ) -> GraphMergeResponse:
        require_policy(
            "graph.node.merge", tenant_id, _trace_id(request), risk_class="medium",
            arguments=payload.model_dump(mode="json"), idempotency_key=idempotency_key,
        )
        try:
            result = GraphService(config.database_url, tenant_slug(tenant_id)).merge_nodes(
                payload.space_key,
                payload.target_key,
                payload.source_keys,
                expected_revision=payload.expected_revision,
                reason=payload.reason,
            )
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return GraphMergeResponse(**result, trace_id=_trace_id(request))

    @app.post("/api/v1/graph/nodes/split", response_model=GraphSplitResponse, tags=["graph"])
    async def graph_nodes_split(
        payload: GraphSplitRequest,
        request: Request,
        tenant_id: Annotated[UUID, Header(alias="X-Tenant-Id")],
        idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=255)],
    ) -> GraphSplitResponse:
        require_policy(
            "graph.node.split", tenant_id, _trace_id(request), risk_class="medium",
            arguments=payload.model_dump(mode="json"), idempotency_key=idempotency_key,
        )
        try:
            result = GraphService(config.database_url, tenant_slug(tenant_id)).split_node(
                payload.space_key,
                payload.source_key,
                [part.model_dump(mode="json") for part in payload.parts],
                expected_revision=payload.expected_revision,
                reason=payload.reason,
            )
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return GraphSplitResponse(**result, trace_id=_trace_id(request))

    @app.post("/api/v1/graph/arbitration/resolve", response_model=GraphArbitrationResponse, tags=["graph"])
    async def graph_arbitration_resolve(
        payload: GraphArbitrationRequest,
        request: Request,
        tenant_id: Annotated[UUID, Header(alias="X-Tenant-Id")],
        idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=255)],
    ) -> GraphArbitrationResponse:
        require_policy(
            "graph.arbitration.resolve", tenant_id, _trace_id(request), risk_class="medium",
            arguments=payload.model_dump(mode="json"), idempotency_key=idempotency_key,
        )
        try:
            result = GraphService(config.database_url, tenant_slug(tenant_id)).arbitrate_conflict(
                payload.case_id,
                payload.decision,
                payload.reason,
                merge=payload.merge,
            )
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return GraphArbitrationResponse(**result, trace_id=_trace_id(request))

    @app.get("/api/v1/graph/merges", response_model=GraphMergeRecordsResponse, tags=["graph"])
    async def graph_merge_records(
        request: Request,
        tenant_id: Annotated[UUID, Header(alias="X-Tenant-Id")],
        space_key: str,
        limit: int = 50,
    ) -> GraphMergeRecordsResponse:
        require_policy(
            "graph.governance.read", tenant_id, _trace_id(request), risk_class="read_only", side_effects="read_only",
            arguments={"space_key": space_key, "limit": limit},
        )
        try:
            items = GraphService(config.database_url, tenant_slug(tenant_id)).list_merge_records(space_key, limit)
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return GraphMergeRecordsResponse(items=items, trace_id=_trace_id(request))

    @app.get("/api/v1/graph/splits", response_model=GraphSplitRecordsResponse, tags=["graph"])
    async def graph_split_records(
        request: Request,
        tenant_id: Annotated[UUID, Header(alias="X-Tenant-Id")],
        space_key: str,
        limit: int = 50,
    ) -> GraphSplitRecordsResponse:
        require_policy(
            "graph.governance.read", tenant_id, _trace_id(request), risk_class="read_only", side_effects="read_only",
            arguments={"space_key": space_key, "limit": limit},
        )
        try:
            items = GraphService(config.database_url, tenant_slug(tenant_id)).list_split_records(space_key, limit)
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return GraphSplitRecordsResponse(items=items, trace_id=_trace_id(request))

    @app.get("/api/v1/graph/visualization", response_model=GraphVisualizationResponse, tags=["graph"])
    async def graph_visualization(
        request: Request,
        tenant_id: Annotated[UUID, Header(alias="X-Tenant-Id")],
        space_key: str | None = None,
        node_type: str | None = None,
        max_nodes: int = 180,
        max_edges: int = 360,
    ) -> GraphVisualizationResponse:
        require_policy(
            "graph.visualize.read",
            tenant_id,
            _trace_id(request),
            risk_class="read_only",
            side_effects="read_only",
        )
        try:
            result = GraphService(config.database_url, tenant_slug(tenant_id)).visualization(
                space_key,
                node_type,
                max_nodes=max_nodes,
                max_edges=max_edges,
            )
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return GraphVisualizationResponse(**result, trace_id=_trace_id(request))

    @app.post("/api/v1/graph/extractions/preview", response_model=GraphExtractionResponse, tags=["graph"])
    async def graph_extraction_preview(
        payload: GraphExtractionRequest,
        request: Request,
        tenant_id: Annotated[UUID, Header(alias="X-Tenant-Id")],
    ) -> GraphExtractionResponse:
        """Read-only preview of deterministic extraction candidates (no staging)."""
        require_policy(
            "knowledge.extract.graph",
            tenant_id,
            _trace_id(request),
            risk_class="read_only",
            side_effects="read_only",
            arguments=payload.model_dump(mode="json"),
        )
        try:
            candidates, conflicts = _run_extraction(
                config.database_url, tenant_slug(tenant_id), payload
            )
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return GraphExtractionResponse(
            candidates=[c.to_json() for c in candidates],
            conflicts=conflicts,
            rejected=[],
            staged_as_changeset=None,
            changeset_status=None,
            trace_id=_trace_id(request),
        )

    @app.post("/api/v1/graph/extractions/propose", response_model=GraphExtractionResponse, tags=["graph"])
    async def graph_extraction_propose(
        payload: GraphExtractionRequest,
        request: Request,
        tenant_id: Annotated[UUID, Header(alias="X-Tenant-Id")],
        idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=255)],
    ) -> GraphExtractionResponse:
        """Stage validated extraction candidates as a shadow ChangeSet (not applied)."""
        trace_id = _trace_id(request)
        require_policy(
            "knowledge.extract.graph",
            tenant_id,
            trace_id,
            risk_class="low",
            side_effects="write_data",
            arguments=payload.model_dump(mode="json"),
            idempotency_key=idempotency_key,
        )
        try:
            candidates, conflicts = _run_extraction(
                config.database_url, tenant_slug(tenant_id), payload
            )
            service = GraphExtractionService(config.database_url, tenant_slug(tenant_id))
            staged = service.route_candidates(candidates, allow_spaces={payload.space_key} if payload.space_key else None)
            proposed = staged.candidates
            changeset_id: UUID | None = None
            if proposed and payload.space_key:
                changeset_id = service.stage_proposal(
                    proposed,
                    f"Auto extract from {payload.document_id or 'recent documents'}",
                    "Phase 5 governed graph extraction",
                    graph_space_key=payload.space_key,
                )
            merged_conflicts = list(conflicts) + list(staged.conflicts)
            status = None
            if changeset_id is not None:
                with psycopg2.connect(config.database_url) as connection, connection.cursor() as cur:
                    tenant_slug_val = tenant_slug(tenant_id)
                    cur.execute("SELECT id FROM iam.tenants WHERE slug=%s", (tenant_slug_val,))
                    tenant_row = cur.fetchone()
                    t = tenant_row[0] if tenant_row else None
                    cur.execute("SELECT set_config('app.tenant_id', %s, true)", (str(t),))
                    cur.execute("SELECT status FROM knowledge.change_sets WHERE id=%s", (changeset_id,))
                    row = cur.fetchone()
                    status = row[0] if row else None
            return GraphExtractionResponse(
                candidates=[c.to_json() for c in proposed],
                conflicts=merged_conflicts,
                rejected=list(staged.rejected),
                staged_as_changeset=str(changeset_id) if changeset_id else None,
                changeset_status=status,
                trace_id=trace_id,
            )
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/v1/graph/extractions/proposals", response_model=GraphProposalListResponse, tags=["graph"])
    async def graph_extraction_proposals(
        request: Request,
        tenant_id: Annotated[UUID, Header(alias="X-Tenant-Id")],
        limit: int = Query(default=50, ge=1, le=200),
    ) -> GraphProposalListResponse:
        """List staged extraction proposals (ChangeSets) with candidate summary."""
        require_policy(
            "knowledge.extract.graph",
            tenant_id,
            _trace_id(request),
            risk_class="read_only",
            side_effects="read_only",
            arguments={"limit": limit},
        )
        try:
            lifecycle = KnowledgeLifecycleService(config.database_url, tenant_slug(tenant_id))
            proposals = [GraphProposal.model_validate(p) for p in lifecycle.list_changesets(change_type="graph", limit=limit)]
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return GraphProposalListResponse(proposals=proposals, trace_id=_trace_id(request))

    @app.post("/api/v1/graph/extractions/proposals/{proposal_id}/approve", response_model=GraphProposalDecisionResponse, tags=["graph"])
    async def graph_extraction_proposal_approve(
        proposal_id: UUID,
        request: Request,
        tenant_id: Annotated[UUID, Header(alias="X-Tenant-Id")],
        idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=255)],
    ) -> GraphProposalDecisionResponse:
        """放行: validate → approve → release → activate a staged proposal into the live graph."""
        trace_id = _trace_id(request)
        require_policy(
            "graph.change.apply",
            tenant_id,
            trace_id,
            risk_class="medium",
            side_effects="write_data",
            arguments={"proposal_id": str(proposal_id)},
            idempotency_key=idempotency_key,
        )
        try:
            lifecycle = KnowledgeLifecycleService(config.database_url, tenant_slug(tenant_id))
            release_id = lifecycle.apply_changeset(proposal_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return GraphProposalDecisionResponse(
            proposal_id=str(proposal_id), status="applied", release_id=str(release_id), trace_id=trace_id
        )

    @app.post("/api/v1/graph/extractions/proposals/{proposal_id}/reject", response_model=GraphProposalDecisionResponse, tags=["graph"])
    async def graph_extraction_proposal_reject(
        proposal_id: UUID,
        request: Request,
        tenant_id: Annotated[UUID, Header(alias="X-Tenant-Id")],
        idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=255)],
    ) -> GraphProposalDecisionResponse:
        """驳回 a staged proposal before it is released."""
        trace_id = _trace_id(request)
        require_policy(
            "graph.change.reject",
            tenant_id,
            trace_id,
            risk_class="low",
            side_effects="write_data",
            arguments={"proposal_id": str(proposal_id)},
            idempotency_key=idempotency_key,
        )
        try:
            lifecycle = KnowledgeLifecycleService(config.database_url, tenant_slug(tenant_id))
            lifecycle.reject_changeset(proposal_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return GraphProposalDecisionResponse(proposal_id=str(proposal_id), status="rejected", trace_id=trace_id)

    @app.post("/api/v1/audit/ledger", response_model=AuditLedgerResponse, tags=["audit"])
    async def audit_ledger(
        payload: AuditLedgerRequest,
        request: Request,
        tenant_id: Annotated[UUID, Header(alias="X-Tenant-Id")],
    ) -> AuditLedgerResponse:
        require_policy("audit.ledger.ingest", tenant_id, _trace_id(request))
        try:
            configured_root = default_drop_root().resolve()
            csv_path = Path(payload.csv_path).resolve()
            csv_path.relative_to(configured_root)
            result = AuditPipeline(config.database_url, tenant_slug(tenant_id)).run_ledger_csv(
                csv_path, payload.engagement_name, payload.amount_threshold
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return AuditLedgerResponse(
            engagement_id=result.engagement_id,
            rows=result.rows,
            anomalies=result.anomalies,
            duplicate_rows=result.duplicate_rows,
            missing_amounts=result.missing_amounts,
            trace_id=_trace_id(request),
        )

    @app.get("/api/v1/audit/engagements", response_model=AuditEngagementsResponse, tags=["audit"])
    async def audit_engagements(
        request: Request,
        tenant_id: Annotated[UUID, Header(alias="X-Tenant-Id")],
        limit: int = 50,
    ) -> AuditEngagementsResponse:
        require_policy(
            "audit.chain.read", tenant_id, _trace_id(request), risk_class="read_only", side_effects="read_only",
            arguments={"limit": limit},
        )
        try:
            items = AuditPipeline(config.database_url, tenant_slug(tenant_id)).list_engagements(limit)
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return AuditEngagementsResponse(items=items, trace_id=_trace_id(request))

    @app.get("/api/v1/audit/engagements/{engagement_id}/lineage", response_model=AuditLineageResponse, tags=["audit"])
    async def audit_engagement_lineage(
        engagement_id: UUID,
        request: Request,
        tenant_id: Annotated[UUID, Header(alias="X-Tenant-Id")],
    ) -> AuditLineageResponse:
        require_policy(
            "audit.chain.read", tenant_id, _trace_id(request), risk_class="read_only", side_effects="read_only",
            arguments={"engagement_id": str(engagement_id)},
        )
        try:
            lineage = AuditPipeline(config.database_url, tenant_slug(tenant_id)).get_engagement_lineage(str(engagement_id))
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return AuditLineageResponse(
            engagement_id=lineage["engagement_id"],
            engagement=lineage["engagement"],
            evidence=lineage["evidence"],
            anomalies=lineage["anomalies"],
            findings=lineage["findings"],
            trace_id=_trace_id(request),
        )

    @app.post("/api/v1/audit/findings/confirm", response_model=AuditConfirmResponse, tags=["audit"])
    async def audit_finding_confirm(
        payload: AuditConfirmRequest,
        request: Request,
        tenant_id: Annotated[UUID, Header(alias="X-Tenant-Id")],
        idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=255)],
    ) -> AuditConfirmResponse:
        require_policy(
            "audit.finding.confirm", tenant_id, _trace_id(request), risk_class="medium",
            side_effects="write_data", arguments=payload.model_dump(mode="json"),
            idempotency_key=idempotency_key,
        )
        try:
            finding_id = AuditPipeline(config.database_url, tenant_slug(tenant_id)).confirm_candidate(
                str(payload.candidate_id), payload.reviewer_label
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return AuditConfirmResponse(
            finding_id=finding_id, candidate_id=str(payload.candidate_id), status="confirmed",
            trace_id=_trace_id(request),
        )

    @app.get("/api/v1/quant/backtests", response_model=QuantBacktestsResponse, tags=["quant"])
    async def quant_backtests(
        request: Request,
        tenant_id: Annotated[UUID, Header(alias="X-Tenant-Id")],
        limit: int = 50,
    ) -> QuantBacktestsResponse:
        require_policy(
            "quant.chain.read", tenant_id, _trace_id(request), risk_class="read_only", side_effects="read_only",
            arguments={"limit": limit},
        )
        try:
            items = QuantService(config.database_url, tenant_slug(tenant_id)).list_backtests(limit)
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return QuantBacktestsResponse(items=items, trace_id=_trace_id(request))

    @app.get("/api/v1/quant/backtests/{backtest_id}/lineage", response_model=QuantLineageResponse, tags=["quant"])
    async def quant_backtest_lineage(
        backtest_id: UUID,
        request: Request,
        tenant_id: Annotated[UUID, Header(alias="X-Tenant-Id")],
    ) -> QuantLineageResponse:
        require_policy(
            "quant.chain.read", tenant_id, _trace_id(request), risk_class="read_only", side_effects="read_only",
            arguments={"backtest_id": str(backtest_id)},
        )
        try:
            lineage = QuantService(config.database_url, tenant_slug(tenant_id)).get_backtest_lineage(str(backtest_id))
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return QuantLineageResponse(
            backtest_id=lineage["backtest_id"],
            backtest=lineage["backtest"],
            dataset=lineage["dataset"],
            trace_id=_trace_id(request),
        )

    @app.get("/api/v1/aiops/incidents", response_model=AIOpsIncidentsResponse, tags=["aiops"])
    async def aiops_incidents(
        request: Request,
        tenant_id: Annotated[UUID, Header(alias="X-Tenant-Id")],
        limit: int = 50,
    ) -> AIOpsIncidentsResponse:
        require_policy(
            "aiops.chain.read", tenant_id, _trace_id(request), risk_class="read_only", side_effects="read_only",
            arguments={"limit": limit},
        )
        try:
            items = AIOpsGovernanceService(config.database_url, tenant_slug(tenant_id)).list_incidents(limit)
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return AIOpsIncidentsResponse(items=items, trace_id=_trace_id(request))

    @app.get("/api/v1/aiops/incidents/{incident_id}/lineage", response_model=AIOpsLineageResponse, tags=["aiops"])
    async def aiops_incident_lineage(
        incident_id: UUID,
        request: Request,
        tenant_id: Annotated[UUID, Header(alias="X-Tenant-Id")],
    ) -> AIOpsLineageResponse:
        require_policy(
            "aiops.chain.read", tenant_id, _trace_id(request), risk_class="read_only", side_effects="read_only",
            arguments={"incident_id": str(incident_id)},
        )
        try:
            lineage = AIOpsGovernanceService(config.database_url, tenant_slug(tenant_id)).get_incident_lineage(str(incident_id))
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return AIOpsLineageResponse(**lineage, trace_id=_trace_id(request))

    @app.post("/api/v1/aiops/executions/{execution_id}/verify", response_model=AIOpsVerifyResponse, tags=["aiops"])
    async def aiops_verify_canary(
        execution_id: UUID,
        payload: AIOpsVerifyRequest,
        request: Request,
        tenant_id: Annotated[UUID, Header(alias="X-Tenant-Id")],
        idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=255)],
    ) -> AIOpsVerifyResponse:
        trace_id = _trace_id(request)
        require_policy(
            "aiops.canary.verify", tenant_id, trace_id, risk_class="medium", side_effects="write_data",
            arguments={"execution_id": str(execution_id), **payload.model_dump(mode="json")},
            idempotency_key=idempotency_key,
        )
        try:
            result = AIOpsGovernanceService(config.database_url, tenant_slug(tenant_id)).verify_canary(
                str(execution_id), payload.outcome, payload.reviewer_label, str(trace_id), idempotency_key
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return AIOpsVerifyResponse(**result, trace_id=trace_id)

    @app.get("/api/v1/topology/clusters", response_model=TopologyClustersResponse, tags=["topology"])
    async def topology_clusters(
        request: Request,
        tenant_id: Annotated[UUID, Header(alias="X-Tenant-Id")],
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
    ) -> TopologyClustersResponse:
        require_policy(
            "topology.cluster.read", tenant_id, _trace_id(request), risk_class="read_only", side_effects="read_only",
            arguments={"limit": limit},
        )
        try:
            items = TopologyService(config.database_url, tenant_slug=tenant_slug(tenant_id)).list_clusters(limit)
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return TopologyClustersResponse(items=items, trace_id=_trace_id(request))

    @app.get("/api/v1/topology/blueprints", response_model=TopologyBlueprintsResponse, tags=["topology"])
    async def topology_blueprints(
        request: Request,
        tenant_id: Annotated[UUID, Header(alias="X-Tenant-Id")],
        limit: Annotated[int, Query(ge=1, le=500)] = 200,
    ) -> TopologyBlueprintsResponse:
        require_policy(
            "topology.blueprint.read", tenant_id, _trace_id(request), risk_class="read_only", side_effects="read_only",
            arguments={"limit": limit},
        )
        try:
            items = TopologyService(config.database_url, tenant_slug=tenant_slug(tenant_id)).list_blueprints(limit)
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return TopologyBlueprintsResponse(items=items, trace_id=_trace_id(request))

    @app.get("/api/v1/topology/plugin-nebula", response_model=PluginNebulaResponse, tags=["topology"])
    async def topology_plugin_nebula(
        request: Request,
        tenant_id: Annotated[UUID, Header(alias="X-Tenant-Id")],
        lifecycle: Annotated[str | None, Query(pattern=r"^(verified|contract_only)$")] = None,
        domain: Annotated[str | None, Query(pattern=r"^[a-z][a-z0-9_-]{0,63}$")] = None,
    ) -> PluginNebulaResponse:
        """Dynamic knowledge-nebula graph (read-only).

        Built live from the on-disk plugin contract directory, so a newly
        added plugin appears without any frontend edit.  ``domain`` restricts the
        graph to one domain pack (``audit`` / ``aiops`` / ``quant`` / ``knowledge``);
        omit it for the audit pack, which is the default the desktop renders.
        Never executes a plugin; Policy Gateway still gates the read.
        """
        require_policy(
            "topology.plugin_nebula.read", tenant_id, _trace_id(request),
            risk_class="read_only", side_effects="read_only",
            arguments={"lifecycle": lifecycle, "domain": domain},
        )
        try:
            graph = build_nebula_graph(lifecycle=lifecycle, domain=domain)
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return PluginNebulaResponse(trace_id=_trace_id(request), **graph)

    @app.get(
        "/api/v1/topology/plugin-nebula/experience",
        response_model=NebulaExperienceResponse,
        tags=["topology"],
    )
    async def topology_plugin_nebula_experience(
        request: Request,
        tenant_id: Annotated[UUID, Header(alias="X-Tenant-Id")],
        lifecycle: Annotated[str | None, Query(pattern=r"^(verified|contract_only)$")] = None,
        domain: Annotated[str | None, Query(pattern=r"^[a-z][a-z0-9_-]{0,63}$")] = None,
    ) -> NebulaExperienceResponse:
        """Design-time nebula graph plus the run-accumulated experience overlay.

        Read-only: returns edge/node experience stats and design-external
        relation suggestions for the desktop graph to render as an overlay.
        ``domain`` scopes both the graph and the overlay to one domain pack
        (``audit`` / ``aiops`` / ``quant`` / ``knowledge``); omit it for the
        audit pack, which is the default the desktop renders.
        """
        require_policy(
            "topology.nebula_experience.read", tenant_id, _trace_id(request),
            risk_class="read_only", side_effects="read_only",
            arguments={"lifecycle": lifecycle, "domain": domain},
        )
        try:
            graph = build_nebula_graph(lifecycle=lifecycle, domain=domain)
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        from packages.experience import ExperienceProjector

        overlay = ExperienceProjector(config.database_url).read_overlay(tenant_id=tenant_id)
        if domain is not None:
            # overlay evidence is tenant-wide; scope it to the rendered graph so
            # a switched domain never mixes another pack's plugin stats in.
            node_ids = {node["id"] for node in graph["nodes"]}
            overlay["edge_stats"] = [
                e for e in overlay["edge_stats"]
                if e["source"] in node_ids and e["target"] in node_ids
            ]
            overlay["node_stats"] = [
                n for n in overlay["node_stats"] if n["plugin_id"] in node_ids
            ]
            overlay["suggestions"] = [
                s for s in overlay["suggestions"]
                if s["source"] in node_ids and s["target"] in node_ids
            ]
            overlay["used_in"] = [
                u for u in overlay["used_in"] if u["plugin_id"] in node_ids
            ]
        return NebulaExperienceResponse(
            graph=PluginNebulaResponse(trace_id=_trace_id(request), **graph),
            edge_stats=overlay["edge_stats"],
            node_stats=overlay["node_stats"],
            suggestions=overlay["suggestions"],
            used_in=overlay["used_in"],
            trace_id=_trace_id(request),
        )

    @app.get(
        "/api/v1/topology/nebula-experience/suggestions",
        response_model=ExperienceSuggestionListResponse,
        tags=["topology"],
    )
    async def list_experience_suggestions(
        request: Request,
        tenant_id: Annotated[UUID, Header(alias="X-Tenant-Id")],
        status: Annotated[str | None, Query(pattern=r"^(proposed|accepted|dismissed)$")] = None,
    ) -> ExperienceSuggestionListResponse:
        require_policy(
            "topology.nebula_experience.read", tenant_id, _trace_id(request),
            risk_class="read_only", side_effects="read_only",
            arguments={"status": status},
        )
        from packages.experience import ExperienceProjector

        overlay = ExperienceProjector(config.database_url).read_overlay(tenant_id=tenant_id)
        items = overlay["suggestions"]
        if status is not None:
            items = [s for s in items if s["status"] == status]
        return ExperienceSuggestionListResponse(items=items, trace_id=_trace_id(request))

    @app.post(
        "/api/v1/topology/nebula-experience/suggestions/{suggestion_id}/decision",
        response_model=ExperienceSuggestionDecisionResponse,
        tags=["topology"],
    )
    async def decide_experience_suggestion(
        suggestion_id: UUID,
        payload: ExperienceSuggestionDecisionRequest,
        request: Request,
        tenant_id: Annotated[UUID, Header(alias="X-Tenant-Id")],
        idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=255)],
        actor_id: Annotated[UUID | None, Header(alias="X-Actor-Id")] = None,
    ) -> ExperienceSuggestionDecisionResponse:
        """Human L2 decision on a design-external relation suggestion.

        The only structural write surface of the experience layer; gated by the
        Policy Gateway (durable decision evidence) and idempotent. The desktop
        is a single-machine shell without login, so X-Actor-Id is optional; when
        supplied it must resolve to an active principal, otherwise the decision
        is recorded as the local desktop operator.
        """
        trace_id = _trace_id(request)
        require_policy(
            "topology.nebula_experience.suggestion.write", tenant_id, trace_id,
            risk_class="low", side_effects="write_data",
            arguments={"suggestion_id": str(suggestion_id), "decision": payload.decision},
            idempotency_key=idempotency_key,
        )
        decided_by = "desktop-local"
        if actor_id is not None:
            with psycopg2.connect(config.database_url) as connection:
                _, cur = _tenant_context(connection, tenant_id)
                cur.execute(
                    "SELECT id FROM iam.principals WHERE tenant_id=%s AND id=%s AND status='active'",
                    (tenant_id, actor_id),
                )
                if cur.fetchone() is None:
                    raise HTTPException(status_code=403, detail="actor is not an active tenant principal")
            decided_by = str(actor_id)
        from packages.experience import (
            ExperienceProjector,
            IllegalSuggestionTransition,
            SuggestionNotFound,
        )

        try:
            projector = ExperienceProjector(config.database_url)
            item = projector.decide_suggestion(
                tenant_id=tenant_id,
                suggestion_id=suggestion_id,
                decision=payload.decision,
                decided_by=decided_by,
                trace_id=str(trace_id),
                idempotency_key=idempotency_key,
            )
        except SuggestionNotFound as exc:
            raise HTTPException(status_code=404, detail="relation suggestion not found") from exc
        except IllegalSuggestionTransition as exc:
            raise HTTPException(status_code=409, detail="suggestion is already decided") from exc

        # L2 exit 1 (light-solidification, design §5.5): an accepted relation is
        # immediately published through the knowledge governance pipeline so it
        # becomes a *confirmed* relation. Dismissals are never solidified. The
        # suggestion stays ``accepted`` if publication fails, so re-accepting is
        # a safe retry (solidification is idempotent on release_id).
        if payload.decision == "accepted":
            from packages.experience.solidify import (
                SolidificationError,
                solidify_accepted_suggestion,
            )

            try:
                solidify_accepted_suggestion(
                    config.database_url, tenant_id=tenant_id, suggestion_id=suggestion_id,
                )
            except SolidificationError as exc:
                raise HTTPException(
                    status_code=500,
                    detail=f"relation accepted but solidification failed: {exc}",
                ) from exc
            item = projector.get_suggestion(tenant_id=tenant_id, suggestion_id=suggestion_id)
        return ExperienceSuggestionDecisionResponse(
            item=ExperienceSuggestion.model_validate(item), trace_id=trace_id
        )

    @app.post(
        "/api/v1/topology/nebula-experience/rebuild",
        response_model=ExperienceRebuildResponse,
        tags=["topology"],
    )
    async def rebuild_experience(
        request: Request,
        tenant_id: Annotated[UUID, Header(alias="X-Tenant-Id")],
        idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=255)],
    ) -> ExperienceRebuildResponse:
        """Recompute all experience rollups from append-only observations."""
        trace_id = _trace_id(request)
        require_policy(
            "topology.nebula_experience.rebuild", tenant_id, trace_id,
            risk_class="low", side_effects="write_data",
            arguments={},
            idempotency_key=idempotency_key,
        )
        from packages.experience import ExperienceProjector

        result = ExperienceProjector(config.database_url).rebuild(tenant_id=tenant_id)
        return ExperienceRebuildResponse(edges=result["edges"], nodes=result["nodes"], trace_id=trace_id)

    @app.get("/api/v1/topology/releases", response_model=TopologyReleasesResponse, tags=["topology"])
    async def topology_releases(
        request: Request,
        tenant_id: Annotated[UUID, Header(alias="X-Tenant-Id")],
        limit: Annotated[int, Query(ge=1, le=500)] = 50,
    ) -> TopologyReleasesResponse:
        require_policy(
            "topology.plan.read", tenant_id, _trace_id(request), risk_class="read_only", side_effects="read_only",
            arguments={"limit": limit},
        )
        try:
            items = TopologyService(config.database_url, tenant_slug=tenant_slug(tenant_id)).list_releases(limit)
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return TopologyReleasesResponse(items=items, trace_id=_trace_id(request))

    @app.get("/api/v1/topology/plans", response_model=TopologyPlansResponse, tags=["topology"])
    async def topology_plans(
        request: Request,
        tenant_id: Annotated[UUID, Header(alias="X-Tenant-Id")],
        limit: Annotated[int, Query(ge=1, le=500)] = 50,
    ) -> TopologyPlansResponse:
        require_policy(
            "topology.plan.read", tenant_id, _trace_id(request), risk_class="read_only", side_effects="read_only",
            arguments={"limit": limit},
        )
        try:
            items = TopologyService(config.database_url, tenant_slug=tenant_slug(tenant_id)).list_plans(limit)
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return TopologyPlansResponse(items=items, trace_id=_trace_id(request))

    @app.get("/api/v1/topology/bridges", response_model=TopologyBridgesResponse, tags=["topology"])
    async def topology_bridges(
        request: Request,
        tenant_id: Annotated[UUID, Header(alias="X-Tenant-Id")],
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
    ) -> TopologyBridgesResponse:
        require_policy(
            "topology.bridge.read", tenant_id, _trace_id(request), risk_class="read_only", side_effects="read_only",
            arguments={"limit": limit},
        )
        try:
            items = TopologyService(config.database_url, tenant_slug=tenant_slug(tenant_id)).list_bridges(limit)
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return TopologyBridgesResponse(items=items, trace_id=_trace_id(request))

    @app.post("/api/v1/topology/chains/materialize", response_model=TopologyChainMaterializeResponse, tags=["topology"])
    async def topology_chain_materialize(
        payload: TopologyChainMaterializeRequest,
        request: Request,
        tenant_id: Annotated[UUID, Header(alias="X-Tenant-Id")],
    ) -> TopologyChainMaterializeResponse:
        """Materialize one deterministic invocation chain from a plan_only plan.

        Idempotent: a replayed ``Idempotency-Key`` returns the recorded chain;
        a different key for the same locked plan returns the same deterministic
        chain (``already_materialized=true``).  The desktop GUI is read-only and
        never calls this endpoint (control-plane only).
        """
        trace_id = _trace_id(request)
        require_policy(
            "topology.chain.write", tenant_id, trace_id, risk_class="low", side_effects="write_data",
            arguments={"plan_key": payload.plan_key},
            idempotency_key=payload.idempotency_key,
        )
        # The service re-evaluates the write under the tenant's active policy
        # rules so the durable gateway and the in-process gate stay coherent.
        rules: list[dict[str, Any]] = []
        try:
            with psycopg2.connect(config.database_url) as connection, connection.cursor() as cur:
                cur.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),))
                rules, _ = _load_policy(cur, tenant_id)
        except psycopg2.Error:
            rules = []
        try:
            result = TopologyService(
                config.database_url, policy=PolicyEngine(rules=rules), tenant_slug=tenant_slug(tenant_id)
            ).materialize_chain(
                {
                    "plan_key": payload.plan_key,
                    "idempotency_key": payload.idempotency_key,
                    "reason": payload.reason,
                },
                trace_id=trace_id,
            )
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return TopologyChainMaterializeResponse(**result, trace_id=trace_id)

    @app.get("/api/v1/topology/chains", response_model=TopologyChainsResponse, tags=["topology"])
    async def topology_chains(
        request: Request,
        tenant_id: Annotated[UUID, Header(alias="X-Tenant-Id")],
        limit: Annotated[int, Query(ge=1, le=500)] = 50,
    ) -> TopologyChainsResponse:
        require_policy(
            "topology.chain.read", tenant_id, _trace_id(request), risk_class="read_only", side_effects="read_only",
            arguments={"limit": limit},
        )
        try:
            items = TopologyService(config.database_url, tenant_slug=tenant_slug(tenant_id)).list_chains(limit)
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return TopologyChainsResponse(items=items, trace_id=_trace_id(request))

    @app.get("/api/v1/topology/chains/{chain_key}/intents", response_model=TopologyIntentsResponse, tags=["topology"])
    async def topology_chain_intents(
        chain_key: str,
        request: Request,
        tenant_id: Annotated[UUID, Header(alias="X-Tenant-Id")],
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
    ) -> TopologyIntentsResponse:
        require_policy(
            "topology.intent.read", tenant_id, _trace_id(request), risk_class="read_only", side_effects="read_only",
            arguments={"chain_key": chain_key, "limit": limit},
        )
        try:
            items = TopologyService(config.database_url, tenant_slug=tenant_slug(tenant_id)).list_intents(chain_key, limit)
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return TopologyIntentsResponse(items=items, trace_id=_trace_id(request))

    @app.post("/api/v1/topology/chains/{chain_key}/approvals", response_model=TopologyApprovalResponse, tags=["topology"])
    async def topology_chain_approval(
        chain_key: str,
        payload: TopologyApprovalRequest,
        request: Request,
        tenant_id: Annotated[UUID, Header(alias="X-Tenant-Id")],
    ) -> TopologyApprovalResponse:
        """Human approve/reject of one requires_approval intent (idempotent).

        ``approve`` moves the intent to ``approved_projection``; ``reject``
        freezes it as ``denied``.  Every decision is written to the approval
        ledger before the intent status is updated (append-only, never
        overwritten or hard-deleted).  AUTO approval is contractually banned.
        """
        trace_id = _trace_id(request)
        if payload.chain_key != chain_key:
            raise HTTPException(status_code=400, detail="chain_key in path and body must match")
        require_policy(
            "topology.chain.approve", tenant_id, trace_id, risk_class="low", side_effects="write_data",
            arguments={"chain_key": chain_key, "slot_key": payload.slot_key},
            idempotency_key=payload.idempotency_key,
        )
        rules: list[dict[str, Any]] = []
        try:
            with psycopg2.connect(config.database_url) as connection, connection.cursor() as cur:
                cur.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),))
                rules, _ = _load_policy(cur, tenant_id)
        except psycopg2.Error:
            rules = []
        try:
            result = TopologyService(
                config.database_url, policy=PolicyEngine(rules=rules), tenant_slug=tenant_slug(tenant_id)
            ).approve_intent(
                {
                    "chain_key": payload.chain_key,
                    "slot_key": payload.slot_key,
                    "decision": payload.decision,
                    "idempotency_key": payload.idempotency_key,
                    "reason": payload.reason,
                    "created_by": str(tenant_id),
                },
                trace_id=trace_id,
            )
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return TopologyApprovalResponse(**result, trace_id=trace_id)

    @app.get("/api/v1/topology/chains/{chain_key}/approvals", response_model=TopologyApprovalsResponse, tags=["topology"])
    async def topology_chain_approvals(
        chain_key: str,
        request: Request,
        tenant_id: Annotated[UUID, Header(alias="X-Tenant-Id")],
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
    ) -> TopologyApprovalsResponse:
        require_policy(
            "topology.execution.read", tenant_id, _trace_id(request), risk_class="read_only", side_effects="read_only",
            arguments={"chain_key": chain_key, "limit": limit},
        )
        try:
            items = TopologyService(config.database_url, tenant_slug=tenant_slug(tenant_id)).list_approvals(chain_key, limit)
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return TopologyApprovalsResponse(items=items, trace_id=_trace_id(request))

    @app.post("/api/v1/topology/chains/{chain_key}/executions", response_model=TopologyExecutionResponse, tags=["topology"])
    async def topology_chain_execute(
        chain_key: str,
        payload: TopologyExecutionRequest,
        request: Request,
        tenant_id: Annotated[UUID, Header(alias="X-Tenant-Id")],
    ) -> TopologyExecutionResponse:
        """Whole-chain execution: ``simulated`` (DB-only projection) or ``isolated``.

        Fail-closed: any denied or not-yet-approved intent blocks the whole
        chain.  ``isolated`` additionally requires the
        ``topology.chain.execute.isolated`` policy and a declared reason, and
        only runs verified read-only built-in plugins in a restricted child
        process; a failed node stops every later node and is recorded as-is.
        """
        trace_id = _trace_id(request)
        if payload.chain_key != chain_key:
            raise HTTPException(status_code=400, detail="chain_key in path and body must match")
        require_policy(
            "topology.chain.execute", tenant_id, trace_id, risk_class="medium", side_effects="write_data",
            arguments={"chain_key": chain_key},
            idempotency_key=payload.idempotency_key,
        )
        if payload.mode == "isolated":
            if not (payload.reason or "").strip():
                raise HTTPException(status_code=400, detail="isolated execution requires a reason")
            require_policy(
                "topology.chain.execute.isolated", tenant_id, trace_id, risk_class="medium",
                side_effects="write_data", arguments={"chain_key": chain_key},
                idempotency_key=payload.idempotency_key,
            )
        rules: list[dict[str, Any]] = []
        try:
            with psycopg2.connect(config.database_url) as connection, connection.cursor() as cur:
                cur.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),))
                rules, _ = _load_policy(cur, tenant_id)
        except psycopg2.Error:
            rules = []
        try:
            service = TopologyService(
                config.database_url, policy=PolicyEngine(rules=rules), tenant_slug=tenant_slug(tenant_id)
            )
            request_payload = {
                "chain_key": payload.chain_key,
                "mode": payload.mode,
                "idempotency_key": payload.idempotency_key,
                "reason": payload.reason,
            }
            result = (
                service.execute_chain_isolated(request_payload, trace_id=trace_id)
                if payload.mode == "isolated"
                else service.execute_chain(request_payload, trace_id=trace_id)
            )
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return TopologyExecutionResponse(
            **{k: v for k, v in result.items() if k != "entries"},
            entries=[dict(item) for item in result.get("entries", [])],
            trace_id=trace_id,
        )

    @app.get("/api/v1/topology/chains/{chain_key}/executions", response_model=TopologyExecutionsResponse, tags=["topology"])
    async def topology_chain_executions(
        chain_key: str,
        request: Request,
        tenant_id: Annotated[UUID, Header(alias="X-Tenant-Id")],
        limit: Annotated[int, Query(ge=1, le=10000)] = 200,
    ) -> TopologyExecutionsResponse:
        require_policy(
            "topology.execution.read", tenant_id, _trace_id(request), risk_class="read_only", side_effects="read_only",
            arguments={"chain_key": chain_key, "limit": limit},
        )
        try:
            items = TopologyService(config.database_url, tenant_slug=tenant_slug(tenant_id)).list_executions(chain_key, limit)
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return TopologyExecutionsResponse(items=items, trace_id=_trace_id(request))

    @app.post("/api/v1/topology/chains/{chain_key}/runs", response_model=TopologyRunResponse, tags=["topology"])
    async def topology_chain_run(
        chain_key: str,
        payload: TopologyRunRequest,
        request: Request,
        tenant_id: Annotated[UUID, Header(alias="X-Tenant-Id")],
    ) -> TopologyRunResponse:
        """M6 chain-level isolated run: preflight -> running -> success | failed.

        One run is a single read-only isolated execution of a release-locked
        chain with a fresh ``run_id``; the per-node ledger rows are grouped by
        that ``run_id``.  Fail-closed: the chain must be release-locked, every
        intent must be past its approval gate, and the tenant policy must
        freshly grant both ``topology.chain.execute`` and
        ``topology.chain.execute.isolated``.  A second run while one is still
        ``running`` on the same chain is rejected with 409; re-using the same
        idempotency key replays the existing run result.
        """
        trace_id = _trace_id(request)
        if payload.chain_key != chain_key:
            raise HTTPException(status_code=400, detail="chain_key in path and body must match")
        require_policy(
            "topology.chain.execute", tenant_id, trace_id, risk_class="medium", side_effects="write_data",
            arguments={"chain_key": chain_key},
            idempotency_key=payload.idempotency_key,
        )
        require_policy(
            "topology.chain.execute.isolated", tenant_id, trace_id, risk_class="medium",
            side_effects="write_data", arguments={"chain_key": chain_key},
            idempotency_key=payload.idempotency_key,
        )
        rules: list[dict[str, Any]] = []
        try:
            with psycopg2.connect(config.database_url) as connection, connection.cursor() as cur:
                cur.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),))
                rules, _ = _load_policy(cur, tenant_id)
        except psycopg2.Error:
            rules = []
        try:
            result = TopologyService(
                config.database_url, policy=PolicyEngine(rules=rules), tenant_slug=tenant_slug(tenant_id)
            ).start_run(
                {
                    "chain_key": payload.chain_key,
                    "mode": payload.mode,
                    "reason": payload.reason,
                    "idempotency_key": payload.idempotency_key,
                },
                trace_id=trace_id,
            )
        except RunConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return TopologyRunResponse(
            **{k: v for k, v in result.items() if k not in {"entries", "idempotent"}},
            entries=[dict(item) for item in result.get("entries", [])],
            idempotent=bool(result.get("idempotent")),
            trace_id=trace_id,
        )

    @app.get("/api/v1/topology/chains/{chain_key}/runs", response_model=TopologyRunsResponse, tags=["topology"])
    async def topology_chain_runs(
        chain_key: str,
        request: Request,
        tenant_id: Annotated[UUID, Header(alias="X-Tenant-Id")],
        limit: Annotated[int, Query(ge=1, le=500)] = 50,
    ) -> TopologyRunsResponse:
        require_policy(
            "topology.execution.read", tenant_id, _trace_id(request), risk_class="read_only", side_effects="read_only",
            arguments={"chain_key": chain_key, "limit": limit},
        )
        try:
            items = TopologyService(config.database_url, tenant_slug=tenant_slug(tenant_id)).list_runs(chain_key, limit)
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return TopologyRunsResponse(items=items, trace_id=_trace_id(request))

    @app.get("/api/v1/topology/runs", tags=["topology"])
    async def topology_runs_feed(
        request: Request,
        tenant_id: Annotated[UUID, Header(alias="X-Tenant-Id")],
        limit: Annotated[int, Query(ge=1, le=500)] = 50,
    ) -> dict[str, Any]:
        """CW4 runs feed: persisted plan runs with aggregated attempt state."""
        require_policy(
            "topology.execution.read", tenant_id, _trace_id(request), risk_class="read_only", side_effects="read_only",
            arguments={"limit": limit},
        )
        try:
            items = TopologyService(config.database_url).list_plan_runs(tenant_id, limit)
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"items": items, "trace_id": _trace_id(request)}

    @app.get("/api/v1/topology/canvas/{run_id}", tags=["topology"])
    async def topology_canvas_projection(
        run_id: UUID,
        request: Request,
        tenant_id: Annotated[UUID, Header(alias="X-Tenant-Id")],
    ) -> dict[str, Any]:
        """CW4 canvas definition: attempt nodes + data edges for one run."""
        require_policy(
            "topology.execution.read", tenant_id, _trace_id(request), risk_class="read_only", side_effects="read_only",
            arguments={"run_id": str(run_id)},
        )
        try:
            projection = TopologyService(config.database_url).canvas_projection(tenant_id, str(run_id))
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (OSError,) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return projection

    @app.post(
        "/api/v1/topology/runs/{run_id}/archive",
        response_model=ArchiveRunResponse,
        tags=["topology"],
    )
    async def topology_archive_run(
        run_id: UUID,
        payload: ArchiveRunRequest,
        request: Request,
        tenant_id: Annotated[UUID, Header(alias="X-Tenant-Id")],
        idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=255)],
    ) -> ArchiveRunResponse:
        """Anchor a finished run to the business project it was performed for.

        This is what turns a run into reusable knowledge: once anchored, the
        plugins the run used become evidence for that project, and the graph can
        answer "which projects has this plugin version been proven in?".
        Write-gated (``topology.run.archive``) and idempotent; the link is
        append-only evidence, never rewritten.
        """
        trace_id = _trace_id(request)
        require_policy(
            "topology.run.archive", tenant_id, trace_id,
            risk_class="low", side_effects="write_data",
            arguments={"run_id": str(run_id), "project_id": str(payload.project_id)},
            idempotency_key=idempotency_key,
        )
        from packages.experience import ExperienceProjector

        try:
            link = ExperienceProjector(config.database_url).archive_run(
                tenant_id=tenant_id,
                run_id=run_id,
                project_id=payload.project_id,
                trace_id=str(trace_id),
                note=payload.note,
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return ArchiveRunResponse(
            run_id=run_id,
            project_id=payload.project_id,
            archive_link_id=UUID(link["archive_link_id"]),
            archived_at=link["archived_at"],
            idempotent=bool(link["idempotent"]),
            trace_id=trace_id,
        )

    @app.post("/api/v1/topology/planning/ai", response_model=AiPlanningResponse, tags=["topology"])
    async def topology_ai_planning(
        payload: AiPlanningRequest,
        request: Request,
        tenant_id: Annotated[UUID, Header(alias="X-Tenant-Id")],
    ) -> AiPlanningResponse:
        """CW5 AI auto-networking: recall -> structured draft -> bounded revision.

        Plan-only: the outcome is a draft or an honest gap report.  Executing
        the draft still requires the CW3 run gate (``topology.chain.execute``)
        — this route grants no execution permission.
        """
        require_policy(
            "topology.planning.ai", tenant_id, _trace_id(request), risk_class="read_only", side_effects="read_only",
            arguments={"goal": payload.goal[:200]},
        )
        try:
            catalog, port_contracts = _planning_directory(
                config.database_url, tenant_id, payload.domain,
                intent_text=_payload_intent(payload),
            )
        except psycopg2.Error as exc:
            raise HTTPException(status_code=503, detail="capability directory unavailable") from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=422, detail=f"unknown domain: {payload.domain}") from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        authorized: set[tuple[str, str]] = set()
        for pair in payload.data_sources:
            if len(pair) != 2 or not pair[0] or not pair[1]:
                raise HTTPException(status_code=422, detail="data_sources entries must be [node_instance_id, port_id]")
            authorized.add((pair[0], pair[1]))
        try:
            outcome = ai_planner_module.AiPlanner(ai_planner_module._default_llm()).plan(
                goal=payload.goal,
                catalog=catalog,
                authorized_sources=authorized,
                budget=payload.budget,
                template_keys=tuple(payload.template_keys or ()),
                port_contracts=port_contracts,
            )
        except (AIClientError, OSError) as exc:
            raise HTTPException(status_code=503, detail=f"model backend unavailable: {exc}") from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return AiPlanningResponse(
            status=outcome.status,
            plan_key=outcome.plan_key,
            revisions=outcome.revisions,
            draft=outcome.draft,
            issues=outcome.issues,
        )

    @app.post("/api/v1/topology/canvas/chat", response_model=CanvasChatResponse, tags=["topology"])
    async def topology_canvas_chat(
        payload: CanvasChatRequest,
        request: Request,
        tenant_id: Annotated[UUID, Header(alias="X-Tenant-Id")],
    ) -> CanvasChatResponse:
        """CW5 canvas chat: message (history + optional base_draft) -> graph flow.

        Plan-only: the model drafts a structured flow which must pass the
        capability whitelist, data boundary and deterministic compiler
        (``AiPlanner``).  No execution permission is granted here — running
        still requires the CW3 chain gate.  Every chat request is recorded as
        an append-only ``topology.planning_intents`` evidence row gated by
        ``topology.intent.plan`` (fail-closed, seeded inactive).
        """
        trace_id = _trace_id(request)
        require_policy(
            "topology.intent.plan", tenant_id, trace_id, risk_class="low",
            side_effects="write_data", arguments={"intent": payload.message[:80]},
            idempotency_key=payload.idempotency_key,
        )
        try:
            catalog, port_contracts = _planning_directory(
                config.database_url, tenant_id, payload.domain,
                intent_text=_payload_intent(payload),
            )
        except psycopg2.Error as exc:
            raise HTTPException(status_code=503, detail="capability directory unavailable") from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=422, detail=f"unknown domain: {payload.domain}") from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        authorized: set[tuple[str, str]] = set()
        for pair in payload.data_sources:
            if len(pair) != 2 or not pair[0] or not pair[1]:
                raise HTTPException(status_code=422, detail="data_sources entries must be [node_instance_id, port_id]")
            authorized.add((pair[0], pair[1]))
        try:
            planner = ai_chat_module.CanvasChatPlanner(
                ai_planner_module.AiPlanner(ai_planner_module._default_llm())
            )
            outcome = planner.chat(
                message=payload.message,
                catalog=catalog,
                authorized_sources=authorized,
                history=[turn.model_dump() for turn in payload.history],
                base_draft=payload.base_draft,
                budget=payload.budget,
                template_keys=tuple(payload.template_keys or ()),
                port_contracts=port_contracts,
                capability_prior=_canvas_capability_prior(config.database_url, tenant_id),
            )
        except (AIClientError, OSError) as exc:
            detail = f"model backend unavailable: {exc}"
            failure_intent_id = _record_canvas_chat_failure(
                config.database_url, tenant_id, payload, trace_id, detail
            )
            raise HTTPException(
                status_code=503,
                detail=_canvas_chat_failure_detail(detail, failure_intent_id),
            ) from exc
        except ValueError as exc:
            detail = str(exc)
            failure_intent_id = _record_canvas_chat_failure(
                config.database_url, tenant_id, payload, trace_id, detail
            )
            raise HTTPException(
                status_code=422,
                detail=_canvas_chat_failure_detail(detail, failure_intent_id),
            ) from exc
        intent_id: str | None = None
        try:
            intent_id = _record_canvas_chat_intent(
                config.database_url, tenant_id, payload, outcome, trace_id
            )
        except psycopg2.Error as exc:
            raise HTTPException(status_code=503, detail="chat evidence record unavailable") from exc
        return CanvasChatResponse(
            status=outcome.status,
            plan_key=outcome.plan_key,
            revisions=outcome.revisions,
            draft=outcome.draft,
            issues=outcome.issues,
            session_id=payload.session_id or f"chat-{uuid4().hex[:12]}",
            reply_text=ai_chat_module.reply_text_for(outcome),
            intent_id=intent_id,
            backend=os.getenv("AI_PLANNER_BACKEND", "openai_compat"),
        )

    @app.post("/api/v1/topology/canvas/chat/stream", tags=["topology"])
    async def topology_canvas_chat_stream(
        payload: CanvasChatRequest,
        request: Request,
        tenant_id: Annotated[UUID, Header(alias="X-Tenant-Id")],
    ) -> StreamingResponse:
        """CW5 canvas chat (SSE): same plan-only flow as ``canvas/chat`` but the
        planner emits live ``stage`` events (capability_recall / llm_draft /
        validate / compile) before the terminal ``done`` or ``error`` event.

        Events: ``event: stage``, ``event: done`` (CanvasChatResponse JSON),
        ``event: error`` (``{"detail": ...}``).  All writes stay append-only
        evidence; the stream itself grants no execution permission.
        """
        trace_id = _trace_id(request)
        require_policy(
            "topology.intent.plan", tenant_id, trace_id, risk_class="low",
            side_effects="write_data", arguments={"intent": payload.message[:80]},
            idempotency_key=payload.idempotency_key,
        )
        try:
            catalog, port_contracts = _planning_directory(
                config.database_url, tenant_id, payload.domain,
                intent_text=_payload_intent(payload),
            )
        except psycopg2.Error as exc:
            raise HTTPException(status_code=503, detail="capability directory unavailable") from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=422, detail=f"unknown domain: {payload.domain}") from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        authorized: set[tuple[str, str]] = set()
        for pair in payload.data_sources:
            if len(pair) != 2 or not pair[0] or not pair[1]:
                raise HTTPException(status_code=422, detail="data_sources entries must be [node_instance_id, port_id]")
            authorized.add((pair[0], pair[1]))
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

        def emit(event: dict[str, Any]) -> None:
            loop.call_soon_threadsafe(queue.put_nowait, event)

        def worker() -> None:
            try:
                planner = ai_chat_module.CanvasChatPlanner(
                    ai_planner_module.AiPlanner(ai_planner_module._default_llm())
                )
                outcome = planner.chat(
                    message=payload.message,
                    catalog=catalog,
                    authorized_sources=authorized,
                    history=[turn.model_dump() for turn in payload.history],
                    base_draft=payload.base_draft,
                    budget=payload.budget,
                    template_keys=tuple(payload.template_keys or ()),
                    on_progress=lambda event: emit({"type": "stage", "data": event}),
                    port_contracts=port_contracts,
                    capability_prior=_canvas_capability_prior(config.database_url, tenant_id),
                )
                intent_id: str | None = None
                try:
                    intent_id = _record_canvas_chat_intent(
                        config.database_url, tenant_id, payload, outcome, trace_id
                    )
                except psycopg2.Error as exc:
                    emit({"type": "error", "data": {"detail": f"chat evidence record unavailable: {exc}"}})
                    return
                emit({"type": "done", "data": {
                    "status": outcome.status,
                    "plan_key": outcome.plan_key,
                    "revisions": outcome.revisions,
                    "draft": outcome.draft,
                    "issues": outcome.issues,
                    "session_id": payload.session_id or f"chat-{uuid4().hex[:12]}",
                    "reply_text": ai_chat_module.reply_text_for(outcome),
                    "intent_id": intent_id,
                    "backend": os.getenv("AI_PLANNER_BACKEND", "openai_compat"),
                }})
            except (AIClientError, OSError) as exc:
                detail = f"model backend unavailable: {exc}"
                failure_intent_id = _record_canvas_chat_failure(
                    config.database_url, tenant_id, payload, trace_id, detail
                )
                emit({"type": "error", "data": {
                    "detail": _canvas_chat_failure_detail(detail, failure_intent_id),
                    "intent_id": failure_intent_id,
                }})
            except ValueError as exc:
                detail = str(exc)
                failure_intent_id = _record_canvas_chat_failure(
                    config.database_url, tenant_id, payload, trace_id, detail
                )
                emit({"type": "error", "data": {
                    "detail": _canvas_chat_failure_detail(detail, failure_intent_id),
                    "intent_id": failure_intent_id,
                }})
            except Exception as exc:  # noqa: BLE001 — stream never dies silently
                detail = f"planning failed: {exc}"
                failure_intent_id = _record_canvas_chat_failure(
                    config.database_url, tenant_id, payload, trace_id, detail
                )
                emit({"type": "error", "data": {
                    "detail": _canvas_chat_failure_detail(detail, failure_intent_id),
                    "intent_id": failure_intent_id,
                }})

        threading.Thread(target=worker, daemon=True).start()

        async def event_generator() -> AsyncIterator[str]:
            while True:
                item = await queue.get()
                yield f"event: {item['type']}\ndata: {json.dumps(item['data'], ensure_ascii=False)}\n\n"
                if item["type"] in ("done", "error"):
                    break

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/v1/observability/spool/health", tags=["observability"])
    async def observability_spool_health(
        request: Request,
        tenant_id: Annotated[UUID, Header(alias="X-Tenant-Id")],
    ) -> dict[str, Any]:
        """CW6: deterministic spool health + watermark alerts (24x7 readiness).

        Thresholds come from SPOOL_MAX_BYTES / SPOOL_MAX_SEGMENTS /
        SPOOL_MAX_HOT_AGE_SECONDS / SPOOL_MAX_HOT_BYTES env vars; unset
        thresholds report no alerts (monitoring can still see the raw health).
        """
        require_policy(
            "observability.spool.read", tenant_id, _trace_id(request), risk_class="read_only", side_effects="read_only",
        )
        from packages.observability.spool_ops import spool_health, watermark_alerts

        def _env_int(name: str) -> int | None:
            raw = os.getenv(name, "").strip()
            if not raw:
                return None
            try:
                return max(0, int(raw))
            except ValueError:
                return None

        data_dir = Path(__file__).resolve().parents[2] / ".data"
        health = spool_health(_spool_root(data_dir))
        alerts = watermark_alerts(
            health,
            max_bytes=_env_int("SPOOL_MAX_BYTES"),
            max_segments=_env_int("SPOOL_MAX_SEGMENTS"),
            max_hot_age_seconds=_env_int("SPOOL_MAX_HOT_AGE_SECONDS"),
            max_hot_bytes=_env_int("SPOOL_MAX_HOT_BYTES"),
        )
        return {
            "health": health.as_dict(),
            "alerts": [alert.as_dict() for alert in alerts],
            "trace_id": _trace_id(request),
        }

    @app.get("/api/v1/observability/evidence/{run_id}", tags=["observability"])
    async def observability_evidence_export(
        run_id: UUID,
        request: Request,
        tenant_id: Annotated[UUID, Header(alias="X-Tenant-Id")],
    ) -> FileResponse:
        """CW6: export one run's verifiable evidence bundle (zip + sha manifest).

        Plan/nodes/edges/attempts come from the same projection the canvas
        shows; log segments are copied from the spool (best effort); the
        manifest lets any auditor re-verify every hash offline.
        """
        require_policy(
            "observability.evidence.export", tenant_id, _trace_id(request),
            risk_class="read_only", side_effects="read_only",
            arguments={"run_id": str(run_id)},
        )
        from packages.observability.evidence import export_evidence_bundle

        data_dir = Path(__file__).resolve().parents[2] / ".data"
        try:
            projection = TopologyService(config.database_url).canvas_projection(tenant_id, str(run_id))
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except OSError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        artifacts: list[dict[str, Any]] = []
        for node in projection["nodes"]:
            for ref in (node.get("output_refs") or {}).values():
                if not isinstance(ref, dict) or not ref.get("uri"):
                    continue
                uri = str(ref["uri"])
                local = Path(url2pathname(uri[len("file://"):] if uri.startswith("file://") else uri))
                artifacts.append({
                    "name": f"{node['node_instance_id']}-{local.name}",
                    "path": str(local),
                    "media_type": str(ref.get("media_type") or "application/octet-stream"),
                    "sha256": str(ref.get("sha256") or ""),
                    "size_bytes": int(ref.get("size_bytes") or 0),
                })

        log_segments: list[tuple[str, str]] = []
        spool_root = _spool_root(data_dir)
        try:
            for meta in SegmentedSpool(spool_root).segments():
                if meta.archive_state == "archived":
                    path = spool_root / "archive" / meta.producer_id / f"e{meta.producer_epoch}" / f"{meta.segment_id}.gz"
                else:
                    path = spool_root / "hot" / meta.producer_id / f"e{meta.producer_epoch}" / f"{meta.segment_id}.jsonl"
                if path.is_file():
                    log_segments.append((str(path), f"{meta.producer_id}-e{meta.producer_epoch}-{meta.segment_id}"))
        except OSError:
            log_segments = []

        out_dir = data_dir / "evidence"
        out_dir.mkdir(parents=True, exist_ok=True)
        bundle = out_dir / f"{run_id}.zip"
        export_evidence_bundle(
            bundle,
            run_id=str(run_id),
            plan_key=projection["plan_key"],
            trace_id=projection["trace_id"],
            plan=projection,
            attempts=projection["nodes"],
            edges=projection["edges"],
            log_segments=log_segments,
            artifacts=artifacts,
        )
        return FileResponse(
            bundle, media_type="application/zip", filename=f"{run_id}.zip",
            headers={"X-Evidence-Manifest-Sha256": _sha256_of_file(bundle)},
        )

    @app.get("/api/v1/topology/runs/{run_id}", response_model=TopologyRunResponse, tags=["topology"])
    async def topology_run_detail(
        run_id: UUID,
        request: Request,
        tenant_id: Annotated[UUID, Header(alias="X-Tenant-Id")],
    ) -> TopologyRunResponse:
        """One run projection plus its grouped per-node ledger entries."""
        require_policy(
            "topology.execution.read", tenant_id, _trace_id(request), risk_class="read_only", side_effects="read_only",
            arguments={"run_id": str(run_id)},
        )
        try:
            result = TopologyService(config.database_url, tenant_slug=tenant_slug(tenant_id)).get_run(run_id)
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return TopologyRunResponse(
            **{k: v for k, v in result.items() if k != "entries"},
            entries=[dict(item) for item in result.get("entries", [])],
            trace_id=_trace_id(request),
        )

    @app.post(
        "/api/v1/topology/runs/{run_id}/verifications",
        response_model=TopologyRunVerificationResponse,
        tags=["topology"],
    )
    async def topology_run_verification(
        run_id: UUID,
        payload: TopologyVerificationRequest,
        request: Request,
        tenant_id: Annotated[UUID, Header(alias="X-Tenant-Id")],
    ) -> TopologyRunVerificationResponse:
        """M7 run verification: pure-DB reproducibility compare + verdict row.

        Fail-closed: the run must be ``success`` (else 409), an explicit
        reference must exist / be success / belong to the same tenant and
        chain (else 409), and the tenant policy must freshly grant
        ``topology.chain.verify`` (else 403).  The comparator spawns no child
        process; ``rollback_verdict`` is advisory only and performs no action.
        """
        trace_id = _trace_id(request)
        require_policy(
            "topology.chain.verify", tenant_id, trace_id, risk_class="medium", side_effects="write_data",
            arguments={"run_id": str(run_id)},
            idempotency_key=payload.idempotency_key,
        )
        rules: list[dict[str, Any]] = []
        try:
            with psycopg2.connect(config.database_url) as connection, connection.cursor() as cur:
                cur.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),))
                rules, _ = _load_policy(cur, tenant_id)
        except psycopg2.Error:
            rules = []
        try:
            result = TopologyService(
                config.database_url, policy=PolicyEngine(rules=rules), tenant_slug=tenant_slug(tenant_id)
            ).verify_chain_run(
                {
                    "run_id": str(run_id),
                    "reference_run_id": str(payload.reference_run_id) if payload.reference_run_id else None,
                    "reason": payload.reason,
                    "idempotency_key": payload.idempotency_key,
                },
                trace_id=trace_id,
            )
        except RunVerificationError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except VerificationConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return TopologyRunVerificationResponse(
            **{k: v for k, v in result.items() if k not in {"idempotent"}},
            idempotent=bool(result.get("idempotent")),
            trace_id=trace_id,
        )

    @app.get(
        "/api/v1/topology/runs/{run_id}/verifications",
        response_model=TopologyVerificationsResponse,
        tags=["topology"],
    )
    async def topology_run_verifications(
        run_id: UUID,
        request: Request,
        tenant_id: Annotated[UUID, Header(alias="X-Tenant-Id")],
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
    ) -> TopologyVerificationsResponse:
        require_policy(
            "topology.execution.read", tenant_id, _trace_id(request), risk_class="read_only", side_effects="read_only",
            arguments={"run_id": str(run_id), "limit": limit},
        )
        try:
            items = TopologyService(config.database_url, tenant_slug=tenant_slug(tenant_id)).list_chain_verifications(
                run_id, limit
            )
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return TopologyVerificationsResponse(items=items, trace_id=_trace_id(request))

    @app.get(
        "/api/v1/topology/verifications/{verification_id}",
        response_model=TopologyRunVerificationResponse,
        tags=["topology"],
    )
    async def topology_verification_detail(
        verification_id: UUID,
        request: Request,
        tenant_id: Annotated[UUID, Header(alias="X-Tenant-Id")],
    ) -> TopologyRunVerificationResponse:
        """One verification projection (incl. the read-only rollback verdict)."""
        require_policy(
            "topology.execution.read", tenant_id, _trace_id(request), risk_class="read_only", side_effects="read_only",
            arguments={"verification_id": str(verification_id)},
        )
        try:
            result = TopologyService(config.database_url, tenant_slug=tenant_slug(tenant_id)).get_run_verification(
                verification_id
            )
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return TopologyRunVerificationResponse(
            **{k: v for k, v in result.items() if k != "idempotent"},
            idempotent=False,
            trace_id=_trace_id(request),
        )

    @app.post(
        "/api/v1/topology/verifications/{verification_id}/remediation",
        response_model=TopologyRemediationProposalResponse,
        tags=["topology"],
    )
    async def topology_verification_remediation_create(
        verification_id: UUID,
        payload: TopologyRemediationProposalRequest,
        request: Request,
        tenant_id: Annotated[UUID, Header(alias="X-Tenant-Id")],
    ) -> TopologyRemediationProposalResponse:
        """M8: open a remediation proposal for one drifted verification.

        Fail-closed: the verification must exist in the tenant and be
        ``drifted`` with a non-empty rollback verdict (else 409), and the
        tenant policy must freshly grant ``topology.chain.remediate`` (else
        403).  Pure-DB evidence projection: no execution surface is added and
        no child process is spawned.
        """
        trace_id = _trace_id(request)
        require_policy(
            "topology.chain.remediate", tenant_id, trace_id, risk_class="medium",
            side_effects="write_data", arguments={"verification_id": str(verification_id)},
            idempotency_key=payload.idempotency_key,
        )
        rules: list[dict[str, Any]] = []
        try:
            with psycopg2.connect(config.database_url) as connection, connection.cursor() as cur:
                cur.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),))
                rules, _ = _load_policy(cur, tenant_id)
        except psycopg2.Error:
            rules = []
        try:
            result = TopologyService(
                config.database_url, policy=PolicyEngine(rules=rules), tenant_slug=tenant_slug(tenant_id)
            ).create_remediation_proposal(
                {
                    "verification_id": str(verification_id),
                    "action": payload.action,
                    "reason": payload.reason,
                    "idempotency_key": payload.idempotency_key,
                },
                trace_id=trace_id,
            )
        except RemediationError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except RemediationConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return TopologyRemediationProposalResponse(
            **{k: v for k, v in result.items() if k not in {"idempotent", "decision"}},
            idempotent=bool(result.get("idempotent")),
            trace_id=trace_id,
        )

    @app.post(
        "/api/v1/topology/remediation/{proposal_id}/decisions",
        response_model=TopologyRemediationProposalResponse,
        tags=["topology"],
    )
    async def topology_remediation_decide(
        proposal_id: UUID,
        payload: TopologyRemediationDecisionRequest,
        request: Request,
        tenant_id: Annotated[UUID, Header(alias="X-Tenant-Id")],
    ) -> TopologyRemediationProposalResponse:
        """M8: record one terminal approve/reject/close decision (append-only).

        The decision lands in the immutable ``remediation_decisions`` ledger;
        terminal status is derived, never stored by UPDATE.  Once finalized
        the proposal cannot flip (different decision -> 409); replaying the
        same decision + idempotency key returns the existing projection.
        """
        trace_id = _trace_id(request)
        require_policy(
            "topology.chain.remediate", tenant_id, trace_id, risk_class="medium",
            side_effects="write_data", arguments={"proposal_id": str(proposal_id)},
            idempotency_key=payload.idempotency_key,
        )
        rules: list[dict[str, Any]] = []
        try:
            with psycopg2.connect(config.database_url) as connection, connection.cursor() as cur:
                cur.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),))
                rules, _ = _load_policy(cur, tenant_id)
        except psycopg2.Error:
            rules = []
        try:
            result = TopologyService(
                config.database_url, policy=PolicyEngine(rules=rules), tenant_slug=tenant_slug(tenant_id)
            ).decide_remediation(
                proposal_id,
                decision=payload.decision,
                approver=payload.approver,
                reason=payload.reason,
                idempotency_key=payload.idempotency_key,
                trace_id=trace_id,
            )
        except RemediationConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return TopologyRemediationProposalResponse(
            **{k: v for k, v in result.items() if k not in {"idempotent", "decision"}},
            idempotent=bool(result.get("idempotent")),
            trace_id=trace_id,
        )

    @app.post(
        "/api/v1/topology/remediation/{proposal_id}/runs",
        response_model=TopologyRemediationRunResponse,
        tags=["topology"],
    )
    async def topology_remediation_run(
        proposal_id: UUID,
        payload: TopologyRemediationRunRequest,
        request: Request,
        tenant_id: Annotated[UUID, Header(alias="X-Tenant-Id")],
    ) -> TopologyRemediationRunResponse:
        """M8: governed re-run for an approved re-run-locked-release proposal.

        Reuses the whole M6 ``start_run`` machinery (execute + isolated policy
        gates, fail-closed preflight, exclusive begin, per-node ``python -I``
        read-only isolation, CAS finalize) unchanged; only the proposal -> run
        lineage row is added.  Non-approved or non-re-run proposals fail
        closed (409) with zero runs.
        """
        trace_id = _trace_id(request)
        require_policy(
            "topology.chain.remediate", tenant_id, trace_id, risk_class="medium",
            side_effects="write_data", arguments={"proposal_id": str(proposal_id)},
            idempotency_key=payload.idempotency_key,
        )
        require_policy(
            "topology.chain.execute", tenant_id, trace_id, risk_class="medium",
            side_effects="write_data", arguments={"proposal_id": str(proposal_id)},
            idempotency_key=payload.idempotency_key,
        )
        require_policy(
            "topology.chain.execute.isolated", tenant_id, trace_id, risk_class="medium",
            side_effects="write_data", arguments={"proposal_id": str(proposal_id)},
            idempotency_key=payload.idempotency_key,
        )
        rules: list[dict[str, Any]] = []
        try:
            with psycopg2.connect(config.database_url) as connection, connection.cursor() as cur:
                cur.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),))
                rules, _ = _load_policy(cur, tenant_id)
        except psycopg2.Error:
            rules = []
        try:
            result = TopologyService(
                config.database_url, policy=PolicyEngine(rules=rules), tenant_slug=tenant_slug(tenant_id)
            ).remediate_run(
                proposal_id,
                reason=payload.reason,
                idempotency_key=payload.idempotency_key,
                trace_id=trace_id,
            )
        except RemediationConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except RunConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return TopologyRemediationRunResponse(
            **{k: v for k, v in result.items() if k not in {"entries", "idempotent"}},
            entries=[dict(item) for item in result.get("entries", [])],
            idempotent=bool(result.get("idempotent")),
            trace_id=trace_id,
        )

    @app.get("/api/v1/topology/remediation", response_model=TopologyRemediationProposalsResponse, tags=["topology"])
    async def topology_remediation_list(
        request: Request,
        tenant_id: Annotated[UUID, Header(alias="X-Tenant-Id")],
        run_id: UUID | None = None,
        verification_id: UUID | None = None,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
    ) -> TopologyRemediationProposalsResponse:
        require_policy(
            "topology.execution.read", tenant_id, _trace_id(request), risk_class="read_only",
            side_effects="read_only",
            arguments={
                "run_id": str(run_id) if run_id else None,
                "verification_id": str(verification_id) if verification_id else None,
                "limit": limit,
            },
        )
        try:
            items = TopologyService(config.database_url, tenant_slug=tenant_slug(tenant_id)).list_remediation_proposals(
                run_id=run_id, verification_id=verification_id, limit=limit
            )
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return TopologyRemediationProposalsResponse(items=items, trace_id=_trace_id(request))

    @app.get(
        "/api/v1/topology/remediation/{proposal_id}",
        response_model=TopologyRemediationProposalResponse,
        tags=["topology"],
    )
    async def topology_remediation_detail(
        proposal_id: UUID,
        request: Request,
        tenant_id: Annotated[UUID, Header(alias="X-Tenant-Id")],
    ) -> TopologyRemediationProposalResponse:
        """One proposal projection (status + re-run lineage derived from ledgers)."""
        require_policy(
            "topology.execution.read", tenant_id, _trace_id(request), risk_class="read_only",
            side_effects="read_only", arguments={"proposal_id": str(proposal_id)},
        )
        try:
            result = TopologyService(config.database_url, tenant_slug=tenant_slug(tenant_id)).get_remediation_proposal(
                proposal_id
            )
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return TopologyRemediationProposalResponse(
            **{k: v for k, v in result.items() if k not in {"idempotent", "decision"}},
            idempotent=False,
            trace_id=_trace_id(request),
        )

    @app.post(
        "/api/v1/topology/evidence/anchors",
        response_model=TopologyEvidenceAnchorResponse,
        tags=["topology", "aiops-evidence"],
    )
    async def topology_evidence_anchor(
        payload: TopologyEvidenceAnchorRequest,
        request: Request,
        tenant_id: Annotated[UUID, Header(alias="X-Tenant-Id")],
    ) -> TopologyEvidenceAnchorResponse:
        """M9: append one anchor batch for the tenant evidence chain.

        Fail-closed: the tenant policy must freshly grant
        ``topology.evidence.anchor`` (else 403) with zero writes; the scope
        must be one of the locked M9 enum (else 400).  Anchoring is a pure DB
        projection of the M1-M8 evidence ledgers - no child process is ever
        spawned and an identical ``Idempotency-Key`` replays the recorded
        batch with ``idempotent=True``.
        """
        trace_id = _trace_id(request)
        require_policy(
            "topology.evidence.anchor", tenant_id, trace_id, risk_class="medium",
            side_effects="write_data", arguments={"scope": payload.scope},
            idempotency_key=payload.idempotency_key,
        )
        rules: list[dict[str, Any]] = []
        try:
            with psycopg2.connect(config.database_url) as connection, connection.cursor() as cur:
                cur.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),))
                rules, _ = _load_policy(cur, tenant_id)
        except psycopg2.Error:
            rules = []
        try:
            result = TopologyService(
                config.database_url, policy=PolicyEngine(rules=rules), tenant_slug=tenant_slug(tenant_id)
            ).anchor_evidence(payload.scope, idempotency_key=payload.idempotency_key, trace_id=trace_id)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return TopologyEvidenceAnchorResponse(
            scope=result["scope"],
            idempotent=bool(result["idempotent"]),
            entries=result["entries"],
            trace_id=trace_id,
        )

    @app.get(
        "/api/v1/topology/evidence/verify",
        response_model=TopologyEvidenceProofResponse,
        tags=["topology", "aiops-evidence"],
    )
    async def topology_evidence_verify(
        request: Request,
        tenant_id: Annotated[UUID, Header(alias="X-Tenant-Id")],
        scope: str = "full",
    ) -> TopologyEvidenceProofResponse:
        """M9: recompute row hashes + replay predecessor links (read-only)."""
        trace_id = _trace_id(request)
        require_policy(
            "topology.evidence.verify", tenant_id, trace_id, risk_class="read_only",
            side_effects="read_only", arguments={"scope": scope},
        )
        rules: list[dict[str, Any]] = []
        try:
            with psycopg2.connect(config.database_url) as connection, connection.cursor() as cur:
                cur.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),))
                rules, _ = _load_policy(cur, tenant_id)
        except psycopg2.Error:
            rules = []
        try:
            result = TopologyService(
                config.database_url, policy=PolicyEngine(rules=rules), tenant_slug=tenant_slug(tenant_id)
            ).verify_evidence_chain(scope=scope, trace_id=trace_id)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return TopologyEvidenceProofResponse(
            **{k: v for k, v in result.items() if k != "trace_id"},
            trace_id=trace_id,
        )

    @app.get(
        "/api/v1/topology/evidence/export",
        response_model=TopologyEvidenceExportResponse,
        tags=["topology", "aiops-evidence"],
    )
    async def topology_evidence_export(
        request: Request,
        tenant_id: Annotated[UUID, Header(alias="X-Tenant-Id")],
        scope: str = "full",
    ) -> TopologyEvidenceExportResponse:
        """M9: read-only audit export of anchor metadata + overall sha256.

        The export is an API response body only — no file is ever created on
        disk by M9.
        """
        trace_id = _trace_id(request)
        require_policy(
            "topology.evidence.export", tenant_id, trace_id, risk_class="read_only",
            side_effects="read_only", arguments={"scope": scope},
        )
        rules: list[dict[str, Any]] = []
        try:
            with psycopg2.connect(config.database_url) as connection, connection.cursor() as cur:
                cur.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),))
                rules, _ = _load_policy(cur, tenant_id)
        except psycopg2.Error:
            rules = []
        try:
            result = TopologyService(
                config.database_url, policy=PolicyEngine(rules=rules), tenant_slug=tenant_slug(tenant_id)
            ).export_evidence(scope=scope, trace_id=trace_id)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return TopologyEvidenceExportResponse(
            **{k: v for k, v in result.items() if k != "trace_id"},
            trace_id=trace_id,
        )

    @app.get(
        "/api/v1/topology/evidence/status",
        response_model=TopologyEvidenceStatusResponse,
        tags=["topology", "aiops-evidence"],
    )
    async def topology_evidence_status(
        request: Request,
        tenant_id: Annotated[UUID, Header(alias="X-Tenant-Id")],
    ) -> TopologyEvidenceStatusResponse:
        """M9: lightweight chain status (anchor count, tail hash, last anchor)."""
        trace_id = _trace_id(request)
        require_policy(
            "topology.evidence.export", tenant_id, trace_id, risk_class="read_only",
            side_effects="read_only", arguments={"capability": "status"},
        )
        rules: list[dict[str, Any]] = []
        try:
            with psycopg2.connect(config.database_url) as connection, connection.cursor() as cur:
                cur.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),))
                rules, _ = _load_policy(cur, tenant_id)
        except psycopg2.Error:
            rules = []
        try:
            result = TopologyService(
                config.database_url, policy=PolicyEngine(rules=rules), tenant_slug=tenant_slug(tenant_id)
            ).evidence_chain_status(trace_id=trace_id)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        return TopologyEvidenceStatusResponse(
            **{k: v for k, v in result.items() if k != "trace_id"},
            trace_id=trace_id,
        )

    @app.post(
        "/api/v1/topology/planning/intents",
        response_model=TopologyPlanningIntentResponse,
        tags=["topology"],
    )
    async def topology_planning_intent(
        payload: TopologyPlanningIntentRequest,
        request: Request,
        tenant_id: Annotated[UUID, Header(alias="X-Tenant-Id")],
    ) -> TopologyPlanningIntentResponse:
        """M10 graph-driven planning: business intent -> plan_only plan.

        The intent text is matched deterministically against the L2 capability /
        L3 domain spaces (pg_trgm + bounded one-hop graph traversal, zero LLM /
        network), mapped back to blueprint capability tokens via
        ``topology.blueprint_graph_links``, and planned by the existing
        ``TopologyService.plan()`` (reused when the deterministic ``plan_key``
        already exists).  Every request is recorded as an append-only
        ``topology.planning_intents`` evidence row.  Fail-closed: the tenant
        policy must freshly grant ``topology.intent.plan`` (seeded inactive,
        else 403); an intent with no graph match or no linked blueprint is
        rejected with 400 and nothing is planned.  Re-using the same
        ``Idempotency-Key`` replays the recorded intent row.
        """
        trace_id = payload.trace_id or _trace_id(request)
        require_policy(
            "topology.intent.plan", tenant_id, trace_id, risk_class="low",
            side_effects="write_data", arguments={"intent": payload.intent[:80]},
            idempotency_key=payload.idempotency_key,
        )
        rules: list[dict[str, Any]] = []
        try:
            with psycopg2.connect(config.database_url) as connection, connection.cursor() as cur:
                cur.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),))
                rules, _ = _load_policy(cur, tenant_id)
        except psycopg2.Error:
            rules = []
        try:
            service = TopologyService(
                config.database_url, policy=PolicyEngine(rules=rules), tenant_slug=tenant_slug(tenant_id)
            )
            result = GraphPlanningService(
                config.database_url,
                topology_service=service,
                graph_port=CapabilityGraphAdapter(config.database_url, tenant_slug=tenant_slug(tenant_id)),
                policy=PolicyEngine(rules=rules),
                tenant_slug=tenant_slug(tenant_id),
            ).plan_from_intent(payload.model_dump(), trace_id=trace_id)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return TopologyPlanningIntentResponse(
            **{k: v for k, v in result.items() if k != "trace_id"},
            trace_id=trace_id,
        )

    @app.get(
        "/api/v1/topology/planning/intents",
        response_model=TopologyPlanningIntentsResponse,
        tags=["topology"],
    )
    async def topology_planning_intents(
        request: Request,
        tenant_id: Annotated[UUID, Header(alias="X-Tenant-Id")],
        limit: Annotated[int, Query(ge=1, le=500)] = 50,
    ) -> TopologyPlanningIntentsResponse:
        """M10: list planning-intent evidence rows (newest first, read-only)."""
        require_policy(
            "topology.intent.read", tenant_id, _trace_id(request), risk_class="read_only",
            side_effects="read_only", arguments={"limit": limit},
        )
        try:
            service = TopologyService(config.database_url, tenant_slug=tenant_slug(tenant_id))
            items = GraphPlanningService(
                config.database_url,
                topology_service=service,
                graph_port=CapabilityGraphAdapter(config.database_url, tenant_slug=tenant_slug(tenant_id)),
                tenant_slug=tenant_slug(tenant_id),
            ).list_intents(limit)
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return TopologyPlanningIntentsResponse(items=items, trace_id=_trace_id(request))

    @app.get(
        "/api/v1/topology/planning/intents/{intent_id}",
        response_model=TopologyPlanningIntentResponse,
        tags=["topology"],
    )
    async def topology_planning_intent_detail(
        intent_id: UUID,
        request: Request,
        tenant_id: Annotated[UUID, Header(alias="X-Tenant-Id")],
    ) -> TopologyPlanningIntentResponse:
        """M10: one planning-intent projection with matched-node evidence."""
        require_policy(
            "topology.intent.read", tenant_id, _trace_id(request), risk_class="read_only",
            side_effects="read_only", arguments={"intent_id": str(intent_id)},
        )
        try:
            service = TopologyService(config.database_url, tenant_slug=tenant_slug(tenant_id))
            result = GraphPlanningService(
                config.database_url,
                topology_service=service,
                graph_port=CapabilityGraphAdapter(config.database_url, tenant_slug=tenant_slug(tenant_id)),
                tenant_slug=tenant_slug(tenant_id),
            ).get_intent(intent_id)
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return TopologyPlanningIntentResponse(
            **{k: v for k, v in result.items() if k != "trace_id"},
            trace_id=_trace_id(request),
        )

    # --- Unified AI access (packages/ai) -----------------------------------
    # One configuration surface for every model call in the project: canvas
    # chat, AI planning, knowledge embeddings.  Settings persist to the project
    # ``.env`` (durable, gitignored) and are applied to the running process
    # immediately, so no restart is needed after saving.

    @app.get("/api/v1/ai/settings", response_model=AISettingsResponse, tags=["ai"])
    async def ai_settings(
        request: Request,
        tenant_id: Annotated[UUID, Header(alias="X-Tenant-Id")],
    ) -> AISettingsResponse:
        """Effective AI provider configuration, with the API key masked.

        Safe to surface in the desktop UI and in logs: the key is reported only
        as ``api_key_set`` plus a masked tail, never in clear text.
        """
        require_policy(
            "ai.settings.read", tenant_id, _trace_id(request),
            risk_class="read_only", side_effects="read_only",
        )
        try:
            return _ai_settings_response()
        except AIConfigurationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.put("/api/v1/ai/settings", response_model=AISettingsResponse, tags=["ai"])
    async def ai_settings_update(
        payload: AISettingsUpdateRequest,
        request: Request,
        tenant_id: Annotated[UUID, Header(alias="X-Tenant-Id")],
        idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=255)],
    ) -> AISettingsResponse:
        """Persist AI provider settings, then apply them to this process.

        Only the keys in ``packages.ai.config.MANAGED_ENV`` are writable; every
        other line of ``.env`` (comments, ``DATABASE_URL``, ordering) is
        preserved.  The write is atomic and leaves a ``.env.bak`` snapshot.
        """
        updates = _ai_settings_updates(payload)
        require_policy(
            "ai.settings.write", tenant_id, _trace_id(request),
            risk_class="low", side_effects="write_data",
            # Values are deliberately excluded: an API key must not be written
            # into the policy audit ledger.
            arguments={"keys": sorted(updates)},
            idempotency_key=idempotency_key,
        )
        if not updates:
            raise HTTPException(status_code=422, detail="no settings supplied")
        _reject_unknown_provider(payload.provider, ai_config.SUPPORTED_CHAT_PROVIDERS, "provider")
        _reject_unknown_provider(
            payload.embed_provider, ai_config.SUPPORTED_EMBED_PROVIDERS, "embed_provider"
        )
        try:
            ai_env_store.update_env_file(updates, allowed_keys=ai_config.MANAGED_ENV)
        except (ai_env_store.EnvWriteError, OSError) as exc:
            raise HTTPException(status_code=500, detail=f"failed to persist AI settings: {exc}") from exc
        ai_env_store.apply_to_environ(updates)
        try:
            return _ai_settings_response()
        except AIConfigurationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/v1/ai/test", response_model=AITestResponse, tags=["ai"])
    async def ai_test(
        payload: AITestRequest,
        request: Request,
        tenant_id: Annotated[UUID, Header(alias="X-Tenant-Id")],
    ) -> AITestResponse:
        """Round-trip the configured channel so the operator can see it work.

        User-initiated diagnostic.  Transport failures come back as
        ``ok=false`` with the reason rather than an HTTP error: the UI has to
        render *why* a channel is unusable.
        """
        require_policy(
            "ai.provider.test", tenant_id, _trace_id(request),
            risk_class="low", side_effects="read_only",
            arguments={"include_embedding": payload.include_embedding},
        )
        try:
            result = ai_gateway.probe(include_embedding=payload.include_embedding)
        except AIConfigurationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return AITestResponse(
            ok=result.ok,
            provider=result.provider,
            model=result.model,
            base_url=result.base_url,
            detail=result.detail,
            latency_ms=result.latency_ms,
            embedding_ok=result.embedding_ok,
            embedding_detail=result.embedding_detail,
            embedding_model=result.embedding_model,
        )

    # The control-plane shell is deliberately served by the same origin as the
    # API so it cannot bypass the policy gateway with an alternate base URL.
    web_root = Path(__file__).resolve().parents[2] / "web"

    @app.get("/", include_in_schema=False)
    async def control_plane_shell() -> FileResponse:
        return FileResponse(web_root / "index.html")

    app.mount("/web", StaticFiles(directory=str(web_root)), name="web")

    return app


app = create_app()
