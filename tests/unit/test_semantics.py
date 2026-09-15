"""Unit tests for the reviewed business-semantics catalog.

The catalog is the only place a port's business identity is asserted, so these
tests care about two things above all: that an **empty/absent** catalog degrades
to today's exact-name behaviour (no invented semantics), and that the shipped
catalog's negative cases stay negative — the whole point is that
``ocr-image`` and ``masked-set`` must never be "the same object".
"""
from __future__ import annotations

import json
from pathlib import Path

from packages.ai_planner.composer import PluginSpec, PortSpec, discover_plugins
from packages.ai_planner.semantics import (
    AliasGroup,
    SemanticCatalog,
    load_semantics,
    validate_semantics,
)

SHIPPED = Path(__file__).resolve().parents[2] / "contracts" / "semantics" / "contract-aliases.json"


def _plugin(plugin_id: str, inputs=(), outputs=()) -> PluginSpec:
    def ports(names, direction):
        return tuple(PortSpec(port_id=n, schema_ref=f"{n}.schema.json", direction=direction) for n in names)
    return PluginSpec(
        plugin_id=plugin_id, capability=plugin_id, name=plugin_id, description="",
        lifecycle="verified", domains=("audit",),
        inputs=ports(inputs, "input"), outputs=ports(outputs, "output"),
    )


# --------------------------------------------------------------------------
# empty / absent catalog
# --------------------------------------------------------------------------

def test_missing_catalog_file_degrades_to_identity(tmp_path: Path) -> None:
    catalog = load_semantics(tmp_path / "nope.json")
    assert catalog.alias_groups == ()
    assert catalog.canonical("anything") == "anything"
    assert catalog.same_object("a", "b") is False
    assert catalog.same_object("a", "a") is True
    assert catalog.is_external("a") is False


def test_empty_catalog_wires_nothing_new() -> None:
    specs = {"a": _plugin("a", inputs=("x",), outputs=("x",))}
    assert validate_semantics(SemanticCatalog(), specs) == []


# --------------------------------------------------------------------------
# alias semantics
# --------------------------------------------------------------------------

def _catalog() -> SemanticCatalog:
    return SemanticCatalog(alias_groups=(
        AliasGroup(canonical="anomaly-set", members=("suspicion-set",), evidence="chain"),
    ))


def test_alias_group_canonicalises_both_directions() -> None:
    catalog = _catalog()
    assert catalog.canonical("suspicion-set") == "anomaly-set"
    assert catalog.canonical("anomaly-set") == "anomaly-set"
    assert catalog.same_object("anomaly-set", "suspicion-set") is True
    assert catalog.same_object("suspicion-set", "anomaly-set") is True
    assert set(catalog.siblings("suspicion-set")) == {"anomaly-set", "suspicion-set"}


def test_unrelated_contracts_are_not_the_same_object() -> None:
    catalog = _catalog()
    assert catalog.same_object("anomaly-set", "risk-matrix") is False
    assert catalog.same_object("suspicion-set", "risk-matrix") is False


# --------------------------------------------------------------------------
# validation is fail-closed
# --------------------------------------------------------------------------

def test_validation_rejects_an_unknown_contract_id() -> None:
    specs = {"a": _plugin("a", outputs=("x",))}
    catalog = SemanticCatalog(alias_groups=(AliasGroup("ghost", ("x",), "ev"),))
    assert any("unknown contract id" in p for p in validate_semantics(catalog, specs))


def test_validation_rejects_a_group_without_evidence() -> None:
    specs = {"a": _plugin("a", outputs=("x", "y"))}
    catalog = SemanticCatalog(alias_groups=(AliasGroup("x", ("y",), ""),))
    assert any("no evidence" in p for p in validate_semantics(catalog, specs))


def test_validation_rejects_a_contract_id_in_two_groups() -> None:
    specs = {"a": _plugin("a", outputs=("x", "y", "z"))}
    catalog = SemanticCatalog(alias_groups=(
        AliasGroup("x", ("z",), "ev"),
        AliasGroup("y", ("z",), "ev"),
    ))
    assert any("two alias groups" in p for p in validate_semantics(catalog, specs))


