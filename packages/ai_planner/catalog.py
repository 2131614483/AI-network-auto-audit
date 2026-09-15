"""CW5 capability recall: the deterministic whitelist of what the AI may use.

The recall list is the *only* source of acceptable capabilities, ports and
contracts.  A draft referencing anything outside it is rejected with
``capability_unavailable`` / ``data_boundary_denied`` before compilation — the
model can never widen its own permission by inventing a plugin name.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Mapping

if TYPE_CHECKING:  # `composer` imports nothing from here at runtime
    from .composer import PluginSpec, schema_sha256
else:
    from .composer import schema_sha256
from uuid import UUID

import psycopg2


@dataclass(frozen=True, slots=True)
class CapabilityEntry:
    capability: str
    plugin_id: str
    inputs: tuple[str, ...] = ()
    outputs: tuple[str, ...] = ()


# Verified runtime plugins (the CW3 execution engine really runs these through
# its isolated runtime).  They are declared in the plugin manifests under
# tests/contract/test_quant_experiment_evaluator_contract.py and are not part
# of the DB blueprint directory — recall must include them so a draft can be
# compiled AND executed, not just look plausible.
RUNTIME_CAPABILITIES: dict[str, CapabilityEntry] = {
    "audit.ledger.validate": CapabilityEntry(
        capability="audit.ledger.validate",
        plugin_id="audit.ledger-quality",
        inputs=("ledger",),
        outputs=("candidates",),
    ),
    "quant.experiment.evaluate": CapabilityEntry(
        capability="quant.experiment.evaluate",
        plugin_id="quant.experiment-evaluator",
        inputs=("experiment",),
        outputs=("evaluation",),
    ),
}


# Authoritative port contracts (方案 5.1).  The recall list is the *only*
# source of acceptable capabilities, ports and contracts: the model must copy
# these values verbatim into its draft, so a source port and a target port
# carrying the same ``schema_ref`` compile into a data edge without the model
# inventing a schema.  ``port_id -> contract fields`` (port_id/direction are
# positional and excluded here).
_PORT_DEFAULTS: dict[str, object] = {
    "schema_version": "1.0.0",
    "media_type": "application/json",
    "required": True,
    "cardinality": "one",
    "classification": "internal",
    "transport": "artifact_ref",
}

#: ``port_id -> schema_ref``, and nothing else written by hand.  The hash is
#: derived from the schema file at import, because a hand-written digest is a
#: digest that can be wrong: the previous value was a 64-character placeholder
#: that made two divergent contracts compare equal through every gate, and the
#: value shipped in its place ("pending") would have been just as false to any
#: reader of this registry.
_PORT_SCHEMA_REFS: dict[str, str] = {
    "ledger": "ledger-artifact-ref",
    "candidates": "audit-quality-candidates",
    "audit-quality-candidates": "audit-quality-candidates",
    "finding-draft": "finding-draft",
    "experiment": "backtest-report",
    "evaluation": "experiment-evaluation",
    "research-note-draft": "research-note-draft",
}
PORT_CONTRACTS: dict[str, dict[str, object]] = {
    port_id: {**_PORT_DEFAULTS, "schema_ref": ref, "schema_sha256": schema_sha256(ref)}
    for port_id, ref in _PORT_SCHEMA_REFS.items()
}
# Fields of a port contract the model must reproduce exactly.
_CONTRACT_FIELDS = (
    "schema_ref", "schema_version", "schema_sha256", "media_type",
    "required", "cardinality", "classification", "transport",
)


def _contract_json(port_id: str) -> str:
    return json.dumps(
        {field: PORT_CONTRACTS[port_id][field] for field in _CONTRACT_FIELDS},
        ensure_ascii=False,
        separators=(",", ":"),
    )


# Blueprint key -> runtime plugin id: the topology blueprint key is the
# governance identity (slot in the catalog graph); the runtime id is what the
# isolated executor actually runs.  A draft carrying a blueprint key as
# plugin_id compiles but can never execute, so recall must expose the runtime id.
PLUGIN_ID_BY_BLUEPRINT_KEY: dict[str, str] = {
    "ledger-quality-slot": "audit.ledger-quality",
    "finding-draft-slot": "audit.finding-draft",
    "research-note-slot": "quant.research-note-draft",
}


def capability_catalog_from_db(
    database_url: str, tenant_id: UUID, *, include_runtime: bool = True,
) -> dict[str, CapabilityEntry]:
    """Build the recall whitelist from ``topology.plugin_blueprints`` plus the
    verified runtime plugins (DB entries win on key conflicts)."""
    entries: dict[str, CapabilityEntry] = {}
    with psycopg2.connect(database_url) as connection, connection.cursor() as cur:
        cur.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant_id),))
        cur.fetchone()
        cur.execute(
            """SELECT key, capability_contract FROM topology.plugin_blueprints
               WHERE status IN ('active','planned') ORDER BY key""",
        )
        for key, contract_raw in cur.fetchall():
            contract: dict[str, Any] = {}
            if isinstance(contract_raw, dict):
                contract = contract_raw
            elif isinstance(contract_raw, str):
                try:
                    parsed = json.loads(contract_raw)
                    if isinstance(parsed, dict):
                        contract = parsed
                except json.JSONDecodeError:
                    continue
            capability = str(contract.get("capability") or "")
            if not capability:
                continue
            entries[capability] = CapabilityEntry(
                capability=capability,
                # Execute against the verified runtime plugin id, not the
                # blueprint key; blueprint keys are topology identities and the
                # isolated executor rejects them.
                plugin_id=PLUGIN_ID_BY_BLUEPRINT_KEY.get(str(key), str(key)),
                inputs=tuple(str(item) for item in contract.get("inputs") or ()),
                outputs=tuple(str(item) for item in contract.get("outputs") or ()),
            )
    if include_runtime:
        for capability, entry in RUNTIME_CAPABILITIES.items():
            entries.setdefault(capability, entry)
    return entries


def recall_snapshot(catalog: dict[str, CapabilityEntry]) -> str:
    """Compact deterministic prompt block: capability -> ports/contracts.

    Ports carry their full registered contract so the model reproduces the
    exact schema values (never invents a schema_ref).  The header states the
    verbatim-copy rule; a draft that deviates is rejected by ``validate_draft``.
    """
    lines = ["能力清单（只能使用这些 capability；端口的契约字段必须逐字段原样复制，不得省略、修改或新增）："]
    for capability in sorted(catalog):
        entry = catalog[capability]
        lines.append(
            f"- {capability} (plugin={entry.plugin_id}, inputs={','.join(entry.inputs) or 'none'}, "
            f"outputs={','.join(entry.outputs) or 'none'})"
        )
        for direction, names in (("input", entry.inputs), ("output", entry.outputs)):
            for port_id in names:
                if port_id not in PORT_CONTRACTS:
                    continue
                lines.append(
                    f"  - {direction} port \"{port_id}\" contract: {_contract_json(port_id)}"
                )
    return "\n".join(lines) if lines else "(no capabilities recalled)"


# --------------------------------------------------------------------------
# deriving the port-contract directory from the plugin directory
# --------------------------------------------------------------------------

#: The contract fields a port must reproduce verbatim.  Mirrors
#: ``draft._CONTRACT_FIELDS``; ``direction`` is positional and excluded.
PORT_CONTRACT_FIELDS: tuple[str, ...] = (
    "schema_ref", "schema_version", "schema_sha256", "media_type",
    "required", "cardinality", "classification", "transport",
)


def build_port_contracts(
    specs: Mapping[str, PluginSpec],
) -> tuple[dict[str, dict[str, object]], dict[str, list[str]]]:
    """``port_id -> contract`` derived from the plugin directory.

    The hand-written ``PORT_CONTRACTS`` above covers seven ports — the legacy
    ledger/finding/research-note flow.  Anything planning the real plugin network
    needs the *whole* directory: ``validate_draft`` rejects a port that is not in
    the registry it was given, so with the seven-entry one every AI draft of an
    audit or aiops flow fails with ``contract_mismatch`` before the compiler is
    even reached.

    Returns the registry plus ``port_id -> [schema_refs]`` for any port two
    plugins disagree about.  A conflict is reported, never silently resolved:
    picking one would make the model's draft valid for a contract it did not see.
    """
    contracts: dict[str, dict[str, object]] = {}
    seen: dict[str, set[str]] = {}
    for spec in specs.values():
        for port in (*spec.inputs, *spec.outputs):
            seen.setdefault(port.port_id, set()).add(port.schema_ref)
            # `required` / `cardinality` are deliberately not pinned here: one
            # port id may be produced as `one` and consumed as `many` (a merge
            # input), so they follow the direction.  The compiler's fan-in and
            # required-input gates are their authority.
            contracts.setdefault(port.port_id, {
                "schema_ref": port.schema_ref,
                "schema_version": "1.0.0",
                "schema_sha256": schema_sha256(port.schema_ref),
                "media_type": "application/json",
                "classification": "internal",
                "transport": "artifact_ref",
            })
    conflicts = {pid: sorted(refs) for pid, refs in seen.items() if len(refs) > 1}
    return contracts, conflicts
