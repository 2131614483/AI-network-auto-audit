"""Pure state machine for design-external relation suggestions (L1→L2).

Flow (design §5.5 "light consolidation first"):

    proposed ──accept──▶ accepted   (terminal; graph-internal consolidation)
       │
       └──dismiss───▶ dismissed   (terminal; never auto-revives)

Only ``proposed`` rows accumulate fresh evidence.  A terminal row is frozen:
later runs never flip it back to ``proposed`` and never overwrite the decision
audit columns.  Re-issuing the same decision is an idempotent no-op.
"""

from __future__ import annotations

from packages.experience.types import (
    SUGGESTION_ACCEPTED,
    SUGGESTION_DISMISSED,
    SUGGESTION_PROPOSED,
)


class IllegalSuggestionTransition(ValueError):
    """Raised when a suggestion is moved from a terminal status to another."""

    def __init__(self, current: str, decision: str) -> None:
        self.current = current
        self.decision = decision
        super().__init__(f"illegal suggestion transition: {current} -> {decision}")


class SuggestionNotFound(LookupError):
    """Raised when a suggestion id does not exist in the tenant scope."""

    def __init__(self, suggestion_id: str) -> None:
        self.suggestion_id = suggestion_id
        super().__init__(f"relation suggestion not found: {suggestion_id}")


def transition_status(current: str, decision: str) -> str:
    """Return the resulting status, or raise on an illegal transition.

    * ``proposed`` may move to ``accepted`` or ``dismissed``;
    * re-deciding a terminal row with the same decision is idempotent;
    * flipping a terminal row to the other decision (or back to proposed) raises.
    """
    if decision not in (SUGGESTION_ACCEPTED, SUGGESTION_DISMISSED):
        raise IllegalSuggestionTransition(current, decision)
    if current == SUGGESTION_PROPOSED:
        return decision
    if current == decision:
        return current  # idempotent replay
    raise IllegalSuggestionTransition(current, decision)


def accumulates_evidence(status: str) -> bool:
    """Whether fresh runs may keep updating a suggestion's evidence counters."""
    return status == SUGGESTION_PROPOSED


__all__ = [
    "IllegalSuggestionTransition",
    "SuggestionNotFound",
    "accumulates_evidence",
    "transition_status",
]
