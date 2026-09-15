"""M4 approval workflow helpers for requires_approval chain intents.

Pure, deterministic functions used by the service layer and unit tests.
The state machine is deliberately tiny: only an intent still in
``materialized`` with ``requires_approval`` may be approved or rejected;
terminal states are frozen and AUTO approval is contractually impossible.
"""

from __future__ import annotations

import hashlib

APPROVAL_DECISIONS = ("approve", "reject")
_AWAITING_POLICY_DECISION = "requires_approval"
_AWAITING_STATUS = "materialized"
_APPROVED_STATUS = "approved_projection"
_DENIED_STATUS = "denied"
_APPROVAL_PREFIX = "apr-"


def transition_status(policy_decision: str, status: str, decision: str) -> str:
    """Return the new intent status for a human approval decision.

    Raises ``ValueError`` when the intent is not awaiting approval or the
    decision is unknown; terminal state changes can never be reverted, so
    approving an already-approved or denied intent fails closed.
    """
    if policy_decision != _AWAITING_POLICY_DECISION or status != _AWAITING_STATUS:
        raise ValueError(
            f"intent is not awaiting approval: decision={policy_decision} status={status}"
        )
    if decision not in APPROVAL_DECISIONS:
        raise ValueError(f"invalid approval decision: {decision}")
    return _APPROVED_STATUS if decision == "approve" else _DENIED_STATUS


def approval_ref_for(*, chain_key: str, slot_key: str, decision: str, approver: str) -> str:
    """Deterministic, traceable approval reference for the ledger."""
    digest = hashlib.sha256("|".join([chain_key, slot_key, decision, approver]).encode("utf-8"))
    return f"{_APPROVAL_PREFIX}{digest.hexdigest()[:16]}"