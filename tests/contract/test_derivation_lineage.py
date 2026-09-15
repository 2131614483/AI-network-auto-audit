"""Derivation lineage: a derived plugin cannot silently diverge from its base.

``provenance.derived_from`` records which plugin version — and which descriptor
*bytes* — a plugin was derived from, and which of the base's ports it
deliberately overrides.  The rules under test:

* the base must exist, still be the declared version, and still hash to the
  declared ``descriptor_sha256`` (otherwise the base moved under the derived
  plugin without anyone noticing);
* the derivation graph must be acyclic;
* a port shared by name with the base must be identical, unless the plugin
  lists it in ``overrides`` — silent contract divergence is the failure this
  whole mechanism exists to prevent;
* ports the derived plugin *adds* are free.

The shipped ``plugins/builtin`` directory declares no derivation yet, so the
first test passes vacuously today; it is the gate that will catch the first real
one.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from packages.plugin_topology.derivation import (
    BUILTIN_DIR,
    DerivationError,
    catalog_from_protocols,
    derivation_edges,
    validate_derivations,
)

SCHEMA = json.loads(
    (Path(__file__).resolve().parents[2] / "contracts" / "jsonschema"
     / "unified-plugin-protocol.schema.json").read_text(encoding="utf-8")
)


def _port(contract_id: str, schema_ref: str) -> dict[str, str]:
    return {"contract_id": contract_id, "schema_ref": schema_ref}


def _protocol(
    plugin_id: str,
    version: str = "0.1.0",
    *,
    inputs: tuple[tuple[str, str], ...] = (),
    outputs: tuple[tuple[str, str], ...] = (),
    derived_from: dict[str, Any] | None = None,
) -> dict[str, Any]:
    provenance: dict[str, Any] = {"source_refs": ["docs/test.md"]}
    if derived_from is not None:
        provenance["derived_from"] = derived_from
    return {
        "id": plugin_id,
        "version": version,
        "capabilities": [
            {
                "id": plugin_id,
                "inputs": [_port(name, ref) for name, ref in inputs],
                "outputs": [_port(name, ref) for name, ref in outputs],
            }
        ],
        "provenance": provenance,
    }


def _catalog(*protocols: dict[str, Any]) -> dict:
    """Hash each fixture protocol the way the real loader does."""
    import hashlib

    return catalog_from_protocols(
        {str(p["id"]): (p, hashlib.sha256(json.dumps(p, sort_keys=True).encode()).hexdigest())
         for p in protocols}
    )


def _base_hash(protocol: dict[str, Any]) -> str:
    import hashlib

    return hashlib.sha256(json.dumps(protocol, sort_keys=True).encode()).hexdigest()


# -- the shipped directory ------------------------------------------------------


def test_shipped_directory_has_sound_derivations() -> None:
    from packages.plugin_topology.derivation import load_directory

    validate_derivations(load_directory(BUILTIN_DIR))


# -- accepted ------------------------------------------------------------------


def test_a_derivation_that_only_inherits_is_valid() -> None:
    base = _protocol("base.plugin", inputs=(("ledger", "ledger-ref"),))
    derived = _protocol(
        "derived.plugin",
        inputs=(("ledger", "ledger-ref"),),
        derived_from={
            "plugin_id": "base.plugin", "version": "0.1.0",
            "descriptor_sha256": _base_hash(base),
        },
    )
    validate_derivations(_catalog(base, derived))
    assert derivation_edges(_catalog(base, derived)) == (("derived.plugin", "base.plugin"),)


def test_a_declared_override_is_valid() -> None:
    base = _protocol("base.plugin", outputs=(("candidates", "audit-quality-candidates"),))
    derived = _protocol(
        "derived.plugin",
        outputs=(("candidates", "strict-candidates"),),
        derived_from={
            "plugin_id": "base.plugin", "version": "0.1.0",
            "descriptor_sha256": _base_hash(base),
            "overrides": ["candidates"],
        },
    )
    validate_derivations(_catalog(base, derived))


def test_ports_the_derived_plugin_adds_are_free() -> None:
    base = _protocol("base.plugin", inputs=(("ledger", "ledger-ref"),))
    derived = _protocol(
        "derived.plugin",
        inputs=(("ledger", "ledger-ref"), ("threshold", "threshold-ref")),
        derived_from={
            "plugin_id": "base.plugin", "version": "0.1.0",
            "descriptor_sha256": _base_hash(base),
        },
    )
    validate_derivations(_catalog(base, derived))


# -- rejected ------------------------------------------------------------------


def test_silent_schema_divergence_is_rejected() -> None:
    base = _protocol("base.plugin", inputs=(("ledger", "ledger-ref"),))
    derived = _protocol(
        "derived.plugin",
        inputs=(("ledger", "ledger-ref-v2"),),
        derived_from={
            "plugin_id": "base.plugin", "version": "0.1.0",
            "descriptor_sha256": _base_hash(base),
        },
    )
    with pytest.raises(DerivationError, match="diverges from base"):
        validate_derivations(_catalog(base, derived))


def test_declaring_a_port_as_overridden_when_it_is_identical_is_rejected() -> None:
    base = _protocol("base.plugin", inputs=(("ledger", "ledger-ref"),))
    derived = _protocol(
        "derived.plugin",
        inputs=(("ledger", "ledger-ref"),),
        derived_from={
            "plugin_id": "base.plugin", "version": "0.1.0",
            "descriptor_sha256": _base_hash(base),
            "overrides": ["ledger"],
        },
    )
    with pytest.raises(DerivationError, match="matches.*exactly"):
        validate_derivations(_catalog(base, derived))


def test_a_base_that_moved_is_rejected() -> None:
    """The declared descriptor hash no longer matches the base's bytes."""
    base = _protocol("base.plugin", inputs=(("ledger", "ledger-ref"),))
    derived = _protocol(
        "derived.plugin",
        inputs=(("ledger", "ledger-ref"),),
        derived_from={
            "plugin_id": "base.plugin", "version": "0.1.0",
            "descriptor_sha256": "c" * 64,
        },
    )
    with pytest.raises(DerivationError, match="has changed since this plugin was derived"):
        validate_derivations(_catalog(base, derived))


