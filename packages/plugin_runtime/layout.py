"""Verified built-in plugin layout: plugin id -> directory + runtime target.

This module is the single source of truth for *where* a verified built-in
plugin lives.  One plugin is one directory, and everything about it is derived
from the plugin id by a single tested convention::

    plugin id       audit.evidence-lineage
    directory       plugins/builtin/audit_evidence_lineage/   ('.' '-' -> '_')
    descriptors     <directory>/plugin.protocol.json
                    <directory>/plugin.manifest.json
                    <directory>/plugin.runtime-binding.json
                    <directory>/contract/            (optional fixtures)
    module          plugins.builtin.audit_evidence_lineage.runtime
    entrypoint      <module>:handle

The directory name is the module name.  A dot and a hyphen are both legal in a
plugin id but neither is legal in a Python identifier, so both become ``_``.

Deriving this replaces two hand-copied tables: a 123 x 7 path table in
``runner``, and — before that — a layout in which each plugin's descriptors and
its ``runtime.py`` lived in *separate* directories (``audit-x-y/`` vs
``audit_x_y/``) kept in sync by hand.  Adding or moving a plugin needed both
edited; now it needs neither.

What stays explicit is deliberately narrow, and both parts are security-relevant:

* :data:`VERIFIED_PLUGIN_IDS` — the allow list is *code*, not directory
  discovery.  A descriptor dropped on disk does not become executable until its
  id is listed here (``runner``: "never resolve an untrusted entrypoint").
* :data:`_INPUT_SHA256` — the per-plugin payload fingerprint.  Its shape
  genuinely varies (plain artifact refs, nested refs, a computed backtest
  hash), so each function is preserved verbatim rather than guessed at.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Callable

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BUILTIN_ROOT = PROJECT_ROOT / "plugins" / "builtin"

#: A dot and a hyphen are both legal in a plugin id but neither is legal in a
#: Python module name, so both map to ``_`` for the runtime package.
_ID_TO_MODULE_CHARS = str.maketrans({".": "_", "-": "_"})


def module_name(plugin_id: str) -> str:
    """Return the runtime module/package name for *plugin_id*."""
    return plugin_id.translate(_ID_TO_MODULE_CHARS)


def plugin_dir(plugin_id: str) -> Path:
    """The single directory holding one built-in plugin.

    The plugin's descriptors and its ``runtime.py`` live here together; the
    directory name is :func:`module_name` of the id.
    """
    return BUILTIN_ROOT / module_name(plugin_id)


def declaration_dir(plugin_id: str) -> Path:
    """Directory holding the protocol / manifest / runtime-binding descriptors.

    The same directory as :func:`runtime_dir` — a plugin is one directory.  The
    two names are kept because the code that reads descriptors and the code that
    runs the implementation are asking different questions.
    """
    return plugin_dir(plugin_id)


def runtime_dir(plugin_id: str) -> Path:
    """Directory holding the plugin's ``runtime.py`` implementation."""
    return plugin_dir(plugin_id)


def runtime_module(plugin_id: str) -> str:
    """Dotted import path of the plugin's runtime module."""
    return f"plugins.builtin.{module_name(plugin_id)}.runtime"


def entrypoint(plugin_id: str) -> str:
    """``module:handle`` entrypoint the isolated child runs."""
    return f"{runtime_module(plugin_id)}:handle"


def input_fingerprint(plugin_id: str) -> Callable[[dict[str, Any]], str]:
    """Return the payload fingerprint function for *plugin_id*.

    Raises :class:`KeyError` for an id outside the verified allow list; callers
    translate that into their own error type.
    """
    if plugin_id not in _INPUT_SHA256:
        raise KeyError(plugin_id)
    return _INPUT_SHA256[plugin_id]