def test_validation_rejects_canonical_listed_as_member() -> None:
    specs = {"a": _plugin("a", outputs=("x",))}
    catalog = SemanticCatalog(alias_groups=(AliasGroup("x", ("x",), "ev"),))
    assert any("as a member" in p for p in validate_semantics(catalog, specs))


def test_validation_rejects_an_external_input_that_is_also_an_alias_member() -> None:
    specs = {"a": _plugin("a", inputs=("x",), outputs=("y",))}
    catalog = SemanticCatalog(
        alias_groups=(AliasGroup("y", ("x",), "ev"),),
        external_inputs=frozenset({"x"}),
    )
    assert any("contradictory" in p for p in validate_semantics(catalog, specs))


def test_validation_rejects_an_external_input_outside_the_directory() -> None:
    specs = {"a": _plugin("a", outputs=("x",))}
    catalog = SemanticCatalog(external_inputs=frozenset({"ghost"}))
    assert any("not a contract id" in p for p in validate_semantics(catalog, specs))


# --------------------------------------------------------------------------
# the shipped catalog
# --------------------------------------------------------------------------

def test_shipped_catalog_is_present_and_coherent() -> None:
    assert SHIPPED.exists(), "contracts/semantics/contract-aliases.json is the reviewed artifact"
    catalog = load_semantics()
    specs = discover_plugins()
    assert validate_semantics(catalog, specs) == []


def test_shipped_catalog_contains_the_six_author_evidenced_aliases() -> None:
    catalog = load_semantics()
    expected = {
        ("finance-anomaly-set", "suspicion-set"),
        ("issue-type-set", "issue-verify-input"),
        ("violation-clause", "issue-trace-input"),
        ("project-scheme", "scheme-request"),
        ("photo-evidence", "evidence-input"),
        ("report-frame", "report-draft-input"),
    }
    actual = {(g.canonical, m) for g in catalog.alias_groups for m in g.members}
    assert expected <= actual


def test_shipped_catalog_keeps_the_schema_ref_traps_apart() -> None:
    """Measured failure mode: aligning on the coarse ``schema_ref`` merges these,
    producing semantically wrong edges.  They must never share a business object."""
    catalog = load_semantics()
    for left, right in (
        ("ocr-image", "masked-set"),
        ("nlp-input", "encrypted-ref"),
        ("encrypt-request", "masked-set"),
        ("raw-finance-set", "encrypted-ref"),
        ("search-query", "encrypted-ref"),
    ):
        assert not catalog.same_object(left, right), (left, right)


def test_shipped_catalog_declares_external_inputs_and_reviews_the_rest() -> None:
    catalog = load_semantics()
    assert catalog.is_external("ocr-image")
    assert catalog.is_external("encrypt-request")
    assert not catalog.is_external("clean-finance-set")
    # The rule is that an undecided input is *listed*, never silently zeroed —
    # not that the shipped directory still has any.  Asserting a non-empty count
    # would break every time the remaining contracts get decided, which is the
    # opposite of the point.
    assert catalog.unreviewed_inputs is not None


def test_an_undecided_input_is_carried_through_rather_than_dropped(tmp_path: Path) -> None:
    """The rule, exercised on a catalog that still has an undecided entry."""
    path = tmp_path / "aliases.json"
    path.write_text(json.dumps({
        "schema_version": "1.0.0", "reviewed_by": "test",
        "alias_groups": [], "suggested_alias_groups": [],
        "external_inputs": [],
        "unreviewed_inputs": [{"contract_id": "mystery-input", "reason": "未判定"}],
    }), encoding="utf-8")
    catalog = load_semantics(path)
    assert catalog.unreviewed_inputs == frozenset({"mystery-input"})
    assert catalog.is_unreviewed("mystery-input")


def test_shipped_catalog_has_no_unconfirmed_group_among_the_active_ones() -> None:
    catalog = load_semantics()
    assert catalog.alias_groups, "the shipped catalog must carry the evidenced aliases"
    assert all(g.confirmed for g in catalog.alias_groups)


def test_shipped_catalog_file_shape_matches_the_loader() -> None:
    raw = json.loads(SHIPPED.read_text(encoding="utf-8"))
    assert raw["schema_version"]
    assert raw["reviewed_by"]
    for key in ("alias_groups", "suggested_alias_groups", "external_inputs", "unreviewed_inputs"):
        assert key in raw, key