def test_a_missing_base_is_rejected() -> None:
    derived = _protocol(
        "derived.plugin",
        derived_from={
            "plugin_id": "ghost.plugin", "version": "0.1.0",
            "descriptor_sha256": "c" * 64,
        },
    )
    with pytest.raises(DerivationError, match="not in the catalog"):
        validate_derivations(_catalog(derived))


def test_a_version_mismatch_is_rejected() -> None:
    base = _protocol("base.plugin", version="0.2.0")
    derived = _protocol(
        "derived.plugin",
        derived_from={
            "plugin_id": "base.plugin", "version": "0.1.0",
            "descriptor_sha256": _base_hash(base),
        },
    )
    with pytest.raises(DerivationError, match="is version"):
        validate_derivations(_catalog(base, derived))


def test_an_override_naming_an_unknown_port_is_rejected() -> None:
    base = _protocol("base.plugin", inputs=(("ledger", "ledger-ref"),))
    derived = _protocol(
        "derived.plugin",
        inputs=(("ledger", "ledger-ref"),),
        derived_from={
            "plugin_id": "base.plugin", "version": "0.1.0",
            "descriptor_sha256": _base_hash(base),
            "overrides": ["nonexistent"],
        },
    )
    with pytest.raises(DerivationError, match="absent from base"):
        validate_derivations(_catalog(base, derived))


def test_a_derivation_cycle_is_rejected() -> None:
    a = _protocol("a.plugin")
    b = _protocol("b.plugin")
    a["provenance"]["derived_from"] = {
        "plugin_id": "b.plugin", "version": "0.1.0",
        "descriptor_sha256": _base_hash(b),
    }
    b["provenance"]["derived_from"] = {
        "plugin_id": "a.plugin", "version": "0.1.0",
        "descriptor_sha256": _base_hash(a),
    }
    with pytest.raises(DerivationError, match="cycle"):
        validate_derivations(_catalog(a, b))


def test_self_derivation_is_rejected() -> None:
    a = _protocol("a.plugin")
    a["provenance"]["derived_from"] = {
        "plugin_id": "a.plugin", "version": "0.1.0",
        "descriptor_sha256": _base_hash(a),
    }
    # A self-derivation is a cycle of length one.
    with pytest.raises(DerivationError, match="cycle"):
        validate_derivations(_catalog(a))


# -- the schema accepts the declaration shape ----------------------------------

_REAL_PROTOCOL = json.loads(
    (Path(__file__).resolve().parents[2] / "plugins" / "builtin"
     / "audit_ledger_quality" / "plugin.protocol.json").read_text(encoding="utf-8")
)


def _schema_errors(derived_from: dict[str, Any] | None) -> list:
    """A real, schema-valid protocol with its provenance swapped for the fixture.

    Using a shipped protocol rather than a hand-built one keeps the assertion
    about ``derived_from`` — a hand-built minimal object fails on the dozen
    other required fields and proves nothing.
    """
    protocol = json.loads(json.dumps(_REAL_PROTOCOL))
    provenance: dict[str, Any] = {"source_refs": ["docs/x.md"]}
    if derived_from is not None:
        provenance["derived_from"] = derived_from
    protocol["provenance"] = provenance
    return sorted(Draft202012Validator(SCHEMA).iter_errors(protocol), key=lambda e: list(e.path))


def test_schema_accepts_a_pinned_derivation() -> None:
    errors = _schema_errors({
        "plugin_id": "base.plugin", "version": "0.1.0",
        "descriptor_sha256": "a" * 64, "overrides": ["p"],
    })
    assert not errors, errors[0].message


def test_schema_accepts_a_protocol_with_no_derivation() -> None:
    assert not _schema_errors(None)


def test_schema_rejects_a_non_hash_descriptor() -> None:
    errors = _schema_errors({
        "plugin_id": "base.plugin", "version": "0.1.0", "descriptor_sha256": "not-a-hash",
    })
    assert errors, "a non-sha256 descriptor must not validate"


def test_schema_rejects_an_unknown_field_inside_the_derivation() -> None:
    errors = _schema_errors({
        "plugin_id": "base.plugin", "version": "0.1.0",
        "descriptor_sha256": "a" * 64, "inherits": ["p"],
    })
    assert errors, "unknown keys inside derived_from must be rejected, never ignored"
