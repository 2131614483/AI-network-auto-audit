"""CW5: deterministic compile-feedback errors for AI revision loops.

The compiler raises ``CompileError`` with a human message; the planner needs
stable machine-readable issues (code / node / port / suggested_action) so the
model can revise without ever toggling validation off.  This mapping is a
pure function over the message — same input, same issues.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

_KNOWN_CODES = (
    "missing_required_input",
    "contract_mismatch",
    "unsupported_feature",
    "budget_exceeded",
    "capability_unavailable",
    "data_boundary_denied",
    "duplicate_node_instance_id",
    "unknown_node_instance",
    "cycle",
)


@dataclass(frozen=True, slots=True)
class CompileIssue:
    code: str
    message: str
    node_id: str | None = None
    port_id: str | None = None
    edge_id: str | None = None
    suggested_action: str = "revise the draft; the compiler cannot be bypassed"


def _classify(message: str) -> str:
    lowered = message.lower()
    if "missing required input" in lowered or "required input" in lowered:
        return "missing_required_input"
    if "contract" in lowered and "mismatch" in lowered:
        return "contract_mismatch"
    if "cycle" in lowered:
        return "cycle"
    if "duplicate node_instance_id" in lowered:
        return "duplicate_node_instance_id"
    if "references unknown node instance" in lowered:
        return "unknown_node_instance"
    if "budget" in lowered:
        return "budget_exceeded"
    if "unsupported field" in lowered or "unsupported feature" in lowered:
        return "unsupported_feature"
    return "contract_mismatch"


def _extract_entity(message: str, pattern: str) -> str | None:
    match = re.search(pattern, message)
    return match.group(1) if match else None


def compile_issues(error: Exception) -> list[CompileIssue]:
    """Normalize one compiler error into stable machine-readable issues.

    Typed as ``Exception`` rather than ``CompileError`` because ``compile_plan``
    can also raise ``PortContractError`` (a sibling ``ValueError``), and this
    only ever classifies the message.
    """
    message = str(error)
    code = _classify(message)
    return [
        CompileIssue(
            code=code,
            message=message,
            node_id=_extract_entity(message, r"node ([\w.-]+)"),
            port_id=_extract_entity(message, r"port ([\w.-]+)"),
            edge_id=_extract_entity(message, r"edge ([\w.-]+)"),
        )
    ]


def issues_to_dicts(issues: list[CompileIssue]) -> list[dict[str, Any]]:
    return [
        {
            "code": issue.code,
            "message": issue.message,
            "node_id": issue.node_id,
            "port_id": issue.port_id,
            "edge_id": issue.edge_id,
            "suggested_action": issue.suggested_action,
        }
        for issue in issues
    ]
