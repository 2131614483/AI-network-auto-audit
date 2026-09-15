from __future__ import annotations

from uuid import uuid4

from packages.knowledge.graph_extraction import (
    REGISTERED_RELATIONS,
    GraphExtractionCandidate,
    extract_text,
    validate_candidate,
)


def _candidate(**overrides) -> GraphExtractionCandidate:
    defaults = {
        "document_id": uuid4(),
        "source_uri": "drop://test/doc.md",
        "space_key": "audit-l1",
        "node_key": "company:acme",
        "node_type": "company",
        "label": "ACME",
        "property_source": {"label": "doc.md#L1"},
        "relation_type": "regulated_by",
        "target_node_key": "regulator:sec",
        "confidence": 0.9,
    }
    defaults.update(overrides)
    return GraphExtractionCandidate(**defaults)


def test_candidate_contract_rejects_unregistered_relation() -> None:
    candidate = _candidate(relation_type="arbitrary_cross_domain_link", target_node_key=None)
    ok, violations = validate_candidate(candidate)
    assert ok is False
    assert any("unregistered relation" in v for v in violations)


def test_candidate_contract_rejects_non_namespaced_node_key() -> None:
    candidate = _candidate(node_key="NotNamespaced")
    ok, violations = validate_candidate(candidate)
    assert ok is False
    assert any("node_key must be namespaced" in v for v in violations)


def test_candidate_contract_rejects_relation_without_target() -> None:
    candidate = _candidate(relation_type="regulated_by", target_node_key=None)
    ok, violations = validate_candidate(candidate)
    assert ok is False
    assert any("relation requires target_node_key" in v for v in violations)


def test_candidate_contract_rejects_bad_confidence() -> None:
    candidate = _candidate(confidence=1.5)
    ok, violations = validate_candidate(candidate)
    assert ok is False
    assert any("confidence" in v for v in violations)


def test_valid_candidate_passes_contract() -> None:
    ok, violations = validate_candidate(_candidate())
    assert ok is True, violations
    assert not violations


def test_registered_relations_are_finite_vocabulary() -> None:
    assert REGISTERED_RELATIONS == {
        "regulated_by", "owns", "part_of", "depends_on", "references", "evidence_for",
    }


def test_extract_text_discovers_entities_and_relations() -> None:
    body = (
        "ACME is regulated by the SEC. "
        "Globex depends on ACME's platform. "
        "ACME is part of Wonka Holdings."
    )
    candidates = list(extract_text("ACME overview", body, document_id=uuid4(), source_uri="drop://test/a.md", space_key="audit-l1"))
    assert any(c.node_type == "company" and c.label.upper() == "ACME" for c in candidates)
    assert any(c.relation_type == "regulated_by" for c in candidates)


def test_extract_text_is_deterministic_and_dedupes() -> None:
    body = "ACME ACME ACME is regulated by SEC."
    document_id = uuid4()
    a = list(extract_text("t", body, document_id=document_id, source_uri="u", space_key="audit-l1"))
    b = list(extract_text("t", body, document_id=document_id, source_uri="u", space_key="audit-l1"))
    assert [c.node_key for c in a] == [c.node_key for c in b]