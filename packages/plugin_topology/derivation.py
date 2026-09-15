"""Design-time derivation lineage between plugins.

A plugin may declare that it is *derived from* another plugin version via
``provenance.derived_from``.  The base is pinned by ``descriptor_sha256`` — the
raw-byte hash of the base's protocol descriptor — so a derived plugin records
exactly which revision of its base it was taken from, not merely a version
string that could later mean different bytes (the failure mode
``plugin.runtime-binding.json`` already guards against for the plugin itself).

Declaring a base is a checkable claim:

* the base exists and is still the declared version *and* bytes;
* the derivation graph has no cycle;
* any port this plugin shares by name with its base must carry the **same**
  contract, unless the plugin explicitly lists that port in ``overrides``.

The last rule is the point of the whole module.  A derived plugin is usually
authored by editing a copy of its base, and the easiest mistake is to widen or
narrow a port without noticing — the plan still compiles, and the divergence
surfaces only when a run binds the wrong artifact.  Silent divergence is
rejected; a declared override is allowed.  Ports the derived plugin *adds*
(names its base does not have) are free: a derivation extends, it does not copy.

Scope note: like the composer, this reads each plugin's **first** capability.
``unified-plugin-protocol.schema.json`` permits several, but nothing in the
system produces or consumes more than one today, and guessing at a multi
capability rule would invent semantics the executor does not implement.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

INPUT = "input"
OUTPUT = "output"

PROJECT_ROOT = Path(__file__).resolve().parents[2]
#: The shipped built-in plugin directory — one folder per plugin.
BUILTIN_DIR = PROJECT_ROOT / "plugins" / "builtin"

#: ``port_id -> (direction, schema_ref)``.  ``port_id`` is the capability's
#: ``contract_id`` — the same handle the composer emits and the executor binds.
PortMap = Mapping[str, tuple[str, str]]


class DerivationError(ValueError):
    """A declared derivation is unsound; the message is stable and test-assertable."""


@dataclass(frozen=True, slots=True)
class Derivation:
    """The declared base of one plugin."""

    plugin_id: str
    version: str
    descriptor_sha256: str
    overrides: frozenset[str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "plugin_id": self.plugin_id,
            "version": self.version,
            "descriptor_sha256": self.descriptor_sha256,
            "overrides": sorted(self.overrides),
        }


@dataclass(frozen=True, slots=True)
class CatalogEntry:
    """One plugin's design-time identity: version, descriptor bytes, ports, base."""

    plugin_id: str
    version: str
    descriptor_sha256: str
    ports: PortMap
    derivation: Derivation | None = None


def ports_of(protocol: Mapping[str, Any]) -> dict[str, tuple[str, str]]:
    """Extract ``contract_id -> (direction, schema_ref)`` from the first capability."""
    capabilities = protocol.get("capabilities") or []
    if not capabilities:
        return {}
    capability = capabilities[0]
    ports: dict[str, tuple[str, str]] = {}
    for direction, key in ((INPUT, "inputs"), (OUTPUT, "outputs")):
        for item in capability.get(key) or []:
            contract_id = str(item.get("contract_id") or "")
            if contract_id:
                ports[contract_id] = (direction, str(item.get("schema_ref") or ""))
    return ports


def derivation_of(protocol: Mapping[str, Any], *, plugin_id: str) -> Derivation | None:
    """Read ``provenance.derived_from``; ``None`` when the plugin declares no base."""
    provenance = protocol.get("provenance") or {}
    declared = provenance.get("derived_from")
    if declared is None:
        return None
    if not isinstance(declared, dict):
        raise DerivationError(f"{plugin_id}: provenance.derived_from must be an object")
    overrides = declared.get("overrides") or []
    if not isinstance(overrides, list) or any(not isinstance(o, str) for o in overrides):
        raise DerivationError(f"{plugin_id}: provenance.derived_from.overrides must be a list of strings")
    return Derivation(
        plugin_id=str(declared.get("plugin_id") or ""),
        version=str(declared.get("version") or ""),
        descriptor_sha256=str(declared.get("descriptor_sha256") or ""),
        overrides=frozenset(overrides),
    )


def entry_from_protocol(
    plugin_id: str, protocol: Mapping[str, Any], *, descriptor_sha256: str
) -> CatalogEntry:
    return CatalogEntry(
        plugin_id=plugin_id,
        version=str(protocol.get("version") or ""),
        descriptor_sha256=descriptor_sha256,
        ports=ports_of(protocol),
        derivation=derivation_of(protocol, plugin_id=plugin_id),
    )


