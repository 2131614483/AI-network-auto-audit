"""Plugin Topology M6 contract-first tests: chain-run request and execution-run
projection schemas, plus the execution-ledger run_id extension.

Positive and negative cases prove the schemas alone enforce the run contract:
mode is always isolated, reason is mandatory, idempotency key is required, run
status is a closed enum, node counts are non-negative, and ledger rows may
carry a run_id UUID.
"""

from __future__ import annotations

import json
from uuid import UUID, uuid4

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from packages.plugin_topology.contracts import (
    SCHEMA_DIR,
    ChainRunRequest,
    validate_run_request,
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


def _run_request(**overrides: object) -> dict:
    payload: dict = {
        "chain_key": _chain_key(),
        "mode": "isolated",
        "reason": "M6 测试库受限只读演练",
        "idempotency_key": "run-contract-1",
    }
    payload.update(overrides)
    return payload


def _run_projection(**overrides: object) -> dict:
    payload: dict = {
        "run_id": str(uuid4()),
        "chain_key": _chain_key(),
        "mode": "isolated",
        "status": "success",
        "node_total": 2,
        "node_succeeded": 2,
        "node_failed": 0,
        "reason": "M6 测试库受限只读演练",
        "trace_id": str(uuid4()),
        "started_at": "2026-09-07T08:00:00+00:00",
        "finished_at": "2026-09-07T08:00:02+00:00",
        "chain_checksum": "a" * 64,
        "planner_version": "1.0.0",
    }
    payload.update(overrides)
    return payload


# -- chain-run-request --------------------------------------------------------


def test_run_request_schema_accepts_isolated_with_reason() -> None:
    _validator("chain-run-request.schema.json").validate(_run_request())


def test_run_request_parse_round_trips() -> None:
    request = ChainRunRequest.parse(_run_request())
    assert request.mode == "isolated"
    assert request.reason == "M6 测试库受限只读演练"


def test_run_request_rejects_simulated_mode() -> None:
    validator = _validator("chain-run-request.schema.json")
    for error in validator.iter_errors(_run_request(mode="simulated")):
        assert error.validator == "const"


def test_run_request_rejects_missing_reason() -> None:
    validator = _validator("chain-run-request.schema.json")
    assert any(
        error.validator in ("required", "minLength")
        for error in validator.iter_errors(_run_request(reason=""))
    )


def test_run_request_rejects_empty_reason() -> None:
    with pytest.raises(ValueError, match="chain run request"):
        ChainRunRequest.parse(_run_request(reason=""))


def test_run_request_rejects_missing_idempotency_key() -> None:
    payload = _run_request()
    del payload["idempotency_key"]
    validator = _validator("chain-run-request.schema.json")
    assert any(
        error.validator == "required"
        for error in validator.iter_errors(payload)
    )


def test_run_request_rejects_extra_fields() -> None:
    with pytest.raises(ValueError, match="chain run request"):
        validate_run_request(_run_request(payload="anything"))


# -- execution-run projection -------------------------------------------------


def test_run_projection_schema_accepts_terminal_run() -> None:
    _validator("execution-run.schema.json").validate(_run_projection())


def test_run_projection_accepts_running_with_null_finished_at() -> None:
    _validator("execution-run.schema.json").validate(
        _run_projection(status="running", finished_at=None, node_failed=0, node_succeeded=0)
    )


def test_run_projection_rejects_unknown_status() -> None:
    validator = _validator("execution-run.schema.json")
    assert any(
        error.validator == "enum" for error in validator.iter_errors(_run_projection(status="queued"))
    )


def test_run_projection_rejects_simulated_mode() -> None:
    validator = _validator("execution-run.schema.json")
    assert any(
        error.validator == "const" for error in validator.iter_errors(_run_projection(mode="simulated"))
    )


def test_run_projection_rejects_negative_node_counts() -> None:
    validator = _validator("execution-run.schema.json")
    assert any(
        error.validator == "minimum"
        for error in validator.iter_errors(_run_projection(node_total=-1, node_failed=-2))
    )


def test_run_projection_rejects_bad_run_id() -> None:
    validator = _validator("execution-run.schema.json")
    assert any(
        error.validator == "format"
        for error in validator.iter_errors(_run_projection(run_id="not-a-uuid"))
    )


def test_run_projection_rejects_payload_body() -> None:
    validator = _validator("execution-run.schema.json")
    assert any(
        error.validator == "additionalProperties"
        for error in validator.iter_errors(_run_projection(payload={"output": "body"}))
    )


# -- execution-ledger run_id extension -----------------------------------------


def test_ledger_schema_accepts_run_id() -> None:
    entry = {
        "execution_id": "exec-" + "a" * 16,
        "chain_key": _chain_key(),
        "run_id": str(uuid4()),
        "slot_key": "audit.ledger.validate",
        "ordinal": 0,
        "mode": "isolated",
        "input_refs": [],
        "output_refs": ["audit-quality-candidates"],
        "output_contract": "contracts/jsonschema/audit-quality-candidates@1",
        "output_checksum": "b" * 64,
        "status": "succeeded",
        "policy_ref": "policy://local",
        "trace_id": str(uuid4()),
        "plugin_id": "audit.ledger-quality",
        "plugin_version": "1.0.0",
        "runtime_code_sha256": "c" * 64,
        "input_sha256": "d" * 64,
        "output_artifact_refs": [{"uri": "file:///tmp/out.json", "sha256": "b" * 64, "media_type": "application/json"}],
    }
    _validator("execution-ledger.schema.json").validate(entry)


def test_ledger_schema_accepts_null_run_id() -> None:
    entry = {
        "execution_id": "exec-" + "b" * 16,
        "chain_key": _chain_key(),
        "run_id": None,
        "slot_key": "quant.research-note.draft",
        "ordinal": 1,
        "mode": "simulated",
        "input_refs": ["file:///in.json"],
        "output_refs": ["research-note-draft"],
        "output_contract": "",
        "output_checksum": "e" * 64,
        "status": "succeeded",
        "policy_ref": "",
        "trace_id": str(uuid4()),
    }
    _validator("execution-ledger.schema.json").validate(entry)


def test_ledger_schema_rejects_non_uuid_run_id() -> None:
    entry = {
        "execution_id": "exec-" + "c" * 16,
        "chain_key": _chain_key(),
        "run_id": "not-a-uuid",
        "slot_key": "slot.one",
        "ordinal": 0,
        "mode": "simulated",
        "input_refs": [],
        "output_refs": [],
        "output_contract": "",
        "output_checksum": "f" * 64,
        "status": "pending",
        "policy_ref": "",
        "trace_id": str(uuid4()),
    }
    validator = _validator("execution-ledger.schema.json")
    assert any(
        error.validator in ("anyOf", "format") for error in validator.iter_errors(entry)
    )