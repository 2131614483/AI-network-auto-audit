"""Contract-first unit tests for M4: approval requests, chain execution, ledgers.

Covers the three new JSON Schemas and the pure helpers in ``approvals.py`` /
``executor.py``: the approval state machine, fail-closed gating, deterministic
simulated checksums and schema-valid ledger projections.
"""

from __future__ import annotations

import json

import pytest
from jsonschema import Draft202012Validator

from packages.plugin_topology.approvals import approval_ref_for, transition_status
from packages.plugin_topology.contracts import (
    SCHEMA_DIR,
    ApprovalRequest,
    ChainExecute,
    validate_approval,
    validate_execute,
    validate_ledger,
)
from packages.plugin_topology.executor import (
    EXECUTION_MODE,
    build_ledger_entry,
    gate_blockers,
    node_output_checksum,
)

_TRACE = "00000000-0000-0000-0000-0000000000a4"


def _validator(name: str) -> Draft202012Validator:
    return Draft202012Validator(json.loads((SCHEMA_DIR / name).read_text(encoding="utf-8")))


# -- approval schema ----------------------------------------------------------


def test_approval_schema_accepts_approve_without_reason() -> None:
    validator = _validator("topology-approval.schema.json")
    validator.validate(
        {
            "chain_key": "chain-" + "a" * 16, "slot_key": "slot.one", "decision": "approve",
            "idempotency_key": "aprv-1",
        }
    )


def test_approval_schema_requires_reason_for_reject() -> None:
    validator = _validator("topology-approval.schema.json")
    errors = list(
        validator.iter_errors(
            {"chain_key": "chain-" + "a" * 16, "slot_key": "slot.one", "decision": "reject", "idempotency_key": "aprv-2"}
        )
    )
    assert any("reason" in error.message for error in errors)


def test_approval_schema_rejects_unknown_decision_and_missing_key() -> None:
    validator = _validator("topology-approval.schema.json")
    errors = list(
        validator.iter_errors(
            {"chain_key": "chain-" + "a" * 16, "slot_key": "slot.one", "decision": "auto_approve", "reason": "x"}
        )
    )
    assert any(list(error.path) and error.path[-1] == "decision" for error in errors)
    errors = list(
        validator.iter_errors(
            {"chain_key": "chain-" + "a" * 16, "slot_key": "slot.one", "decision": "approve"}
        )
    )
    assert any("idempotency_key" in error.message for error in errors)


def test_approval_request_model_parse() -> None:
    ok = ApprovalRequest.parse(
        {"chain_key": "chain-" + "a" * 16, "slot_key": "slot.one", "decision": "reject",
         "idempotency_key": "k1", "reason": "证据不满足"}
    )
    assert ok.decision == "reject"
    assert ok.reason == "证据不满足"
    with pytest.raises(ValueError, match="invalid topology approval"):
        ApprovalRequest.parse({"chain_key": "chain-" + "a" * 16, "slot_key": "slot.one", "decision": "reject"})
    with pytest.raises(ValueError, match="invalid topology approval"):
        validate_approval({"chain_key": "chain-" + "a" * 16, "slot_key": "s", "decision": "auto"})


# -- execution request schema -------------------------------------------------


def test_execution_request_schema_accepts_simulated_and_isolated_requires_reason() -> None:
    validator = _validator("chain-execution-request.schema.json")
    # simulated 保持 M4 语义：无需 reason，即使载荷携带空 reason 键也通过
    validator.validate({"chain_key": "chain-" + "a" * 16, "mode": "simulated", "idempotency_key": "exec-1"})
    validator.validate(
        {"chain_key": "chain-" + "a" * 16, "mode": "simulated", "idempotency_key": "exec-1b", "reason": ""}
    )
    # isolated 通过：必须说明演练用途
    validator.validate(
        {"chain_key": "chain-" + "a" * 16, "mode": "isolated", "idempotency_key": "exec-2",
         "reason": "受限只读演练"}
    )
    # isolated 空 reason 拒绝
    errors = list(
        validator.iter_errors(
            {"chain_key": "chain-" + "a" * 16, "mode": "isolated", "idempotency_key": "exec-3"}
        )
    )
    assert any("reason" in error.message for error in errors)
    # 任意其他 mode 拒绝
    errors = list(
        validator.iter_errors(
            {"chain_key": "chain-" + "a" * 16, "mode": "auto", "idempotency_key": "exec-4"}
        )
    )
    assert any("mode" in error.json_path for error in errors)
    # 缺幂等键拒绝
    errors = list(
        validator.iter_errors({"chain_key": "chain-" + "a" * 16, "mode": "simulated"})
    )
    assert any("idempotency_key" in error.message for error in errors)


def test_execution_request_model_parse() -> None:
    ok = ChainExecute.parse({"chain_key": "chain-" + "a" * 16, "mode": "simulated", "idempotency_key": "exec-3"})
    assert ok.mode == EXECUTION_MODE
    iso = ChainExecute.parse(
        {"chain_key": "chain-" + "a" * 16, "mode": "isolated", "idempotency_key": "exec-5",
         "reason": "受限只读演练"}
    )
    assert iso.mode == "isolated"
    with pytest.raises(ValueError, match="invalid chain execution request"):
        ChainExecute.parse({"chain_key": "chain-" + "a" * 16, "mode": "auto", "idempotency_key": "x"})
    with pytest.raises(ValueError, match="invalid chain execution request"):
        validate_execute({"chain_key": "chain-" + "a" * 16, "mode": "simulated"})


# -- ledger schema ------------------------------------------------------------


