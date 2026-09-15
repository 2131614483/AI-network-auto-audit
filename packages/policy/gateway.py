"""Durable policy-gateway persistence shared by background executors.

The API keeps its inline gateway for request paths; this module exists so that
the 24x7 task executor can record tool calls + decisions through the exact same
policy tables without duplicating SQL in three places.  A recorded decision is
immutable evidence: no update or delete path is exposed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

from psycopg2.extras import Json

from packages.policy.engine import PolicyResult

DECISION_NAMES = {
    "allow": "ALLOW",
    "deny": "DENY",
    "freeze": "FREEZE",
    "approval_required": "REQUIRE_APPROVAL",
}


def decision_name(decision: str) -> str:
    return DECISION_NAMES.get(decision, decision.upper())


@dataclass(frozen=True, slots=True)
class RecordedDecision:
    decision_id: UUID
    tool_call_id: UUID
    decision: str
    risk_score: float
    reason: str


def record_decision(
    cur: Any,
    tenant_id: UUID,
    capability: str,
    arguments: dict[str, Any] | None,
    result: PolicyResult,
    trace_id: UUID,
    *,
    argument_hash: str | None = None,
    matched_rule_ids: list[UUID] | None = None,
    create_approval: bool = True,
) -> RecordedDecision:
    """Persist one immutable policy decision bound to a trace id.

    ``cur`` must already run inside the tenant context (``set_config``) and the
    caller owns the transaction.  Matched rule ids are resolved by rule id
    string when the caller does not supply them.
    """
    import hashlib
    import json

    arguments = dict(arguments or {})
    if argument_hash is None:
        canonical = json.dumps(arguments, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        argument_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    matched_ids = list(matched_rule_ids or [])
    if result.matched_rule and not matched_ids:
        try:
            matched_ids = [UUID(str(result.matched_rule))]
        except ValueError:
            matched_ids = []
    tool_call_id = uuid4()
    decision_id = uuid4()
    cur.execute(
        """
        INSERT INTO policy.tool_calls
          (id,tenant_id,capability,arguments,argument_hash,trace_id)
        VALUES(%s,%s,%s,%s,%s,%s)
        """,
        (tool_call_id, tenant_id, capability, Json(arguments), argument_hash, str(trace_id)),
    )
    cur.execute(
        """
        INSERT INTO policy.decisions
          (id,tenant_id,tool_call_id,decision,risk_score,reason,matched_rule_ids)
        VALUES(%s,%s,%s,%s,%s,%s,%s)
        """,
        (decision_id, tenant_id, tool_call_id, decision_name(result.decision), result.risk_score,
         result.reason, matched_ids),
    )
    if create_approval and result.decision == "approval_required":
        cur.execute(
            """
            INSERT INTO control.approvals
              (tenant_id,tool_call_id,capability,argument_hash,risk_class)
            VALUES(%s,%s,%s,%s,%s)
            ON CONFLICT (tenant_id,tool_call_id) DO NOTHING
            """,
            (tenant_id, tool_call_id, capability, argument_hash, result.risk_class),
        )
    return RecordedDecision(decision_id, tool_call_id, decision_name(result.decision), result.risk_score, result.reason)
