"""Read-only adapter contracts between the plan layer and runtime layers (M3).

These projections prove a plan can be handed to the policy layer, the plugin
runtime, or an agent assembler without executing anything: every contract is
``plan_only`` and never invokes a plugin.  (Port-level schema adapters live in
``port_adapters.py``.)
"""

from __future__ import annotations

from typing import Any

from .chain import InvocationChain, InvocationIntent


def runtime_adapter_contract(
    chain: InvocationChain, intents: tuple[InvocationIntent, ...]
) -> dict[str, Any]:
    """plugin_runtime adapter: nodes + port flow, execution never invoked."""
    return {
        "adapter": "plugin_runtime",
        "mode": "plan_only",
        "execution_invoked": False,
        "nodes": [{"slot_key": intent.slot_key} for intent in intents],
        "port_flow": [
            {
                "producer_slot": binding.producer_slot,
                "consumer_slot": binding.consumer_slot,
                "relation_type": binding.relation_type,
                "bind_mode": binding.bind_mode,
            }
            for binding in chain.port_bindings
        ],
    }


def policy_adapter_contract(
    chain: InvocationChain,
    intents: tuple[InvocationIntent, ...],
    *,
    decisions: dict[str, tuple[str, str]] | None = None,
) -> dict[str, Any]:
    """policy adapter: approval counts + per-slot decision, read-only projection."""
    decisions = decisions or {}
    slot_decisions: list[dict[str, str]] = []
    requires_approval = 0
    for intent in intents:
        decision, _status = decisions.get(intent.slot_key, (intent.policy_decision, intent.status))
        slot_decisions.append({"slot_key": intent.slot_key, "policy_decision": decision})
        if decision == "requires_approval":
            requires_approval += 1
    return {
        "mode": "plan_only",
        "projection": "read_only",
        "approval_required_total": requires_approval,
        "decisions": slot_decisions,
    }


def agent_adapter_contract(
    chain: InvocationChain, intents: tuple[InvocationIntent, ...]
) -> dict[str, Any]:
    """agent_task_assembly adapter: approval-gated steps, no task created."""
    return {
        "adapter": "agent_task_assembly",
        "mode": "plan_only",
        "no_task_created": True,
        "steps": [
            {"action": "request_approval_if_required", "rollback_coordinator_ref": None}
            for _intent in intents
        ],
    }