def load_directory(root: Path) -> dict[str, CatalogEntry]:
    """Read every ``<root>/*/plugin.protocol.json`` into a catalog keyed by id."""
    entries: dict[str, CatalogEntry] = {}
    for folder in sorted(root.iterdir()):
        protocol_path = folder / "plugin.protocol.json"
        if not folder.is_dir() or not protocol_path.exists():
            continue
        raw = protocol_path.read_bytes()
        protocol = json.loads(raw.decode("utf-8"))
        plugin_id = str(protocol.get("id") or folder.name)
        entries[plugin_id] = entry_from_protocol(
            plugin_id, protocol, descriptor_sha256=hashlib.sha256(raw).hexdigest()
        )
    return entries


def catalog_from_protocols(
    protocols: Mapping[str, tuple[Mapping[str, Any], str]],
) -> dict[str, CatalogEntry]:
    """Build a catalog from in-memory ``plugin_id -> (protocol, descriptor_sha256)``.

    Lets a caller validate a derivation without touching the filesystem — the
    tests use it, and a future "is this draft derived soundly?" check would too.
    """
    return {
        plugin_id: entry_from_protocol(plugin_id, protocol, descriptor_sha256=digest)
        for plugin_id, (protocol, digest) in protocols.items()
    }


def derivation_edges(entries: Mapping[str, CatalogEntry]) -> tuple[tuple[str, str], ...]:
    """``(derived_plugin_id, base_plugin_id)`` pairs, deterministically ordered."""
    edges = [
        (entry.plugin_id, entry.derivation.plugin_id)
        for entry in entries.values()
        if entry.derivation is not None
    ]
    return tuple(sorted(edges))


def validate_derivations(entries: Mapping[str, CatalogEntry]) -> None:
    """Raise :class:`DerivationError` on the first unsound derivation found.

    Checks, per declaring plugin: the base is present, still the declared
    version and bytes, the graph is acyclic, and every shared port matches
    unless explicitly overridden.

    Acyclicity is checked first: content checks are meaningless inside a cycle —
    "the base has changed" is a nonsense verdict when the base is the plugin
    itself, transitively.
    """
    _reject_cycles(entries)

    for entry in sorted(entries.values(), key=lambda e: e.plugin_id):
        derivation = entry.derivation
        if derivation is None:
            continue
        base = entries.get(derivation.plugin_id)
        if base is None:
            raise DerivationError(
                f"{entry.plugin_id}: base plugin {derivation.plugin_id!r} is not in the catalog"
            )
        if base.version != derivation.version:
            raise DerivationError(
                f"{entry.plugin_id}: base {base.plugin_id} is version {base.version!r}, "
                f"declared {derivation.version!r}"
            )
        if base.descriptor_sha256 != derivation.descriptor_sha256:
            raise DerivationError(
                f"{entry.plugin_id}: base {base.plugin_id}@{base.version} has changed since this "
                "plugin was derived from it (descriptor hash mismatch); re-derive and update "
                "provenance.derived_from.descriptor_sha256"
            )
        _reject_undeclared_divergence(entry, base, derivation)


def _reject_undeclared_divergence(entry: CatalogEntry, base: CatalogEntry, derivation: Derivation) -> None:
    shared = sorted(set(entry.ports) & set(base.ports))
    for port_id in shared:
        ours, theirs = entry.ports[port_id], base.ports[port_id]
        declared = port_id in derivation.overrides
        if ours == theirs:
            if declared:
                raise DerivationError(
                    f"{entry.plugin_id}: port {port_id!r} is listed in overrides but matches "
                    f"its base {base.plugin_id} exactly; drop it from overrides"
                )
            continue
        if not declared:
            raise DerivationError(
                f"{entry.plugin_id}: port {port_id!r} diverges from base {base.plugin_id} "
                f"({theirs[0]}/{theirs[1]} -> {ours[0]}/{ours[1]}) but is not listed in "
                "provenance.derived_from.overrides"
            )
    unknown = sorted(derivation.overrides - set(base.ports))
    if unknown:
        raise DerivationError(
            f"{entry.plugin_id}: overrides name port(s) absent from base {base.plugin_id}: {unknown}"
        )
    missing = sorted(derivation.overrides - set(entry.ports))
    if missing:
        raise DerivationError(
            f"{entry.plugin_id}: overrides name port(s) this plugin does not declare: {missing}"
        )


def _reject_cycles(entries: Mapping[str, CatalogEntry]) -> None:
    """Kahn reachability: a cycle would make the derivation tree a lie."""
    resolved: set[str] = set()
    for start in sorted(entries):
        seen: list[str] = []
        current: str | None = start
        while current is not None:
            if current in seen:
                raise DerivationError(
                    "derivation cycle: " + " -> ".join([*seen, current])
                )
            seen.append(current)
            if current in resolved:
                break
            entry = entries.get(current)
            if entry is None or entry.derivation is None:
                break
            current = entry.derivation.plugin_id
        resolved.update(seen)
