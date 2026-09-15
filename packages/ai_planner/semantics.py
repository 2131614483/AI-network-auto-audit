"""Business-semantics layer for the audit plugin network.

The wiring problem this solves: a plugin's port identity today is its
``contract_id`` string compared for **exact equality**.  That works when both
sides happen to use the same name, and silently fails when they don't — the
author's own hand-written main chain proves six such pairs exist
(``finance-anomaly-set`` → ``suspicion-set``, ``report-frame`` →
``report-draft-input``, …), and those six are exactly the edges the matcher
could not find on its own.

What this module deliberately is **not**:

* Not a ``schema_ref``-based matcher.  ``schema_ref`` is a coarse compatibility
  type — ``artifact-ref.schema.json`` is a catch-all produced by 5 plugins —
  and aligning on it produces edges like ``ocr-image ← masked-set`` and
  ``nlp-input ← encrypted-ref``.  It is a *compatibility filter*, never an
  identity.
* Not a name-similarity heuristic.  Measured against the shipped directory,
  "same schema_ref + forward stage" yields ~50 candidates of which roughly 10%
  are real; the rest connect a permission request to evidence photos.
* Not auto-derived.  Alias groups are a **reviewed domain artifact**
  (``contracts/semantics/contract-aliases.json``), each one carrying the
  evidence that justifies it.  Unreviewed candidates are recorded as
  ``suggested`` and create no edges.

Read-only: pure functions over a JSON file and the plugin directory.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .composer import PluginSpec

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SEMANTICS_PATH = PROJECT_ROOT / "contracts" / "semantics" / "contract-aliases.json"


@dataclass(frozen=True, slots=True)
class AliasGroup:
    """Contract ids that name the same business object under different strings."""

    canonical: str
    members: tuple[str, ...]
    evidence: str
    confirmed: bool = True

    @property
    def all_ids(self) -> tuple[str, ...]:
        return (self.canonical, *self.members)


@dataclass(frozen=True, slots=True)
class SemanticCatalog:
    """Reviewed business semantics for the port contracts."""

    alias_groups: tuple[AliasGroup, ...] = ()
    suggested_alias_groups: tuple[AliasGroup, ...] = ()
    external_inputs: frozenset[str] = frozenset()
    unreviewed_inputs: frozenset[str] = frozenset()

    def canonical(self, contract_id: str) -> str:
        """The business object a contract id names; itself when unaliased."""
        for group in self.alias_groups:
            if contract_id in group.all_ids:
                return group.canonical
        return contract_id

    def siblings(self, contract_id: str) -> tuple[str, ...]:
        """Every contract id naming the same business object (self included)."""
        for group in self.alias_groups:
            if contract_id in group.all_ids:
                return group.all_ids
        return (contract_id,)

    def same_object(self, left: str, right: str) -> bool:
        return self.canonical(left) == self.canonical(right)

    def is_external(self, contract_id: str) -> bool:
        """Declared by design to take external/human input, not a peer's output."""
        return contract_id in self.external_inputs

    def is_unreviewed(self, contract_id: str) -> bool:
        return contract_id in self.unreviewed_inputs

    def canonical_ids(self) -> frozenset[str]:
        return frozenset(g.canonical for g in self.alias_groups)


def load_semantics(path: Path | None = None) -> SemanticCatalog:
    """Load the reviewed catalog.  A missing file yields an empty catalog.

    Empty is the correct default: with no reviewed aliases every port is its own
    business object, so wiring degrades to today's exact-name behaviour rather
    than inventing semantics nobody approved.
    """
    source = Path(path) if path is not None else DEFAULT_SEMANTICS_PATH
    if not source.exists():
        return SemanticCatalog()
    raw: dict[str, Any] = json.loads(source.read_text(encoding="utf-8"))

    def _groups(key: str, confirmed: bool) -> tuple[AliasGroup, ...]:
        out: list[AliasGroup] = []
        for item in raw.get(key) or ():
            if not isinstance(item, Mapping):
                continue
            canonical = str(item.get("canonical") or "")
            members = tuple(str(m) for m in (item.get("members") or ()))
            if not canonical or not members:
                continue
            out.append(AliasGroup(
                canonical=canonical,
                members=members,
                evidence=str(item.get("evidence") or ""),
                confirmed=confirmed,
            ))
        return tuple(out)

    def _ids(key: str) -> frozenset[str]:
        return frozenset(
            str(item["contract_id"])
            for item in raw.get(key) or ()
            if isinstance(item, Mapping) and item.get("contract_id")
        )

    return SemanticCatalog(
        alias_groups=_groups("alias_groups", True),
        suggested_alias_groups=_groups("suggested_alias_groups", False),
        external_inputs=_ids("external_inputs"),
        unreviewed_inputs=_ids("unreviewed_inputs"),
    )


def validate_semantics(
    catalog: SemanticCatalog, specs: Mapping[str, PluginSpec],
) -> list[str]:
    """Return the problems with a catalog against the live plugin directory.

    Empty list means the catalog is coherent.  This is a fail-closed check: a
    canonical id that no plugin produces, an alias member that does not exist,
    or a contract id claimed by two groups would all silently mis-wire.
    """
    problems: list[str] = []
    known: set[str] = set()
    for spec in specs.values():
        for port in (*spec.inputs, *spec.outputs):
            known.add(port.port_id)

    seen: dict[str, str] = {}
    for group in catalog.alias_groups:
        if not group.evidence:
            problems.append(f"alias group {group.canonical!r} carries no evidence")
        for contract_id in group.all_ids:
            if contract_id not in known:
                problems.append(f"alias group {group.canonical!r} names unknown contract id {contract_id!r}")
            if contract_id in seen:
                problems.append(
                    f"contract id {contract_id!r} belongs to two alias groups "
                    f"({seen[contract_id]!r} and {group.canonical!r})"
                )
            else:
                seen[contract_id] = group.canonical
        if group.members and group.canonical in group.members:
            problems.append(f"alias group {group.canonical!r} lists its canonical id as a member")

    for contract_id in sorted(catalog.external_inputs):
        if contract_id not in known:
            problems.append(f"external input {contract_id!r} is not a contract id in the directory")
        if contract_id in seen:
            problems.append(f"external input {contract_id!r} is also an alias member — contradictory")

    for group in catalog.suggested_alias_groups:
        for contract_id in group.all_ids:
            if contract_id not in known:
                problems.append(f"suggested alias group names unknown contract id {contract_id!r}")
    for contract_id in sorted(catalog.unreviewed_inputs):
        if contract_id in seen:
            problems.append(f"unreviewed input {contract_id!r} is also an alias member")
    return problems