#: The verified built-in allow list (code, not directory discovery).
VERIFIED_PLUGIN_IDS: tuple[str, ...] = (
    "knowledge.document-ingestion",
    "audit.ledger-quality",
    "audit.journal-anomaly",
    "audit.investigation-plan",
    "knowledge.graph-proposal-builder",
    "audit.evidence-lineage",
    "audit.finding-draft",
    "audit.workpaper-export",
    "audit.report-draft",
    "knowledge.entity-relation-candidate",
    "knowledge.retention-recommendation",
    "quant.snapshot-guard",
    "quant.factor-compute",
    "quant.experiment-evaluator",
    "quant.simulated-backtest",
    "quant.research-note-draft",
    "aiops.playbook-proposer",
    "aiops.recovery-verifier",
    "aiops.alert-triage",
    "aiops.alert-correlation",
    "aiops.ticket-draft",
    "aiops.postmortem-draft",
    "aiops.rca-ranker",
    "audit.foundation.metric-compute",
    "audit.foundation.nlp-process",
    "audit.foundation.ocr-extract",
    "audit.foundation.quality-check",
    "audit.foundation.tag-manage",
    "audit.foundation.rule-engine",
    "audit.evidence.evidence-verify",
    "audit.finding.responsible-party-find",
    "audit.finding.suspicion-merge",
    "audit.remedy.remedy-progress-track",
    "audit.finding.violation-clause-match",
    "audit.field.suspicion-flag",
    "audit.field.asset-check",
    "audit.field.audit-log",
    "audit.field.confirm-letter",
    "audit.field.cross-dept-inquiry",
    "audit.field.evidence-photo",
    "audit.field.extension-approve",
    "audit.field.interview-record",
    "audit.field.inventory-count",
    "audit.field.meeting-minutes",
    "audit.field.progress-report",
    "audit.field.site-checkin-track",
    "audit.field.voucher-drilldown",
    "audit.field.workpaper-build",
    "audit.mandate.demand-collect",
    "audit.mandate.strategy-align",
    "audit.mandate.annual-propose",
    "audit.mandate.proposal-score",
    "audit.mandate.project-library",
    "audit.mandate.priority-rank",
    "audit.mandate.notice-generate",
    "audit.mandate.material-submit",
    "audit.mandate.material-precheck",
    "audit.mandate.team-forming",
    "audit.plan.annual-plan-build",
    "audit.plan.project-scheme-build",
    "audit.plan.program-template-match",
    "audit.plan.sampling-select",
    "audit.plan.sample-size-compute",
    "audit.plan.staff-schedule",
    "audit.plan.effort-budget",
    "audit.plan.resource-conflict-detect",
    "audit.plan.plan-version-control",
    "audit.risk.macro-policy-risk-scan",
    "audit.risk.industry-risk-benchmark",
    "audit.risk.internal-control-risk-map",
    "audit.risk.process-gap-detect",
    "audit.risk.fraud-risk-match",
    "audit.risk.risk-level-assign",
    "audit.risk.high-risk-area-locate",
    "audit.risk.risk-heatmap-draw",
    "audit.risk.risk-advice-generate",
    "audit.evidence.evidence-archive",
    "audit.evidence.evidence-index-link",
    "audit.evidence.workpaper-reconcile",
    "audit.evidence.workpaper-review3",
    "audit.evidence.e-signature",
    "audit.evidence.workpaper-encrypt-store",
    "audit.evidence.workpaper-version-diff",
    "audit.evidence.workpaper-template-update",
    "audit.evidence.workpaper-borrow-approve",
    "audit.report.report-frame-build",
    "audit.report.advice-match",
    "audit.report.report-data-check",
    "audit.report.report-multi-review",
    "audit.report.result-distill",
    "audit.report.notice-mask-publish",
    "audit.remedy.remedy-dispatch",
    "audit.remedy.remedy-plan-review",
    "audit.remedy.remedy-overdue-alert",
    "audit.remedy.remedy-effect-verify",
    "audit.remedy.remedy-close",
    "audit.remedy.remedy-publish",
    "audit.govern.project-quality-score",
    "audit.govern.effect-evaluate",
    "audit.govern.case-library-update",
    "audit.govern.issue-trend-analysis",
    "audit.govern.rule-iteration",
    "audit.govern.method-distill",
    "audit.foundation.biz-standardize",
    "audit.foundation.data-encrypt",
    "audit.foundation.data-mask",
    "audit.foundation.fulltext-search",
    "audit.foundation.lineage-track",
    "audit.foundation.master-mapping",
    "audit.foundation.metadata-manage",
    "audit.foundation.multi-source-collect",
    "audit.foundation.permission-control",
    "audit.foundation.viz-analysis",
    "audit.foundation.workflow-engine",
    "audit.foundation.finance-clean",
    "audit.risk.finance-anomaly-alert",
    "audit.risk.risk-matrix-build",
    "audit.finding.issue-final-review",
    "audit.finding.issue-grade",
    "audit.finding.issue-type-judge",
    "audit.finding.auditee-feedback",
    "audit.finding.issue-amount-compute",
    "audit.report.issue-desc-write",
)

