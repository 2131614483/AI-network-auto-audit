"""Plugin Topology M8 contract-first tests: remediation-proposal request and
remediation-proposal projection schemas.

Positive cases prove a proposal request (drifted verification_id + action) and
the projection (status derived from the immutable evidence ledgers, baseline
inherited from the M7 rollback verdict, remediation_run_id only on an approved
proposal that actually re-ran) validate; negatives prove uuid format, action
enum closure, reason/idempotency-key requirement, status closure, empty/non-
string affected_slots, and remediation_run_id appearing on a non-approved
status are all rejected.
"""

from __future__ import annotations

import json
from uuid import UUID, uuid4

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from packages.plugin_topology.contracts import (
    SCHEMA_DIR,
    RemediationProposalRequest,
    validate_proposal,
    validate_proposal_request,
)

_format_checker = FormatChecker()


@_format_checker.checks("uuid")
def _check_uuid(value: object) -> bool:
    try:
        UUID(str(value))
    except ValueError:
        return False
    return True


def _validator(name: str) -> Draft202012Validator:
    return Draft202012Validator(
        json.loads((SCHEMA_DIR / name).read_text(encoding="utf-8")), format_checker=_format_checker
    )


def _chain_key() -> str:
    return "chain-" + uuid4().hex[:16]


def _proposal_request(**overrides: object) -> dict:
    payload: dict = {
        "verification_id": str(uuid4()),
        "action": "re-run-locked-release",
        "reason": "M8 测试库漂移处置",
        "idempotency_key": "proposal-contract-1",
    }
    payload.update(overrides)
    return payload


def _baseline(reference: str | None = None) -> dict:
    return {
        "reference_run_id": reference or str(uuid4()),
        "chain_checksum": "a" * 64,
        "planner_version": "1.0.0",
    }


def _proposal_projection(**overrides: object) -> dict:
    payload: dict = {
        "proposal_id": str(uuid4()),
        "verification_id": str(uuid4()),
        "run_id": str(uuid4()),
        "chain_key": _chain_key(),
        "status": "pending_approval",
        "action": "re-run-locked-release",
        "affected_slots": ["audit.ledger.validate", "quant.research-note.draft"],
        "baseline": _baseline(),
        "remediation_run_id": None,
        "reason": "M8 测试库漂移处置",
        "trace_id": str(uuid4()),
        "created_at": "2026-09-07T08:00:00+00:00",
    }
    payload.update(overrides)
    return payload


# -- remediation-proposal-request ----------------------------------------------


def test_proposal_request_accepts_valid_payload() -> None:
    _validator("remediation-proposal-request.schema.json").validate(_proposal_request())


def test_proposal_request_accepts_all_action_enum() -> None:
    validator = _validator("remediation-proposal-request.schema.json")
    for action in ("re-verify", "re-run-locked-release", "escalate-human"):
        validator.validate(_proposal_request(action=action))


def test_proposal_request_rejects_non_uuid_verification_id() -> None:
    validator = _validator("remediation-proposal-request.schema.json")
    assert any(
        error.validator == "format"
        for error in validator.iter_errors(_proposal_request(verification_id="not-a-uuid"))
    )


def test_proposal_request_rejects_unknown_action() -> None:
    validator = _validator("remediation-proposal-request.schema.json")
    assert any(
        error.validator == "enum"
        for error in validator.iter_errors(_proposal_request(action="apply-cr"))
    )


def test_proposal_request_parse_round_trips() -> None:
    request = RemediationProposalRequest.parse(_proposal_request())
    assert request.action == "re-run-locked-release"
    assert request.reason == "M8 测试库漂移处置"


def test_proposal_request_rejects_empty_reason() -> None:
    with pytest.raises(ValueError, match="remediation proposal request"):
        RemediationProposalRequest.parse(_proposal_request(reason=""))


def test_proposal_request_rejects_extra_fields() -> None:
    with pytest.raises(ValueError, match="remediation proposal request"):
        validate_proposal_request(_proposal_request(payload="anything"))


# -- remediation-proposal projection -------------------------------------------


def test_proposal_projection_accepts_pending_approval() -> None:
    _validator("topology-remediation-proposal.schema.json").validate(_proposal_projection())


def test_proposal_projection_accepts_approved_with_run() -> None:
    _validator("topology-remediation-proposal.schema.json").validate(
        _proposal_projection(
            status="approved",
            remediation_run_id=str(uuid4()),
        )
    )


def test_proposal_projection_rejects_unknown_status() -> None:
    validator = _validator("topology-remediation-proposal.schema.json")
    assert any(
        error.validator == "enum"
        for error in validator.iter_errors(_proposal_projection(status="draft"))
    )


def test_proposal_projection_rejects_run_on_pending_approval() -> None:
    validator = _validator("topology-remediation-proposal.schema.json")
    assert any(
        error.validator in ("const", "type")
        for error in validator.iter_errors(_proposal_projection(remediation_run_id=str(uuid4())))
    )


def test_proposal_projection_rejects_run_on_rejected() -> None:
    validator = _validator("topology-remediation-proposal.schema.json")
    assert any(
        error.validator in ("const", "type")
        for error in validator.iter_errors(
            _proposal_projection(status="rejected", remediation_run_id=str(uuid4()))
        )
    )


def test_proposal_projection_rejects_empty_affected_slots() -> None:
    validator = _validator("topology-remediation-proposal.schema.json")
    assert any(
        error.validator == "minItems"
        for error in validator.iter_errors(_proposal_projection(affected_slots=[]))
    )


def test_proposal_projection_rejects_non_slot_affected_list() -> None:
    validator = _validator("topology-remediation-proposal.schema.json")
    assert any(
        error.validator in ("type", "items")
        for error in validator.iter_errors(_proposal_projection(affected_slots=["a", 2]))
    )


def test_proposal_projection_rejects_bad_baseline_checksum() -> None:
    validator = _validator("topology-remediation-proposal.schema.json")
    baseline = _baseline()
    baseline["chain_checksum"] = "not-a-sha256"
    assert any(
        error.validator == "pattern"
        for error in validator.iter_errors(_proposal_projection(baseline=baseline))
    )


def test_proposal_projection_validate_passes() -> None:
    validate_proposal(_proposal_projection())


def test_proposal_projection_validate_rejects_invalid() -> None:
    with pytest.raises(ValueError, match="remediation proposal"):
        validate_proposal(_proposal_projection(action="apply-cr"))


def test_proposal_projection_rejects_payload_body() -> None:
    validator = _validator("topology-remediation-proposal.schema.json")
    assert any(
        error.validator == "additionalProperties"
        for error in validator.iter_errors(_proposal_projection(payload={"o": 1}))
    )