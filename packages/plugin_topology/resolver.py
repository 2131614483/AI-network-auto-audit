"""Contract compatibility matching for topology routing.

Compatibility is decided by declared contracts only: capability token,
semver major/minor compatibility and explicit input/output names.  Name
guessing (fuzzy string similarity) is deliberately rejected so the planner
can only chain blueprints whose contracts were authored to line up.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class ContractCompatibility:
    """One deterministic compatibility result between a producer and consumer."""

    producer_capability: str
    consumer_capability: str
    compatible: bool
    reasons: tuple[str, ...] = field(default_factory=tuple)


def _parse_version(version: str) -> tuple[int, int, int] | None:
    match = re.match(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-|$)", version)
    if match is None:
        return None
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


class ContractResolver:
    """Match a producer blueprint's output contract against a consumer's input need."""

    @staticmethod
    def version_compatible(producer_version: str, consumer_version: str | None) -> bool:
        """Producer satisfies the consumer when major matches and minor is >= requested."""
        if not consumer_version:
            return True
        producer = _parse_version(producer_version)
        required = _parse_version(consumer_version)
        if producer is None or required is None:
            return False
        if producer[0] != required[0]:
            return False
        return producer[1] >= required[1]

    @staticmethod
    def capability_matches(pattern: str, candidate: str) -> bool:
        """Exact token match; ``*`` wildcards are allowed in a consumer need only."""
        if "*" in pattern:
            return re.fullmatch(pattern.replace(".", r"\.").replace("*", ".*"), candidate) is not None
        return pattern == candidate

    @classmethod
    def resolve(
        cls,
        producer: dict[str, Any],
        consumer: dict[str, Any],
    ) -> ContractCompatibility:
        """Compare one producer contract snapshot against one consumer need.

        ``producer`` carries ``capability``/``version``/``outputs``.
        ``consumer`` carries ``capability``/``version``/``inputs``.
        Fuzzy name guessing is rejected: only declared contract tokens match.
        """
        reasons: list[str] = []
        producer_capability = str(producer.get("capability") or "")
        consumer_capability = str(consumer.get("capability") or "")
        if not producer_capability or not consumer_capability:
            return ContractCompatibility(producer_capability, consumer_capability, False, ("capability must be declared",))

        if not cls.capability_matches(consumer_capability, producer_capability):
            reasons.append(
                f"capability mismatch: consumer '{consumer_capability}' vs producer '{producer_capability}'"
            )
        if not cls.version_compatible(
            str(producer.get("version") or ""), consumer.get("version")
        ):
            reasons.append(
                f"version mismatch: producer '{producer.get('version')}' incompatible with consumer "
                f"'{consumer.get('version')}'"
            )

        producer_outputs = {str(item) for item in producer.get("outputs") or []}
        consumer_inputs = {str(item) for item in consumer.get("inputs") or []}
        if consumer_inputs:
            shared = consumer_inputs & producer_outputs
            if not shared:
                reasons.append(
                    "no shared contract item: consumer inputs "
                    f"{sorted(consumer_inputs)} vs producer outputs {sorted(producer_outputs)}"
                )

        compatible = not reasons
        return ContractCompatibility(
            producer_capability=producer_capability,
            consumer_capability=consumer_capability,
            compatible=compatible,
            reasons=tuple(reasons),
        )
