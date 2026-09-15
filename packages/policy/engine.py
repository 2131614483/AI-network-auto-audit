from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from fnmatch import fnmatchcase
from typing import Any

KNOWN_RISK_CLASSES = frozenset({"read_only", "low", "medium", "high", "critical"})
RISK_SCORES = {"read_only": 5.0, "low": 20.0, "medium": 50.0, "high": 75.0, "critical": 100.0}


@dataclass(frozen=True, slots=True)
class PolicyResult:
    decision: str
    risk_class: str
    reason: str
    matched_rule: str | None = None
    risk_score: float = 50.0


class PolicyEngine:
    """Deterministic allow/deny/approval evaluator shared by GUI and Agents."""

    def __init__(
        self,
        *,
        allow: list[str] | None = None,
        deny: list[str] | None = None,
        auto: bool = False,
        rules: list[dict[str, Any]] | None = None,
    ) -> None:
        self.allow = tuple(allow or ())
        self.deny = tuple(deny or ())
        self.auto = auto
        self.rules = tuple(rules or ())

    def evaluate(
        self,
        capability: str,
        arguments: dict[str, Any] | None = None,
        risk_class: str = "medium",
        *,
        side_effects: str | None = None,
    ) -> PolicyResult:
        """Evaluate a capability using deny-first deterministic semantics.

        Rule matching is intentionally small and JSON-compatible: capability
        globs, risk/side-effect filters and exact argument constraints are
        supported.  Callers cannot make an unknown/invalid capability or risk
        class executable by asking for AUTO mode.
        """
        capability = capability.strip()
        arguments = arguments or {}
        if not capability or any(char.isspace() for char in capability):
            return PolicyResult("deny", "critical", "capability must be a non-empty token", risk_score=100.0)
        if risk_class not in KNOWN_RISK_CLASSES:
            return PolicyResult("deny", "critical", f"unsupported risk class: {risk_class}", risk_score=100.0)
        score = RISK_SCORES[risk_class]

        matching_rules = [
            rule for rule in self.rules
            if self._is_active(rule)
            and self._matches(rule, capability, arguments, risk_class, side_effects)
        ]
        # A deny/freeze rule always wins, independent of list or priority order.
        for rule in sorted(matching_rules, key=lambda item: int(item.get("priority", 0)), reverse=True):
            effect = str(rule.get("effect", "")).lower()
            if effect in {"deny", "freeze"}:
                return PolicyResult(
                    "deny" if effect == "deny" else "freeze",
                    "critical" if effect == "freeze" else risk_class,
                    f"matched {effect} rule: {rule.get('rule_id', 'unnamed')}",
                    str(rule.get("rule_id")) if rule.get("rule_id") else None,
                    100.0 if effect == "freeze" else score,
                )
        for deny_pattern in self.deny:
            if fnmatchcase(capability, deny_pattern):
                return PolicyResult(
                    "deny", "critical", f"matched deny rule: {deny_pattern}", deny_pattern, 100.0
                )
        for rule in matching_rules:
            if str(rule.get("effect", "")).lower() == "require_approval":
                return PolicyResult(
                    "approval_required", risk_class,
                    f"matched approval rule: {rule.get('rule_id', 'unnamed')}",
                    str(rule.get("rule_id")) if rule.get("rule_id") else None,
                    score,
                )
        for allow_pattern in self.allow:
            if fnmatchcase(capability, allow_pattern):
                return PolicyResult(
                    "allow", risk_class, f"matched allow rule: {allow_pattern}", allow_pattern, score
                )
        for rule in matching_rules:
            if str(rule.get("effect", "")).lower() == "allow":
                return PolicyResult(
                    "allow", risk_class,
                    f"matched allow rule: {rule.get('rule_id', 'unnamed')}",
                    str(rule.get("rule_id")) if rule.get("rule_id") else None,
                    score,
                )
        if self.auto and risk_class in {"low", "read_only"}:
            return PolicyResult("allow", risk_class, "AUTO mode permits low-risk capability", risk_score=score)
        return PolicyResult("approval_required", risk_class, "no allow rule matched", risk_score=score)

    @staticmethod
    def _is_active(rule: dict[str, Any]) -> bool:
        if rule.get("enabled", True) is False:
            return False
        now = datetime.now(timezone.utc)
        for key, is_start in (("valid_from", True), ("valid_until", False)):
            raw = rule.get(key)
            if raw is None:
                continue
            if not isinstance(raw, str):
                return False
            try:
                boundary = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except ValueError:
                return False
            if boundary.tzinfo is None:
                boundary = boundary.replace(tzinfo=timezone.utc)
            if is_start and now < boundary:
                return False
            if not is_start and now >= boundary:
                return False
        return True

    @staticmethod
    def _matches(
        rule: dict[str, Any], capability: str, arguments: dict[str, Any], risk_class: str,
        side_effects: str | None,
    ) -> bool:
        match = rule.get("match", {})
        if not isinstance(match, dict):
            return False
        capabilities = match.get("capabilities", [])
        if capabilities and not any(fnmatchcase(capability, str(pattern)) for pattern in capabilities):
            return False
        risk_classes = match.get("risk_classes", [])
        if risk_classes and risk_class not in risk_classes:
            return False
        effects = match.get("side_effects", [])
        if effects and side_effects not in effects:
            return False
        expected = match.get("argument_equals", {})
        if not isinstance(expected, dict) or any(arguments.get(key) != value for key, value in expected.items()):
            return False
        return True
