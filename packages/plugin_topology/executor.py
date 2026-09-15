"""M4 simulated chain execution and verification ledger helpers.

The whole-chain executor is a pure database-internal state projection:
``mode`` is always ``simulated``, no subprocess is ever started, and every
ledger row carries only contract refs plus a deterministic SHA256.  The
isolated-execution branch (reusing verified builtin plugin runtimes) is
deliberately NOT implemented here - only its gate vocabulary is kept so the
contract stays honest about what a later milestone could enable.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from .contracts import validate_ledger

EXECUTION_MODE = "simulated"
LEDGER_STATUSES = ("pending", "succeeded", "failed")
_MAX_BLOCKERS = 32


def gate_blockers(intents: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Return per-slot reasons why the chain cannot execute (fail-closed).

    An intent blocks execution when its Policy decision is ``denied`` or when
    a ``requires_approval`` intent has not yet reached ``approved_projection``.
    ``isolated`` requests need additional gates in a later milestone; here only
    ``simulated`` is accepted by the contract.
    """
    blockers: list[dict[str, str]] = []
    for intent in intents:
        slot = str(intent.get("slot_key") or "")
        decision = str(intent.get("policy_decision") or "")
        status = str(intent.get("status") or "")
        if decision == "denied" or status == "denied":
            blockers.append({"slot_key": slot, "reason": "intent denied"})
        elif decision == "requires_approval" and status != "approved_projection":
            blockers.append({"slot_key": slot, "reason": "approval pending"})
        if len(blockers) >= _MAX_BLOCKERS:
            break
    return blockers


def node_output_checksum(
    *, slot_key: str, ordinal: int, version: str, input_refs: list[str], output_refs: list[str]
) -> str:
    """Deterministic SHA256 over the node's wiring projection."""
    payload = {
        "slot_key": slot_key,
        "ordinal": ordinal,
        "version": version,
        "input_refs": sorted(set(input_refs)),
        "output_refs": sorted(set(output_refs)),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_ledger_entry(
    *,
    execution_id: str,
    chain_key: str,
    slot_key: str,
    ordinal: int,
    input_refs: list[str],
    output_refs: list[str],
    output_contract: str,
    output_checksum: str,
    policy_ref: str,
    trace_id: str,
    status: str = "succeeded",
    started_at: str | None = None,
    finished_at: str | None = None,
) -> dict[str, Any]:
    """Build and schema-validate one execution-ledger projection."""
    entry = {
        "execution_id": execution_id,
        "chain_key": chain_key,
        "slot_key": slot_key,
        "ordinal": ordinal,
        "mode": EXECUTION_MODE,
        "input_refs": sorted(set(input_refs)),
        "output_refs": sorted(set(output_refs)),
        "output_contract": output_contract,
        "output_checksum": output_checksum,
        "status": status,
        "policy_ref": policy_ref,
        "trace_id": trace_id,
        "started_at": started_at,
        "finished_at": finished_at,
    }
    validate_ledger(entry)
    return entry