"""Experience layer: accumulate real plugin collaboration into the nebula.

Pure extraction/statistics live here and have no DB dependency; the durable
``ExperienceProjector`` (observations → rebuildable aggregates → relation
suggestions) is added on top with explicit Policy Gateway + idempotency.

Design: docs/知识图谱经验积累与动态更新方案-20260911.md
"""

from __future__ import annotations

from .handoff import extract_handovers, extract_node_uses, latency_ms
from .projector import ExperienceProjector
from .rollup import edge_stat_values, node_stat_values
from .statistics import (
    DEFAULT_HALF_LIFE_DAYS,
    age_days,
    display_weight,
    half_life_decay,
    wilson_lower_bound,
)
from .suggestion import (
    IllegalSuggestionTransition,
    SuggestionNotFound,
    accumulates_evidence,
    transition_status,
)
from .types import (
    STATUS_FAILED,
    STATUS_SUCCEEDED,
    SUGGESTION_ACCEPTED,
    SUGGESTION_DISMISSED,
    SUGGESTION_PROPOSED,
    SUGGESTION_STATUSES,
    TERMINAL_STATUSES,
    EdgeAggregate,
    Handover,
    NodeAggregate,
    NodeUse,
)

__all__ = [
    "DEFAULT_HALF_LIFE_DAYS",
    "EdgeAggregate",
    "ExperienceProjector",
    "Handover",
    "IllegalSuggestionTransition",
    "NodeAggregate",
    "NodeUse",
    "SUGGESTION_ACCEPTED",
    "SUGGESTION_DISMISSED",
    "SUGGESTION_PROPOSED",
    "SUGGESTION_STATUSES",
    "STATUS_FAILED",
    "STATUS_SUCCEEDED",
    "SuggestionNotFound",
    "TERMINAL_STATUSES",
    "accumulates_evidence",
    "age_days",
    "display_weight",
    "edge_stat_values",
    "extract_handovers",
    "extract_node_uses",
    "half_life_decay",
    "latency_ms",
    "node_stat_values",
    "transition_status",
    "wilson_lower_bound",
]