def test_ledger_schema_rejects_payload_body() -> None:
    validator = _validator("execution-ledger.schema.json")
    base = {
        "execution_id": "exec-" + "b" * 16, "chain_key": "chain-" + "a" * 16, "slot_key": "slot.one",
        "ordinal": 0, "mode": "simulated", "input_refs": [], "output_refs": ["artifact://out/1"],
        "output_contract": "contracts/jsonschema/x@1",
        "output_checksum": node_output_checksum(slot_key="slot.one", ordinal=0, version="1.0.0",
                                                input_refs=[], output_refs=["artifact://out/1"]),
        "status": "succeeded", "policy_ref": "", "trace_id": _TRACE,
    }
    validator.validate(base)
    errors = list(validator.iter_errors({**base, "payload": {"evidence": "body"}}))
    assert errors  # additionalProperties: false rejects payload bodies
    errors = list(validator.iter_errors({**base, "status": "executing"}))
    assert any("status" in error.json_path for error in errors)


def test_ledger_schema_accepts_isolated_mode_with_real_artifact_refs() -> None:
    validator = _validator("execution-ledger.schema.json")
    base = {
        "execution_id": "exec-" + "c" * 16, "chain_key": "chain-" + "a" * 16, "slot_key": "slot.one",
        "ordinal": 0, "mode": "isolated", "input_refs": [],
        "output_refs": ["file:///s/chains/c/slot/out-abc.json"],
        "output_contract": "", "output_checksum": "d" * 64,
        "status": "succeeded", "policy_ref": "policy_allowed", "trace_id": _TRACE,
        "plugin_id": "audit.ledger-quality", "plugin_version": "0.1.0",
        "runtime_code_sha256": "e" * 64, "input_sha256": "f" * 64,
        "output_artifact_refs": [
            {"uri": "file:///s/chains/c/slot/out-abc.json", "sha256": "a" * 64, "media_type": "application/json"},
        ],
    }
    validator.validate(base)  # isolated + artifact refs 通过
    # artifact ref 缺 sha256 拒绝
    errors = list(
        validator.iter_errors(
            {**base, "output_artifact_refs": [{"uri": "file:///x", "media_type": "application/json"}]}
        )
    )
    assert any("sha256" in error.message for error in errors)
    # 非法 mode 拒绝
    errors = list(validator.iter_errors({**base, "mode": "auto"}))
    assert any("mode" in error.json_path for error in errors)


def test_validate_ledger_passes_service_projection() -> None:
    entry = build_ledger_entry(
        execution_id="exec-" + "c" * 16, chain_key="chain-" + "a" * 16, slot_key="slot.one", ordinal=1,
        input_refs=["contracts/jsonschema/audit-quality-candidates@1"],
        output_refs=["artifact://notes/1"], output_contract="contracts/jsonschema/research-note@1",
        output_checksum="a" * 64, policy_ref="policy_allowed", trace_id=_TRACE,
    )
    validate_ledger(entry)  # no raise


# -- approval state machine ---------------------------------------------------


def test_transition_approve_only_for_materialized_requires_approval() -> None:
    assert transition_status("requires_approval", "materialized", "approve") == "approved_projection"
    assert transition_status("requires_approval", "materialized", "reject") == "denied"
    with pytest.raises(ValueError, match="not awaiting approval"):
        transition_status("allowed", "policy_allowed", "approve")
    with pytest.raises(ValueError, match="not awaiting approval"):
        transition_status("requires_approval", "approved_projection", "approve")
    with pytest.raises(ValueError, match="invalid approval decision"):
        transition_status("requires_approval", "materialized", "auto_approve")


def test_approval_ref_is_deterministic() -> None:
    ref1 = approval_ref_for(chain_key="chain-" + "a" * 16, slot_key="slot.one", decision="approve", approver="u-1")
    ref2 = approval_ref_for(chain_key="chain-" + "a" * 16, slot_key="slot.one", decision="approve", approver="u-1")
    ref3 = approval_ref_for(chain_key="chain-" + "a" * 16, slot_key="slot.one", decision="reject", approver="u-1")
    assert ref1 == ref2
    assert ref1.startswith("apr-")
    assert ref1 != ref3


# -- fail-closed gating -------------------------------------------------------


def test_gate_blockers_detects_denied_and_pending_approval() -> None:
    intents = [
        {"slot_key": "slot.ok", "policy_decision": "allowed", "status": "policy_allowed"},
        {"slot_key": "slot.denied", "policy_decision": "denied", "status": "denied"},
        {"slot_key": "slot.pending", "policy_decision": "requires_approval", "status": "materialized"},
        {"slot_key": "slot.approved", "policy_decision": "requires_approval", "status": "approved_projection"},
    ]
    blockers = gate_blockers(intents)
    assert blockers == [
        {"slot_key": "slot.denied", "reason": "intent denied"},
        {"slot_key": "slot.pending", "reason": "approval pending"},
    ]


def test_gate_blockers_pass_when_every_node_green() -> None:
    intents = [
        {"slot_key": "slot.a", "policy_decision": "allowed", "status": "policy_allowed"},
        {"slot_key": "slot.b", "policy_decision": "requires_approval", "status": "approved_projection"},
    ]
    assert gate_blockers(intents) == []


# -- deterministic simulated checksums ----------------------------------------


def test_node_output_checksum_is_deterministic_and_order_stable() -> None:
    c1 = node_output_checksum(slot_key="s", ordinal=0, version="1.0.0", input_refs=["b", "a"], output_refs=["y", "x"])
    c2 = node_output_checksum(slot_key="s", ordinal=0, version="1.0.0", input_refs=["a", "b"], output_refs=["x", "y"])
    c3 = node_output_checksum(slot_key="s", ordinal=0, version="1.0.0", input_refs=["a"], output_refs=["x"])
    assert c1 == c2
    assert len(c1) == 64
    assert c1 != c3