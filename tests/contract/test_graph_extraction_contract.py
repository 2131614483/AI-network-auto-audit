from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]
SCHEMAS = ROOT / "contracts" / "jsonschema"


def _schema(name: str) -> dict[str, object]:
    return json.loads((SCHEMAS / name).read_text(encoding="utf-8"))


def test_graph_extraction_contract_rejects_arbitrary_relation_or_missing_space() -> None:
    schema = _schema("graph-extraction.schema.json")
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    valid = {
        "document_id": str(uuid4()),
        "source_uri": "drop://test/doc.md",
        "space_key": "audit-l1",
        "node_key": "company:ACME",
        "node_type": "organization",
        "label": "ACME",
        "property_source": {"label": "doc.md#L1"},
        "relation_type": "regulated_by",
        "target_node_key": "regulator:SEC",
        "confidence": 0.9,
    }
    validator.validate(valid)

    # arbitrary cross-domain relation -> rejected
    invalid_relation = {**valid, "relation_type": "arbitrary_cross_domain_link"}
    assert list(validator.iter_errors(invalid_relation))

    # unregistered / non-namespaced space key -> rejected
    invalid_space = {**valid, "space_key": "Arb Itrary Space!"}
    assert list(validator.iter_errors(invalid_space))

    # missing mandatory source fields -> rejected
    missing = {key: value for key, value in valid.items() if key not in {"node_key", "node_type", "label"}}
    assert list(validator.iter_errors(missing))