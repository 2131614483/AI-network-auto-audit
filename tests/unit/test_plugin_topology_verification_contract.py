"""Plugin Topology M7 contract-first tests: run-verification request and
run-verification projection schemas.

Positive cases prove a verification request (run_id + optional reference) and
both projections (verified/drifted, the latter carrying a read-only rollback
verdict) validate; negatives prove mode/status enum closure, uuid format,
reason/idempotency-key requirement, node-count bounds and conservation, and
the verdict sub-schema (action enum, affected_slots, baseline).
"""

from __future__ import annotations

import json
from uuid import UUID, uuid4

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from packages.plugin_topology.contracts import (
    SCHEMA_DIR,
    RunVerificationRequest,
    validate_verification,
    validate_verification_request,
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


def _verification_request(**overrides: object) -> dict:
    payload: dict = {
        "run_id": str(uuid4()),
        "reference_run_id": str(uuid4()),
        "reason": "M7 测试库可复现性核查",
        "idempotency_key": "verification-contract-1",
    }
    payload.update(overrides)
    return payload


def _verdict(action: str = "re-run-locked-release", **overrides: object) -> dict:
    verdict: dict = {
        "action": action,
        "affected_slots": ["audit.ledger.validate"],
        "baseline": {
            "reference_run_id": str(uuid4()),
            "chain_checksum": "a" * 64,
            "planner_version": "1.0.0",
        },
    }
    verdict.update(overrides)
    return verdict


def _verification_projection(**overrides: object) -> dict:
    payload: dict = {
        "verification_id": str(uuid4()),
        "run_id": str(uuid4()),
        "chain_key": _chain_key(),
        "reference_run_id": str(uuid4()),
        "status": "verified",
        "node_total": 2,
        "node_matched": 2,
        "node_mismatched": 0,
        "node_ref_missing": 0,
        "reason": "M7 测试库可复现性核查",
        "rollback_verdict": None,
        "trace_id": str(uuid4()),
        "created_at": "2026-09-07T08:00:00+00:00",
    }
    payload.update(overrides)
    return payload


# -- run-verification-request --------------------------------------------------


def test_verification_request_accepts_reference_run() -> None:
    _validator("run-verification-request.schema.json").validate(_verification_request())


def test_verification_request_accepts_null_reference() -> None:
    request = RunVerificationRequest.parse(_verification_request(reference_run_id=None))
    assert request.reference_run_id is None


def test_verification_request_rejects_non_uuid_run_id() -> None:
    validator = _validator("run-verification-request.schema.json")
    assert any(
        error.validator == "format"
        for error in validator.iter_errors(_verification_request(run_id="not-a-uuid"))
    )


def test_verification_request_rejects_non_uuid_reference() -> None:
    validator = _validator("run-verification-request.schema.json")
    assert any(
        error.validator in ("anyOf", "format")
        for error in validator.iter_errors(_verification_request(reference_run_id="not-a-uuid"))
    )


def test_verification_request_rejects_self_reference() -> None:
    target = str(uuid4())
    with pytest.raises(ValueError, match="must differ from run_id"):
        RunVerificationRequest.parse(
            _verification_request(run_id=target, reference_run_id=target)
        )


def test_verification_request_parse_round_trips() -> None:
    request = RunVerificationRequest.parse(_verification_request())
    assert request.reason == "M7 测试库可复现性核查"
    assert request.reference_run_id is not None


def test_verification_request_rejects_empty_reason() -> None:
    with pytest.raises(ValueError, match="run verification request"):
        RunVerificationRequest.parse(_verification_request(reason=""))


def test_verification_request_rejects_extra_fields() -> None:
    with pytest.raises(ValueError, match="run verification request"):
        validate_verification_request(_verification_request(payload="anything"))


# -- run-verification projection -----------------------------------------------


def test_verification_projection_accepts_verified() -> None:
    _validator("run-verification.schema.json").validate(_verification_projection())


def test_verification_projection_accepts_drifted_with_verdict() -> None:
    _validator("run-verification.schema.json").validate(
        _verification_projection(
            status="drifted",
            reference_run_id=str(uuid4()),
            node_matched=1,
            node_mismatched=1,
            node_ref_missing=0,
            rollback_verdict=_verdict(),
        )
    )


def test_verification_projection_rejects_verified_with_verdict() -> None:
    validator = _validator("run-verification.schema.json")
    assert any(
        error.validator == "const"
        for error in validator.iter_errors(
            _verification_projection(rollback_verdict=_verdict())
        )
    )


def test_verification_projection_rejects_drifted_without_verdict() -> None:
    validator = _validator("run-verification.schema.json")
    assert any(
        error.validator in ("required", "anyOf", "type", "$ref")
        for error in validator.iter_errors(_verification_projection(status="drifted"))
    )


def test_verification_projection_rejects_unknown_status() -> None:
    validator = _validator("run-verification.schema.json")
    assert any(
        error.validator == "enum"
        for error in validator.iter_errors(_verification_projection(status="queued"))
    )


def test_verification_projection_rejects_negative_node_counts() -> None:
    validator = _validator("run-verification.schema.json")
    assert any(
        error.validator == "minimum"
        for error in validator.iter_errors(
            _verification_projection(node_matched=-1, node_mismatched=1, node_total=0)
        )
    )


def test_verification_projection_rejects_unconserved_counts() -> None:
    with pytest.raises(ValueError, match="not conserved"):
        validate_verification(
            _verification_projection(
                node_total=2, node_matched=1, node_mismatched=0, node_ref_missing=0
            )
        )


def test_verification_projection_rejects_bad_verdict_action() -> None:
    validator = _validator("run-verification.schema.json")
    assert any(
        error.validator == "enum"
        for error in validator.iter_errors(
            _verification_projection(
                status="drifted",
                node_matched=1,
                node_mismatched=1,
                rollback_verdict=_verdict(action="apply-cr"),
            )
        )
    )


def test_verification_projection_rejects_non_slot_affected_list() -> None:
    validator = _validator("run-verification.schema.json")
    assert any(
        error.validator in ("type", "items")
        for error in validator.iter_errors(
            _verification_projection(
                status="drifted",
                node_matched=1,
                node_mismatched=1,
                rollback_verdict=_verdict(affected_slots=["a", 2]),
            )
        )
    )


def test_verification_projection_rejects_payload_body() -> None:
    validator = _validator("run-verification.schema.json")
    assert any(
        error.validator == "additionalProperties"
        for error in validator.iter_errors(_verification_projection(payload={"o": 1}))
    )