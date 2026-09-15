"""Typed contracts for the experience layer (C0, no DB / no side effects).

The experience layer turns *what actually happened* during real business
runs (``control.node_attempts`` / ``topology.execution_runs``) into durable,
tenant-scoped observations and rebuildable aggregates.  These dataclasses fix
the field-level contract before any persistence or API code is written
(AGENTS.md: contracts and tests first).

Design reference: docs/知识图谱经验积累与动态更新方案-20260911.md
"""

from __future__ import annotations

from dataclasses import dataclass

# Consumer-side terminal outcomes we record for a hand-off / node use.
STATUS_SUCCEEDED = "succeeded"
STATUS_FAILED = "failed"
TERMINAL_STATUSES = frozenset({STATUS_SUCCEEDED, STATUS_FAILED})

# Suggestion lifecycle. Observations are immutable; suggestions only move
# state, they are never hard-deleted (evidence retained).
SUGGESTION_PROPOSED = "proposed"
SUGGESTION_ACCEPTED = "accepted"
SUGGESTION_DISMISSED = "dismissed"
SUGGESTION_STATUSES = frozenset(
    {SUGGESTION_PROPOSED, SUGGESTION_ACCEPTED, SUGGESTION_DISMISSED}
)

# How many evidence run ids we keep inline on a stats row (full evidence stays
# in the append-only observation tables).
EVIDENCE_RUN_LIMIT = 20


@dataclass(frozen=True, slots=True)
class Handover:
    """One real producer→consumer data hand-off observed inside one run.

    Extracted deterministically from the attempt ledger: the consumer's
    ``input_bindings[port].source_instance`` resolves to the producer's
    plugin id.  ``contract_id`` uses the consumer-side input port name so it
    matches the design-time nebula ``dataflow`` edge key; ``source_port`` is
    kept because an adapter may rename the producer-side port.
    """

    source_plugin_id: str
    target_plugin_id: str
    contract_id: str
    source_instance: str
    target_instance: str
    source_attempt_id: str
    target_attempt_id: str
    source_port: str | None
    artifact_sha256: str | None
    status: str
    latency_ms: int | None

    def edge_key(self) -> tuple[str, str, str]:
        return (self.source_plugin_id, self.target_plugin_id, self.contract_id)


@dataclass(frozen=True, slots=True)
class NodeUse:
    """One terminal attempt of one plugin (a single node-use observation)."""

    plugin_id: str
    capability: str
    #: Which plugin version ran. ``""`` when the attempt never reached the
    #: runtime (see ``control.node_attempts``) — never a fabricated value.
    plugin_version: str
    attempt_id: str
    attempt_seq: int
    status: str
    error_kind: str | None
    latency_ms: int | None


@dataclass(frozen=True, slots=True)
class EdgeAggregate:
    """Rebuildable rollup for one (source, target, contract) edge."""

    source_plugin_id: str
    target_plugin_id: str
    contract_id: str
    success_count: int
    fail_count: int
    total_latency_ms: int
    first_used_at: str | None
    last_used_at: str | None
    declared: bool
    confidence: float
    weight: float

    @property
    def total(self) -> int:
        return self.success_count + self.fail_count

    @property
    def success_rate(self) -> float:
        total = self.total
        return self.success_count / total if total else 0.0


@dataclass(frozen=True, slots=True)
class NodeAggregate:
    """Rebuildable rollup for one plugin (optionally per capability)."""

    plugin_id: str
    capability: str | None
    use_count: int
    success_count: int
    fail_count: int
    total_latency_ms: int
    first_used_at: str | None
    last_used_at: str | None
    confidence: float
    weight: float

    @property
    def success_rate(self) -> float:
        return self.success_count / self.use_count if self.use_count else 0.0