#: Payload fingerprint: which part of the invocation payload carries the
#: input artifact's sha256.  Shapes vary by contract, so preserved verbatim.
_INPUT_SHA256: dict[str, Callable[[dict[str, Any]], str]] = {
    "knowledge.document-ingestion": lambda payload: str(payload.get("artifact", {}).get("sha256", "")).lower(),
    "audit.ledger-quality": lambda payload: str(payload.get("ledger", {}).get("artifact", {}).get("sha256", "")).lower(),
    "audit.journal-anomaly": lambda payload: str(payload.get("journal", {}).get("artifact", {}).get("sha256", "")).lower(),
    "audit.investigation-plan": lambda payload: str(payload.get("investigation", {}).get("anomaly_candidates", {}).get("sha256", "")).lower(),
    "knowledge.graph-proposal-builder": lambda payload: str(payload.get("proposal", {}).get("candidate_set", {}).get("sha256", "")).lower(),
    "audit.evidence-lineage": lambda payload: str(payload.get("lineage", {}).get("graph_ref", {}).get("artifact", {}).get("sha256", "")).lower(),
    "audit.finding-draft": lambda payload: str(payload.get("finding", {}).get("anomaly_candidates", {}).get("sha256", "")).lower(),
    "audit.workpaper-export": lambda payload: str(payload.get("workpaper", {}).get("finding_set", {}).get("sha256", "")).lower(),
    "audit.report-draft": lambda payload: str(payload.get("report", {}).get("finding_set", {}).get("sha256", "")).lower(),
    "knowledge.entity-relation-candidate": lambda payload: str(payload.get("document", {}).get("artifact", {}).get("sha256", "")).lower(),
    "knowledge.retention-recommendation": lambda payload: str(payload.get("retention", {}).get("document", {}).get("sha256", "")).lower(),
    "quant.snapshot-guard": lambda payload: str(payload.get("snapshot", {}).get("artifact", {}).get("sha256", "")).lower(),
    "quant.factor-compute": lambda payload: str(payload.get("dataset", {}).get("artifact", {}).get("sha256", "")).lower(),
    "quant.experiment-evaluator": lambda payload: str(payload.get("experiment", {}).get("report", {}).get("sha256", "")).lower(),
    "quant.simulated-backtest": lambda payload: hashlib.sha256(
                    (
                        str(payload.get("backtest", {}).get("strategy", {}).get("code_sha256", ""))
                        + "|"
                        + str(payload.get("backtest", {}).get("snapshot_ref", {}).get("artifact", {}).get("sha256", ""))
                    ).lower().encode("utf-8")
                ).hexdigest(),
    "quant.research-note-draft": lambda payload: str(payload.get("research_note", {}).get("evaluation", {}).get("sha256", "")).lower(),
    "aiops.playbook-proposer": lambda payload: str(payload.get("remediation", {}).get("rca_candidates", {}).get("sha256", "")).lower(),
    "aiops.recovery-verifier": lambda payload: str(payload.get("recovery", {}).get("observed", {}).get("sha256", "")).lower(),
    "aiops.alert-triage": lambda payload: str(payload.get("triage", {}).get("alert_event", {}).get("sha256", "")).lower(),
    "aiops.alert-correlation": lambda payload: str(payload.get("alerts", {}).get("artifact", {}).get("sha256", "")).lower(),
    "aiops.ticket-draft": lambda payload: str(payload.get("ticket", {}).get("incident_proposal", {}).get("sha256", "")).lower(),
    "aiops.postmortem-draft": lambda payload: str(payload.get("postmortem", {}).get("incident_proposal", {}).get("sha256", "")).lower(),
    "aiops.rca-ranker": lambda payload: str(payload.get("rca", {}).get("incident_set", {}).get("sha256", "")).lower(),
    "audit.foundation.metric-compute": lambda payload: str(payload.get("metric-input", {}).get("artifact", {}).get("sha256", "")).lower(),
    "audit.foundation.nlp-process": lambda payload: str(payload.get("nlp-input", {}).get("artifact", {}).get("sha256", "")).lower(),
    "audit.foundation.ocr-extract": lambda payload: str(payload.get("ocr-image", {}).get("artifact", {}).get("sha256", "")).lower(),
    "audit.foundation.quality-check": lambda payload: str(payload.get("quality-input", {}).get("artifact", {}).get("sha256", "")).lower(),
    "audit.foundation.tag-manage": lambda payload: str(payload.get("tag-query", {}).get("artifact", {}).get("sha256", "")).lower(),
    "audit.foundation.rule-engine": lambda payload: str(payload.get("rule-input", {}).get("artifact", {}).get("sha256", "")).lower(),
    "audit.evidence.evidence-verify": lambda payload: str(payload.get("evidence-index", {}).get("artifact", {}).get("sha256", "")).lower(),
    "audit.finding.responsible-party-find": lambda payload: str(payload.get("issue-trace-input", {}).get("artifact", {}).get("sha256", "")).lower(),
    "audit.finding.suspicion-merge": lambda payload: str(payload.get("suspicion-set", {}).get("artifact", {}).get("sha256", "")).lower(),
    "audit.remedy.remedy-progress-track": lambda payload: str(payload.get("remedy-plan-approved", {}).get("artifact", {}).get("sha256", "")).lower(),
    "audit.finding.violation-clause-match": lambda payload: str(payload.get("issue-type-set", {}).get("artifact", {}).get("sha256", "")).lower(),
    "audit.field.suspicion-flag": lambda payload: str(payload.get("field-finding-input", {}).get("artifact", {}).get("sha256", "")).lower(),
    "audit.field.asset-check": lambda payload: str(payload.get("asset-input", {}).get("artifact", {}).get("sha256", "")).lower(),
    "audit.field.audit-log": lambda payload: str(payload.get("log-event", {}).get("artifact", {}).get("sha256", "")).lower(),
    "audit.field.confirm-letter": lambda payload: str(payload.get("confirm-input", {}).get("artifact", {}).get("sha256", "")).lower(),
    "audit.field.cross-dept-inquiry": lambda payload: str(payload.get("inquiry-request", {}).get("artifact", {}).get("sha256", "")).lower(),
    "audit.field.evidence-photo": lambda payload: str(payload.get("photo-input", {}).get("artifact", {}).get("sha256", "")).lower(),
    "audit.field.extension-approve": lambda payload: str(payload.get("extension-request", {}).get("artifact", {}).get("sha256", "")).lower(),
    "audit.field.interview-record": lambda payload: str(payload.get("interview-input", {}).get("artifact", {}).get("sha256", "")).lower(),
    "audit.field.inventory-count": lambda payload: str(payload.get("inventory-input", {}).get("artifact", {}).get("sha256", "")).lower(),
    "audit.field.meeting-minutes": lambda payload: str(payload.get("meeting-audio", {}).get("artifact", {}).get("sha256", "")).lower(),
    "audit.field.progress-report": lambda payload: str(payload.get("daily-progress", {}).get("artifact", {}).get("sha256", "")).lower(),
    "audit.field.site-checkin-track": lambda payload: str(payload.get("checkin-input", {}).get("artifact", {}).get("sha256", "")).lower(),
    "audit.field.voucher-drilldown": lambda payload: str(payload.get("clean-finance-set", {}).get("artifact", {}).get("sha256", "")).lower(),
    "audit.field.workpaper-build": lambda payload: str(payload.get("program-template", {}).get("artifact", {}).get("sha256", "")).lower(),
    "audit.mandate.demand-collect": lambda payload: str(payload.get("demand-input", {}).get("artifact", {}).get("sha256", "")).lower(),
    "audit.mandate.strategy-align": lambda payload: str(payload.get("demand-set", {}).get("artifact", {}).get("sha256", "")).lower(),
    "audit.mandate.annual-propose": lambda payload: str(payload.get("aligned-demand", {}).get("artifact", {}).get("sha256", "")).lower(),
    "audit.mandate.proposal-score": lambda payload: str(payload.get("proposal-set", {}).get("artifact", {}).get("sha256", "")).lower(),
    "audit.mandate.project-library": lambda payload: str(payload.get("scored-proposal", {}).get("artifact", {}).get("sha256", "")).lower(),
    "audit.mandate.priority-rank": lambda payload: str(payload.get("project-snapshot", {}).get("artifact", {}).get("sha256", "")).lower(),
    "audit.mandate.notice-generate": lambda payload: str(payload.get("project-snapshot", {}).get("artifact", {}).get("sha256", "")).lower(),
    "audit.mandate.material-submit": lambda payload: str(payload.get("notice-accept", {}).get("artifact", {}).get("sha256", "")).lower(),
    "audit.mandate.material-precheck": lambda payload: str(payload.get("submitted-material", {}).get("artifact", {}).get("sha256", "")).lower(),
    "audit.mandate.team-forming": lambda payload: str(payload.get("project-snapshot", {}).get("artifact", {}).get("sha256", "")).lower(),
    "audit.plan.annual-plan-build": lambda payload: str(payload.get("priority-order", {}).get("artifact", {}).get("sha256", "")).lower(),
    "audit.plan.project-scheme-build": lambda payload: str(payload.get("high-risk-area", {}).get("artifact", {}).get("sha256", "")).lower(),
    "audit.plan.program-template-match": lambda payload: str(payload.get("scheme-request", {}).get("artifact", {}).get("sha256", "")).lower(),
    "audit.plan.sampling-select": lambda payload: str(payload.get("sampling-input", {}).get("artifact", {}).get("sha256", "")).lower(),
    "audit.plan.sample-size-compute": lambda payload: str(payload.get("sampling-plan", {}).get("artifact", {}).get("sha256", "")).lower(),
    "audit.plan.staff-schedule": lambda payload: str(payload.get("team-scheme", {}).get("artifact", {}).get("sha256", "")).lower(),
    "audit.plan.effort-budget": lambda payload: str(payload.get("schedule-plan", {}).get("artifact", {}).get("sha256", "")).lower(),
    "audit.plan.resource-conflict-detect": lambda payload: str(payload.get("schedule-plan", {}).get("artifact", {}).get("sha256", "")).lower(),
    "audit.plan.plan-version-control": lambda payload: str(payload.get("plan-change", {}).get("artifact", {}).get("sha256", "")).lower(),
    "audit.risk.macro-policy-risk-scan": lambda payload: str(payload.get("policy-input", {}).get("artifact", {}).get("sha256", "")).lower(),
    "audit.risk.industry-risk-benchmark": lambda payload: str(payload.get("industry-benchmark", {}).get("artifact", {}).get("sha256", "")).lower(),
    "audit.risk.internal-control-risk-map": lambda payload: str(payload.get("ic-flow", {}).get("artifact", {}).get("sha256", "")).lower(),
    "audit.risk.process-gap-detect": lambda payload: str(payload.get("process-input", {}).get("artifact", {}).get("sha256", "")).lower(),
    "audit.risk.fraud-risk-match": lambda payload: str(payload.get("fraud-input", {}).get("artifact", {}).get("sha256", "")).lower(),
    "audit.risk.risk-level-assign": lambda payload: str(payload.get("risk-matrix", {}).get("artifact", {}).get("sha256", "")).lower(),
    "audit.risk.high-risk-area-locate": lambda payload: str(payload.get("risk-level", {}).get("artifact", {}).get("sha256", "")).lower(),
    "audit.risk.risk-heatmap-draw": lambda payload: str(payload.get("risk-level", {}).get("artifact", {}).get("sha256", "")).lower(),
    "audit.risk.risk-advice-generate": lambda payload: str(payload.get("high-risk-area", {}).get("artifact", {}).get("sha256", "")).lower(),
    "audit.evidence.evidence-archive": lambda payload: str(payload.get("evidence-input", {}).get("artifact", {}).get("sha256", "")).lower(),
    "audit.evidence.evidence-index-link": lambda payload: str(payload.get("workpaper-draft", {}).get("artifact", {}).get("sha256", "")).lower(),
    "audit.evidence.workpaper-reconcile": lambda payload: str(payload.get("workpaper-draft", {}).get("artifact", {}).get("sha256", "")).lower(),
    "audit.evidence.workpaper-review3": lambda payload: str(payload.get("workpaper-draft", {}).get("artifact", {}).get("sha256", "")).lower(),
    "audit.evidence.e-signature": lambda payload: str(payload.get("review-status", {}).get("artifact", {}).get("sha256", "")).lower(),
    "audit.evidence.workpaper-encrypt-store": lambda payload: str(payload.get("signed-workpaper", {}).get("artifact", {}).get("sha256", "")).lower(),
    "audit.evidence.workpaper-version-diff": lambda payload: str(payload.get("workpaper-draft", {}).get("artifact", {}).get("sha256", "")).lower(),
    "audit.evidence.workpaper-template-update": lambda payload: str(payload.get("workpaper-draft", {}).get("artifact", {}).get("sha256", "")).lower(),
    "audit.evidence.workpaper-borrow-approve": lambda payload: str(payload.get("borrow-request", {}).get("artifact", {}).get("sha256", "")).lower(),
    "audit.report.report-frame-build": lambda payload: str(payload.get("project-snapshot", {}).get("artifact", {}).get("sha256", "")).lower(),
    "audit.report.advice-match": lambda payload: str(payload.get("final-issue-set", {}).get("artifact", {}).get("sha256", "")).lower(),
    "audit.report.report-data-check": lambda payload: str(payload.get("report-draft-input", {}).get("artifact", {}).get("sha256", "")).lower(),
    "audit.report.report-multi-review": lambda payload: str(payload.get("report-check-report", {}).get("artifact", {}).get("sha256", "")).lower(),
    "audit.report.result-distill": lambda payload: str(payload.get("report-approved", {}).get("artifact", {}).get("sha256", "")).lower(),
    "audit.report.notice-mask-publish": lambda payload: str(payload.get("report-approved", {}).get("artifact", {}).get("sha256", "")).lower(),
    "audit.remedy.remedy-dispatch": lambda payload: str(payload.get("final-issue-set", {}).get("artifact", {}).get("sha256", "")).lower(),
    "audit.remedy.remedy-plan-review": lambda payload: str(payload.get("remedy-task", {}).get("artifact", {}).get("sha256", "")).lower(),
    "audit.remedy.remedy-overdue-alert": lambda payload: str(payload.get("remedy-progress", {}).get("artifact", {}).get("sha256", "")).lower(),
    "audit.remedy.remedy-effect-verify": lambda payload: str(payload.get("remedy-evidence", {}).get("artifact", {}).get("sha256", "")).lower(),
    "audit.remedy.remedy-close": lambda payload: str(payload.get("remedy-verdict", {}).get("artifact", {}).get("sha256", "")).lower(),
    "audit.remedy.remedy-publish": lambda payload: str(payload.get("remedy-ledger", {}).get("artifact", {}).get("sha256", "")).lower(),
    "audit.govern.project-quality-score": lambda payload: str(payload.get("project-flow-input", {}).get("artifact", {}).get("sha256", "")).lower(),
    "audit.govern.effect-evaluate": lambda payload: str(payload.get("remedy-ledger", {}).get("artifact", {}).get("sha256", "")).lower(),
    "audit.govern.case-library-update": lambda payload: str(payload.get("distilled-result", {}).get("artifact", {}).get("sha256", "")).lower(),
    "audit.govern.issue-trend-analysis": lambda payload: str(payload.get("history-issue-set", {}).get("artifact", {}).get("sha256", "")).lower(),
    "audit.govern.rule-iteration": lambda payload: str(payload.get("trend-report", {}).get("artifact", {}).get("sha256", "")).lower(),
    "audit.govern.method-distill": lambda payload: str(payload.get("project-review-input", {}).get("artifact", {}).get("sha256", "")).lower(),
    "audit.foundation.biz-standardize": lambda payload: str(payload.get("raw-biz-set", {}).get("artifact", {}).get("sha256", "")).lower(),
    "audit.foundation.data-encrypt": lambda payload: str(payload.get("encrypt-request", {}).get("artifact", {}).get("sha256", "")).lower(),
    "audit.foundation.data-mask": lambda payload: str(payload.get("mask-request", {}).get("artifact", {}).get("sha256", "")).lower(),
    "audit.foundation.fulltext-search": lambda payload: str(payload.get("search-query", {}).get("artifact", {}).get("sha256", "")).lower(),
    "audit.foundation.lineage-track": lambda payload: str(payload.get("lineage-query", {}).get("artifact", {}).get("sha256", "")).lower(),
    "audit.foundation.master-mapping": lambda payload: str(payload.get("master-source", {}).get("artifact", {}).get("sha256", "")).lower(),
    "audit.foundation.metadata-manage": lambda payload: str(payload.get("metadata-query", {}).get("artifact", {}).get("sha256", "")).lower(),
    "audit.foundation.multi-source-collect": lambda payload: str(payload.get("audit-source-request", {}).get("artifact", {}).get("sha256", "")).lower(),
    "audit.foundation.permission-control": lambda payload: str(payload.get("access-request", {}).get("artifact", {}).get("sha256", "")).lower(),
    "audit.foundation.viz-analysis": lambda payload: str(payload.get("viz-input", {}).get("artifact", {}).get("sha256", "")).lower(),
    "audit.foundation.workflow-engine": lambda payload: str(payload.get("workflow-request", {}).get("artifact", {}).get("sha256", "")).lower(),
    "audit.foundation.finance-clean": lambda payload: str(payload.get("raw-finance-set", {}).get("artifact", {}).get("sha256", "")).lower(),
    "audit.risk.finance-anomaly-alert": lambda payload: str(payload.get("clean-finance-set", {}).get("artifact", {}).get("sha256", "")).lower(),
    "audit.risk.risk-matrix-build": lambda payload: str(payload.get("finance-anomaly-set", {}).get("artifact", {}).get("sha256", "")).lower(),
    "audit.finding.issue-final-review": lambda payload: str(payload.get("feedback-set", {}).get("artifact", {}).get("sha256", "")).lower(),
    "audit.finding.issue-grade": lambda payload: str(payload.get("issue-amount", {}).get("artifact", {}).get("sha256", "")).lower(),
    "audit.finding.issue-type-judge": lambda payload: str(payload.get("merged-suspicion", {}).get("artifact", {}).get("sha256", "")).lower(),
    "audit.finding.auditee-feedback": lambda payload: str(payload.get("graded-issue", {}).get("artifact", {}).get("sha256", "")).lower(),
    "audit.finding.issue-amount-compute": lambda payload: str(payload.get("issue-verify-input", {}).get("artifact", {}).get("sha256", "")).lower(),
    "audit.report.issue-desc-write": lambda payload: str(payload.get("final-issue-set", {}).get("artifact", {}).get("sha256", "")).lower(),
}
