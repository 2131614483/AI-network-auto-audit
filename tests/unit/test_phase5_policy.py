from __future__ import annotations

from packages.policy.engine import PolicyEngine


def test_deny_always_wins_over_allow() -> None:
    result = PolicyEngine(allow=["file.*"], deny=["file.delete"]).evaluate("file.delete")
    assert result.decision == "deny"
    assert result.risk_class == "critical"


def test_auto_only_allows_low_risk_unknown_capability() -> None:
    engine = PolicyEngine(auto=True)
    assert engine.evaluate("knowledge.read", risk_class="low").decision == "allow"
    assert engine.evaluate("system.execute", risk_class="high").decision == "approval_required"


def test_policy_rules_match_arguments_and_deny_invalid_risk() -> None:
    engine = PolicyEngine(
        auto=True,
        rules=[
            {
                "rule_id": "approve-read",
                "effect": "allow",
                "match": {
                    "capabilities": ["file.read"],
                    "argument_equals": {"path": "reports/current.csv"},
                    "risk_classes": ["read_only"],
                },
            }
        ],
    )
    assert engine.evaluate("file.read", {"path": "reports/current.csv"}, "read_only").decision == "allow"
    assert engine.evaluate("file.read", {"path": "secrets.txt"}, "read_only").decision == "allow"
    assert engine.evaluate("file.read", risk_class="unknown").decision == "deny"


def test_freeze_rule_cannot_be_overridden_by_allow() -> None:
    engine = PolicyEngine(
        allow=["system.*"],
        rules=[{"effect": "freeze", "match": {"capabilities": ["system.execute"]}}],
    )
    assert engine.evaluate("system.execute", risk_class="high").decision == "freeze"


def test_disabled_rule_is_not_evaluated() -> None:
    engine = PolicyEngine(
        rules=[
            {
                "effect": "deny",
                "enabled": False,
                "match": {"capabilities": ["file.read"]},
            }
        ],
        auto=True,
    )
    assert engine.evaluate("file.read", risk_class="read_only").decision == "allow"
