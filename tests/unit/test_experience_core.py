"""Unit tests for the experience layer pure core (statistics + extraction).

Contract locked here (design §5.2/§5.3, decision 2026-09-11):
* Wilson lower bound is conservative for small samples (1/1 is not 100%).
* 90-day half-life decay: 0d→1, 90d→0.5, 180d→0.25.
* Real hand-offs are reconstructed from the attempt ledger deterministically:
  seed inputs skipped, self-loops skipped, retries collapse to final attempt.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import pytest

from packages.experience import (
    SUGGESTION_ACCEPTED,
    SUGGESTION_DISMISSED,
    SUGGESTION_PROPOSED,
    Handover,
    IllegalSuggestionTransition,
    NodeUse,
    accumulates_evidence,
    display_weight,
    edge_stat_values,
    extract_handovers,
    extract_node_uses,
    half_life_decay,
    latency_ms,
    node_stat_values,
    transition_status,
    wilson_lower_bound,
)

# --- statistics -------------------------------------------------------------

def test_wilson_empty_sample_is_zero() -> None:
    assert wilson_lower_bound(0, 0) == 0.0


def test_wilson_single_success_is_conservative_not_one() -> None:
    # A lone success must not be presented as a 100%-reliable edge.
    assert 0.0 < wilson_lower_bound(1, 1) < 1.0


def test_wilson_large_sample_approaches_rate() -> None:
    lo = wilson_lower_bound(900, 1000)
    assert 0.87 < lo < 0.90


def test_wilson_rejects_impossible_counts() -> None:
    with pytest.raises(ValueError):
        wilson_lower_bound(3, 2)


@pytest.mark.parametrize(
    "age,expected",
    [(0, 1.0), (90, 0.5), (180, 0.25)],
)
def test_half_life_decay(age: float, expected: float) -> None:
    assert math.isclose(half_life_decay(age), expected, rel_tol=1e-6)


def test_decay_monotonic_and_bounded() -> None:
    d0, d45, d90, d360 = (half_life_decay(a) for a in (0, 45, 90, 360))
    assert d0 > d45 > d90 > d360 > 0.0


def test_display_weight_zero_without_sample() -> None:
    assert display_weight(0, 1.0, 1.0) == 0.0


def test_display_weight_grows_with_usage_and_respects_decay() -> None:
    fresh = display_weight(50, 0.9, half_life_decay(0))
    stale = display_weight(50, 0.9, half_life_decay(360))
    assert fresh > stale > 0.0


def test_latency_ms() -> None:
    assert latency_ms("2026-09-11T00:00:00+00:00", "2026-09-11T00:00:01+00:00") == 1000
    assert latency_ms(None, "2026-09-11T00:00:01+00:00") is None


# --- ledger extraction ------------------------------------------------------

def _attempt(instance, plugin, seq, status, *, bindings=None, created="2026-09-11T00:00:00+00:00",
             finished="2026-09-11T00:00:02+00:00", capability="cap.x", error_kind=None):
    return {
        "attempt_id": f"att-{instance}-{seq}",
        "run_id": "run-1",
        "node_instance_id": instance,
        "capability": capability,
        "plugin_id": plugin,
        "attempt_seq": seq,
        "status": status,
        "input_bindings": bindings or {},
        "output_refs": {},
        "error_kind": error_kind,
        "created_at": created,
        "finished_at": finished,
    }


def test_extracts_one_real_handover() -> None:
    attempts = [
        _attempt("n-a", "audit.a.producer", 1, "succeeded"),
        _attempt(
            "n-b", "audit.b.consumer", 1, "succeeded",
            bindings={"doc": {"source_instance": "n-a", "source_port": "doc", "sha256": "abc"}},
        ),
    ]
    handovers = extract_handovers(attempts)
    assert len(handovers) == 1
    h = handovers[0]
    assert isinstance(h, Handover)
    assert h.edge_key() == ("audit.a.producer", "audit.b.consumer", "doc")
    assert h.source_attempt_id == "att-n-a-1"
    assert h.target_attempt_id == "att-n-b-1"
    assert h.artifact_sha256 == "abc"
    assert h.status == "succeeded"
    assert h.latency_ms == 2000


def test_seed_input_without_source_is_skipped() -> None:
    attempts = [
        _attempt("n-b", "audit.b.consumer", 1, "succeeded",
                 bindings={"raw": {"sha256": "ext"}}),  # no source_instance
    ]
    assert extract_handovers(attempts) == []


def test_self_loop_is_skipped() -> None:
    attempts = [
        _attempt("n-a", "audit.a.same", 1, "succeeded"),
        _attempt("n-b", "audit.a.same", 1, "succeeded",
                 bindings={"x": {"source_instance": "n-a"}}),
    ]
    assert extract_handovers(attempts) == []


def test_retry_collapses_to_final_attempt() -> None:
    attempts = [
        _attempt("n-a", "audit.a.producer", 1, "succeeded"),
        _attempt("n-b", "audit.b.consumer", 1, "failed", error_kind="transient",
                 bindings={"doc": {"source_instance": "n-a"}}),
        _attempt("n-b", "audit.b.consumer", 2, "succeeded",
                 bindings={"doc": {"source_instance": "n-a"}}),
    ]
    handovers = extract_handovers(attempts)
    assert len(handovers) == 1
    assert handovers[0].target_attempt_id == "att-n-b-2"
    assert handovers[0].status == "succeeded"
    uses = extract_node_uses(attempts)
    assert len(uses) == 2  # one per instance, final attempt only
    b_use = next(u for u in uses if u.plugin_id == "audit.b.consumer")
    assert isinstance(b_use, NodeUse)
    assert b_use.attempt_seq == 2
    assert b_use.status == "succeeded"


def test_non_terminal_attempts_ignored() -> None:
    attempts = [
        _attempt("n-a", "audit.a.producer", 1, "running"),
    ]
    assert extract_node_uses(attempts) == []
    assert extract_handovers(attempts) == []


def test_handover_order_is_deterministic() -> None:
    attempts = [
        _attempt("n-a", "audit.a.producer", 1, "succeeded"),
        _attempt("n-z", "audit.z.second", 1, "succeeded",
                 bindings={"m": {"source_instance": "n-a"}}),
        _attempt("n-b", "audit.b.first", 1, "succeeded",
                 bindings={"m": {"source_instance": "n-a"}}),
    ]
    first = extract_handovers(attempts)
    second = extract_handovers(list(reversed(attempts)))
    assert [h.target_plugin_id for h in first] == ["audit.b.first", "audit.z.second"]
    assert [h.target_plugin_id for h in second] == ["audit.b.first", "audit.z.second"]


# --- roll-up (time-free stored aggregates) ---------------------------------

_BASE = datetime(2026, 9, 1, tzinfo=timezone.utc)


def _edge_row(status: str, run: str, *, declared: bool, ago_days: int, latency: int = 100) -> dict:
    return {
        "status": status,
        "latency_ms": latency,
        "observed_at": _BASE - timedelta(days=ago_days),
        "run_id": run,
        "declared": declared,
    }


def test_edge_rollup_counts_and_declared_or() -> None:
    rows = [
        _edge_row("succeeded", "r1", declared=True, ago_days=2),
        _edge_row("succeeded", "r2", declared=False, ago_days=1),
        _edge_row("failed", "r3", declared=False, ago_days=0),
    ]
    values = edge_stat_values(rows)
    assert values["success_count"] == 2
    assert values["fail_count"] == 1
    assert values["total_latency_ms"] == 300
    assert values["declared"] is True  # OR of per-observation snapshots
    assert values["first_used_at"] == _BASE - timedelta(days=2)
    assert values["last_used_at"] == _BASE
    # most recent first
    assert values["evidence_run_ids"] == ["r3", "r2", "r1"]


def test_edge_rollup_is_time_free_and_repeatable() -> None:
    rows = [_edge_row("succeeded", "r1", declared=True, ago_days=5)]
    first = edge_stat_values(rows)
    second = edge_stat_values(list(rows))
    assert first == second  # no clock dependency → incremental == rebuild


def test_edge_rollup_caps_evidence_runs() -> None:
    rows = [_edge_row("succeeded", f"r{i:02d}", declared=True, ago_days=i) for i in range(25)]
    values = edge_stat_values(rows)
    assert len(values["evidence_run_ids"]) == 20
    assert values["evidence_run_ids"][0] == "r00"  # newest (ago_days=0) first


def test_node_rollup_error_kind_histogram() -> None:
    rows = [
        {"status": "succeeded", "latency_ms": 50, "observed_at": _BASE, "error_kind": None},
        {"status": "failed", "latency_ms": 70, "observed_at": _BASE, "error_kind": "timeout"},
        {"status": "failed", "latency_ms": 30, "observed_at": _BASE, "error_kind": "timeout"},
        {"status": "failed", "latency_ms": 20, "observed_at": _BASE, "error_kind": "schema"},
    ]
    values = node_stat_values(rows)
    assert values["use_count"] == 4
    assert values["success_count"] == 1
    assert values["fail_count"] == 3
    assert values["total_latency_ms"] == 170
    assert values["error_kind_counts"] == {"schema": 1, "timeout": 2}


# --- suggestion state machine ----------------------------------------------

def test_suggestion_proposed_can_accept_or_dismiss() -> None:
    assert transition_status(SUGGESTION_PROPOSED, SUGGESTION_ACCEPTED) == SUGGESTION_ACCEPTED
    assert transition_status(SUGGESTION_PROPOSED, SUGGESTION_DISMISSED) == SUGGESTION_DISMISSED


def test_suggestion_terminal_is_frozen() -> None:
    # accepted cannot be flipped to dismissed and vice versa
    with pytest.raises(IllegalSuggestionTransition):
        transition_status(SUGGESTION_ACCEPTED, SUGGESTION_DISMISSED)
    with pytest.raises(IllegalSuggestionTransition):
        transition_status(SUGGESTION_DISMISSED, SUGGESTION_ACCEPTED)
    # cannot return to proposed
    with pytest.raises(IllegalSuggestionTransition):
        transition_status(SUGGESTION_ACCEPTED, SUGGESTION_PROPOSED)


def test_suggestion_same_decision_is_idempotent() -> None:
    assert transition_status(SUGGESTION_ACCEPTED, SUGGESTION_ACCEPTED) == SUGGESTION_ACCEPTED
    assert transition_status(SUGGESTION_DISMISSED, SUGGESTION_DISMISSED) == SUGGESTION_DISMISSED


def test_suggestion_only_proposed_accumulates_evidence() -> None:
    assert accumulates_evidence(SUGGESTION_PROPOSED) is True
    assert accumulates_evidence(SUGGESTION_ACCEPTED) is False
    assert accumulates_evidence(SUGGESTION_DISMISSED) is False
