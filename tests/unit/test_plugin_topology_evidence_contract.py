"""Plugin Topology M9 contract-first tests: evidence anchor request, anchor
projection, chain proof and read-only export schemas.

Positive cases prove an anchor request (closed scope enum + idempotency key),
the anchor projection (row hash + predecessor pointer with sha256 shapes),
a verified proof (first_mismatch must be null when verified), and the export
(entries carry only id/timestamp/hash-level fields); negatives prove scope
enum closure, idempotency-key requirement, malformed hashes, zero seq, a
verified proof carrying first_mismatch, and export entries smuggling payload
bodies are all rejected.
"""

from __future__ import annotations

import json
from uuid import UUID, uuid4

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from packages.plugin_topology.contracts import (
    SCHEMA_DIR,
    EvidenceAnchorRequest,
    validate_evidence_anchor,
    validate_evidence_export,
    validate_evidence_proof,
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


def _uri_hash() -> str:
    return "a" * 64


def _anchor_request(**overrides: object) -> dict:
    payload: dict = {
        "scope": "full",
        "idempotency_key": "evidence-anchor-contract-1",
    }
    payload.update(overrides)
    return payload


def _anchor(**overrides: object) -> dict:
    payload: dict = {
        "anchor_id": str(uuid4()),
        "chain_key": f"evidence://{uuid4()}/full",
        "seq": 1,
        "source_table": "topology.execution_runs",
        "source_pk": str(uuid4()),
        "row_hash": _uri_hash(),
        "prev_hash": _uri_hash(),
        "group_id": str(uuid4()),
        "anchor_scope": "full",
        "trace_id": str(uuid4()),
        "created_at": "2026-09-07T09:00:00+00:00",
    }
    payload.update(overrides)
    return payload


def _proof(**overrides: object) -> dict:
    payload: dict = {
        "chain_key": f"evidence://{uuid4()}/full",
        "scope": "full",
        "total_anchors": 3,
        "tail_hash": _uri_hash(),
        "verified": True,
        "first_mismatch": None,
        "checked_at": "2026-09-07T09:00:00+00:00",
        "trace_id": str(uuid4()),
    }
    payload.update(overrides)
    return payload


def _export(**overrides: object) -> dict:
    payload: dict = {
        "chain_key": f"evidence://{uuid4()}/full",
        "scope": "execution",
        "entries": [
            {
                "anchor_id": str(uuid4()),
                "seq": 1,
                "source_table": "topology.execution_runs",
                "source_pk": str(uuid4()),
                "row_hash": _uri_hash(),
                "created_at": "2026-09-07T09:00:00+00:00",
            }
        ],
        "sha256": _uri_hash(),
        "proof_ref": {"tail_hash": _uri_hash(), "total_anchors": 1},
        "exported_at": "2026-09-07T09:00:00+00:00",
        "trace_id": str(uuid4()),
    }
    payload.update(overrides)
    return payload


# -- evidence-anchor-request ---------------------------------------------------


def test_anchor_request_accepts_valid_payload() -> None:
    _validator("topology-evidence-anchor-request.schema.json").validate(_anchor_request())


def test_anchor_request_accepts_all_scope_enum() -> None:
    validator = _validator("topology-evidence-anchor-request.schema.json")
    for scope in ("full", "topology", "chain", "execution", "verification", "remediation"):
        validator.validate(_anchor_request(scope=scope))


def test_anchor_request_rejects_unknown_scope() -> None:
    validator = _validator("topology-evidence-anchor-request.schema.json")
    assert any(
        error.validator == "enum"
        for error in validator.iter_errors(_anchor_request(scope="graph"))
    )


def test_anchor_request_rejects_missing_idempotency_key() -> None:
    validator = _validator("topology-evidence-anchor-request.schema.json")
    assert any(
        error.validator in ("required", "minLength")
        for error in validator.iter_errors(_anchor_request(idempotency_key=""))
    )


def test_anchor_request_parse_round_trips() -> None:
    request = EvidenceAnchorRequest.parse(_anchor_request())
    assert request.scope == "full"
    assert request.idempotency_key == "evidence-anchor-contract-1"


def test_anchor_request_rejects_extra_fields() -> None:
    with pytest.raises(ValueError, match="evidence anchor request"):
        EvidenceAnchorRequest.parse(_anchor_request(payload="anything"))


# -- evidence-anchor projection ------------------------------------------------


def test_anchor_projection_accepts_valid() -> None:
    _validator("topology-evidence-anchor.schema.json").validate(_anchor())


def test_anchor_projection_validate_passes() -> None:
    validate_evidence_anchor(_anchor())


def test_anchor_projection_rejects_bad_row_hash() -> None:
    validator = _validator("topology-evidence-anchor.schema.json")
    assert any(
        error.validator == "pattern"
        for error in validator.iter_errors(_anchor(row_hash="not-a-hash"))
    )


def test_anchor_projection_rejects_bad_prev_hash() -> None:
    validator = _validator("topology-evidence-anchor.schema.json")
    assert any(
        error.validator == "pattern"
        for error in validator.iter_errors(_anchor(prev_hash="not-a-hash"))
    )


def test_anchor_projection_rejects_zero_seq() -> None:
    validator = _validator("topology-evidence-anchor.schema.json")
    assert any(
        error.validator == "minimum"
        for error in validator.iter_errors(_anchor(seq=0))
    )


def test_anchor_projection_rejects_payload_body() -> None:
    validator = _validator("topology-evidence-anchor.schema.json")
    assert any(
        error.validator == "additionalProperties"
        for error in validator.iter_errors(_anchor(payload={"o": 1}))
    )


def test_anchor_projection_validate_rejects_invalid() -> None:
    with pytest.raises(ValueError, match="evidence anchor"):
        validate_evidence_anchor(_anchor(row_hash="nope"))


# -- evidence-proof ------------------------------------------------------------


def test_evidence_proof_accepts_verified() -> None:
    _validator("topology-evidence-proof.schema.json").validate(_proof())


def test_evidence_proof_accepts_mismatch() -> None:
    _validator("topology-evidence-proof.schema.json").validate(
        _proof(
            verified=False,
            first_mismatch={
                "seq": 2,
                "source_table": "topology.execution_runs",
                "source_pk": str(uuid4()),
            },
        )
    )


def test_evidence_proof_rejects_verified_with_mismatch() -> None:
    validator = _validator("topology-evidence-proof.schema.json")
    assert any(
        error.validator in ("const", "type", "anyOf")
        for error in validator.iter_errors(
            _proof(first_mismatch={"seq": 1, "source_table": "topology.runs", "source_pk": "x"})
        )
    )


def test_evidence_proof_rejects_bad_tail_hash() -> None:
    validator = _validator("topology-evidence-proof.schema.json")
    assert any(
        error.validator == "pattern"
        for error in validator.iter_errors(_proof(tail_hash="xyz"))
    )


def test_evidence_proof_validate_passes() -> None:
    validate_evidence_proof(_proof())
    validate_evidence_proof(
        _proof(
            verified=False,
            first_mismatch={"seq": 1, "source_table": "topology.runs", "source_pk": "x"},
        )
    )


def test_evidence_proof_validate_rejects_invalid() -> None:
    with pytest.raises(ValueError, match="evidence proof"):
        validate_evidence_proof(_proof(tail_hash="nope"))


# -- evidence-export -----------------------------------------------------------


def test_evidence_export_accepts_valid() -> None:
    _validator("topology-evidence-export.schema.json").validate(_export())


def test_evidence_export_rejects_payload_in_entry() -> None:
    entries = _export()["entries"]
    entries[0]["payload"] = {"secret": True}
    validator = _validator("topology-evidence-export.schema.json")
    assert any(
        error.validator == "additionalProperties"
        for error in validator.iter_errors(_export(entries=entries))
    )


def test_evidence_export_rejects_bad_sha256() -> None:
    validator = _validator("topology-evidence-export.schema.json")
    assert any(
        error.validator == "pattern"
        for error in validator.iter_errors(_export(sha256="short"))
    )


def test_evidence_export_validate_passes() -> None:
    validate_evidence_export(_export())


def test_evidence_export_validate_rejects_invalid() -> None:
    with pytest.raises(ValueError, match="evidence export"):
        validate_evidence_export(_export(sha256="bad"))