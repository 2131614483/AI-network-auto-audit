"""Deterministic extraction of real hand-offs / node uses from the attempt ledger.

Pure functions only.  Input is the row shape returned by
``AttemptStore.query_attempts`` (one row per node attempt, with
``input_bindings`` / ``output_refs`` jsonb already decoded).  Output is the
minimal experience evidence (``Handover`` / ``NodeUse``).

A consumer's ``input_bindings[port].source_instance`` points at the producer
node instance in the same run; resolving that instance's plugin id yields one
real producer→consumer hand-off.  Seed / external inputs have no
``source_instance`` and are skipped (they are not subject-to-subject edges).
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import Any

from packages.experience.types import (
    TERMINAL_STATUSES,
    Handover,
    NodeUse,
)


def _parse_ts(value: object) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def latency_ms(created_at: object, finished_at: object) -> int | None:
    start = _parse_ts(created_at)
    end = _parse_ts(finished_at)
    if start is None or end is None:
        return None
    millis = (end - start).total_seconds() * 1000.0
    return max(0, int(millis))


def _final_attempt_by_instance(
    attempts: Iterable[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Keep the highest attempt_seq (the final retry) per node instance."""
    latest: dict[str, dict[str, Any]] = {}
    for attempt in attempts:
        status = str(attempt.get("status") or "")
        if status not in TERMINAL_STATUSES:
            continue
        instance = str(attempt.get("node_instance_id") or "")
        if not instance:
            continue
        seq = int(attempt.get("attempt_seq") or 0)
        current = latest.get(instance)
        if current is None or seq >= int(current.get("attempt_seq") or 0):
            latest[instance] = attempt
    return latest


def extract_node_uses(attempts: Iterable[dict[str, Any]]) -> list[NodeUse]:
    """One NodeUse per node instance, from its final terminal attempt."""
    uses: list[NodeUse] = []
    for instance, attempt in sorted(_final_attempt_by_instance(attempts).items()):
        uses.append(
            NodeUse(
                plugin_id=str(attempt.get("plugin_id") or ""),
                capability=str(attempt.get("capability") or ""),
                plugin_version=str(attempt.get("plugin_version") or ""),
                attempt_id=str(attempt.get("attempt_id") or ""),
                attempt_seq=int(attempt.get("attempt_seq") or 0),
                status=str(attempt.get("status") or ""),
                error_kind=(str(attempt["error_kind"]) if attempt.get("error_kind") else None),
                latency_ms=latency_ms(attempt.get("created_at"), attempt.get("finished_at")),
            )
        )
    return uses


def extract_handovers(attempts: Iterable[dict[str, Any]]) -> list[Handover]:
    """Real producer→consumer hand-offs inside one run, deterministic order."""
    final = _final_attempt_by_instance(attempts)
    by_instance = {
        instance: {
            "plugin_id": str(attempt.get("plugin_id") or ""),
            "attempt_id": str(attempt.get("attempt_id") or ""),
        }
        for instance, attempt in final.items()
    }
    handovers: list[Handover] = []
    seen: set[tuple[str, str, str, str]] = set()
    for target_instance in sorted(final):
        target = final[target_instance]
        target_plugin = str(target.get("plugin_id") or "")
        target_attempt_id = str(target.get("attempt_id") or "")
        bindings = target.get("input_bindings") or {}
        if not isinstance(bindings, dict):
            continue
        for contract_id in sorted(bindings):
            binding = bindings[contract_id]
            if not isinstance(binding, dict):
                continue
            source_instance = binding.get("source_instance")
            if not source_instance or source_instance not in by_instance:
                continue  # external/seed input or unresolved producer
            source = by_instance[str(source_instance)]
            source_plugin = str(source["plugin_id"])
            if not source_plugin or source_plugin == target_plugin:
                continue  # no subject or self-loop
            dedupe_key = (source_instance, target_instance, str(contract_id), target_attempt_id)
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            handovers.append(
                Handover(
                    source_plugin_id=source_plugin,
                    target_plugin_id=target_plugin,
                    contract_id=str(contract_id),
                    source_instance=str(source_instance),
                    target_instance=target_instance,
                    source_attempt_id=str(source["attempt_id"]),
                    target_attempt_id=target_attempt_id,
                    source_port=(str(binding["source_port"]) if binding.get("source_port") else None),
                    artifact_sha256=(str(binding["sha256"]) if binding.get("sha256") else None),
                    status=str(target.get("status") or ""),
                    latency_ms=latency_ms(target.get("created_at"), target.get("finished_at")),
                )
            )
    return handovers
