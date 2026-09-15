"""Domain packs: the business knowledge that used to be hardcoded in `packages/`.

The audio of a plugin network — which folders belong to a domain, how plugins
group into business stages, which id segments are non-business layers, what the
closed-loop ring is and what the planner should be told — is domain knowledge,
not framework mechanics.  Keeping it in code meant the framework only ever
understood audit: `composer.discover_plugins` globbed ``audit-*``, and the 16
aiops/quant/knowledge plugins were fully implemented yet invisible to the
planner.

A pack is data (`contracts/domains/<domain>/pack.json`), validated against
`domain-pack.schema.json`, and **the audit pack reproduces the previous
hardcoded values verbatim** so loading it changes nothing.

Read-only: pure functions over JSON and the plugin directory.
"""
from __future__ import annotations

import fnmatch
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PACKS_ROOT = PROJECT_ROOT / "contracts" / "domains"
DEFAULT_DOMAIN = "audit"

LAYER_GOV = "gov"
LAYER_BASE = "base"
LAYER_BIZ = "biz"


def id_segment(plugin_id: str) -> str:
    """Second dotted segment of a plugin id.

    ``audit.field.workpaper-build`` → ``field``; the legacy two-segment form
    ``audit.evidence-lineage`` → ``evidence-lineage`` (use
    :func:`group_segment` for classification).
    """
    parts = plugin_id.split(".")
    return parts[1] if len(parts) >= 2 else parts[0]


def group_segment(plugin_id: str) -> str:
    """Coarse grouping key: the semantic segment with any ``-slug`` tail stripped.

    Handles both the three-segment form (``audit.field.x`` → ``field``) and
    legacy two-segment ids (``audit.evidence-lineage`` → ``evidence``).
    """
    return id_segment(plugin_id).split("-", 1)[0]


@dataclass(frozen=True, slots=True)
class Stage:
    key: str
    name_zh: str
    order: int
    color: str = ""
    segments: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DomainPack:
    """One domain's declarative description."""

    domain: str
    globs: tuple[str, ...]
    stages: tuple[Stage, ...]
    name_zh: str = ""
    governance_segment: str | None = None
    support_segment: str | None = None
    ungrouped_stage: str = "s0"
    trunk: tuple[tuple[str, str], ...] = ()
    control_segments: frozenset[str] = frozenset()
    stage_by_plugin: Mapping[str, str] = field(default_factory=dict)
    semantics_ref: str | None = None
    prompt: str | None = None
    path: Path | None = None

    # -- derived views, built once at load --------------------------------

    @property
    def stage_order(self) -> tuple[str, ...]:
        return tuple(s.key for s in sorted(self.stages, key=lambda s: s.order))

    @property
    def segment_to_stage(self) -> dict[str, str]:
        return {seg: s.key for s in self.stages for seg in s.segments}

    @property
    def stage_name(self) -> dict[str, str]:
        return {s.key: s.name_zh for s in self.stages}

    @property
    def stage_color(self) -> dict[str, str]:
        return {s.key: s.color for s in self.stages}

    # -- classification ----------------------------------------------------

    def layer_of(self, plugin_id: str) -> str:
        segment = group_segment(plugin_id)
        if self.governance_segment and segment == self.governance_segment:
            return LAYER_GOV
        if self.support_segment and segment == self.support_segment:
            return LAYER_BASE
        return LAYER_BIZ

    def stage_of(self, plugin_id: str) -> str | None:
        """Business stage for a plugin, or ``None`` for governance/support layers.

        Resolution order: an explicit ``stage_by_plugin`` entry first, then the
        id segment, then the pack's ``ungrouped_stage``.  The explicit map exists
        because segments collide across plugins — ``aiops.alert-triage`` and
        ``aiops.alert-correlation`` both segment to ``alert`` — and guessing
        between them would silently mis-stage half of that domain.
        """
        layer = self.layer_of(plugin_id)
        if layer != LAYER_BIZ:
            return None
        explicit = self.stage_by_plugin.get(plugin_id)
        if explicit:
            return explicit
        return self.segment_to_stage.get(group_segment(plugin_id), self.ungrouped_stage)

    def classify(self, plugin_id: str) -> tuple[str, str | None]:
        """``(layer, stage)`` — the shape callers already depend on."""
        layer = self.layer_of(plugin_id)
        return layer, (self.stage_of(plugin_id) if layer == LAYER_BIZ else None)

    def is_control(self, plugin_id: str) -> bool:
        """A full-process penetrating control plugin (cross-cutting, not a stage).

        Matched on the id **tail** because the point is the plugin's function,
        not the segment it happens to live in.  Falls back to the tail itself so
        a pack may name either the segment or the plugin tail.
        """
        tail = plugin_id.split(".")[-1]
        return tail in self.control_segments or group_segment(plugin_id) in self.control_segments


def _stage_from(raw: Mapping[str, Any]) -> Stage:
    return Stage(
        key=str(raw["key"]),
        name_zh=str(raw.get("name_zh") or raw["key"]),
        order=int(raw.get("order") or 0),
        color=str(raw.get("color") or ""),
        segments=tuple(str(s) for s in (raw.get("segments") or ())),
    )


def available_domains(root: Path | None = None) -> tuple[str, ...]:
    """Every domain that has a pack on disk."""
    base = Path(root) if root is not None else DEFAULT_PACKS_ROOT
    if not base.exists():
        return ()
    return tuple(sorted(p.parent.name for p in base.glob("*/pack.json")))


def load_pack(domain: str = DEFAULT_DOMAIN, root: Path | None = None) -> DomainPack:
    """Load one domain pack.

    A missing or unreadable pack is a hard error rather than an empty default:
    silently falling back would mean composing a plan with no stages and no
    discovery globs, which looks like "this domain has no plugins".
    """
    base = Path(root) if root is not None else DEFAULT_PACKS_ROOT
    path = base / domain / "pack.json"
    if not path.exists():
        raise FileNotFoundError(f"no domain pack for {domain!r} at {path}")
    raw: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    layers = raw.get("layers") or {}
    discovery = raw.get("discovery") or {}
    return DomainPack(
        domain=str(raw["domain"]),
        name_zh=str(raw.get("name_zh") or raw["domain"]),
        globs=tuple(str(g) for g in (discovery.get("globs") or ())),
        stages=tuple(_stage_from(s) for s in raw.get("stages") or ()),
        governance_segment=layers.get("governance"),
        support_segment=layers.get("support"),
        ungrouped_stage=str(raw.get("ungrouped_stage") or "s0"),
        trunk=tuple((str(a), str(b)) for a, b in (raw.get("trunk") or ())),
        control_segments=frozenset(str(s) for s in (raw.get("control_segments") or ())),
        stage_by_plugin={str(k): str(v) for k, v in (raw.get("stage_by_plugin") or {}).items()},
        semantics_ref=raw.get("semantics_ref"),
        prompt=raw.get("prompt"),
        path=path,
    )


def validate_pack(
    pack: DomainPack,
    *,
    plugin_ids: Iterable[str] = (),
    folders: Iterable[str] = (),
) -> list[str]:
    """Problems with a pack, optionally checked against a live plugin directory.

    Empty list means coherent.  Fail-closed checks: a duplicate stage key, a
    segment claimed by two stages, a trunk edge naming an unknown stage, or an
    explicit override pointing at a stage that does not exist would all classify
    plugins wrongly and quietly.

    ``plugin_ids`` and ``folders`` are checked separately because they are
    different namespaces: a pack globs *folders* (``audit-foundation-ocr-extract``)
    while stage overrides name *plugin ids* (``audit.foundation.ocr-extract``),
    and mixing them makes a correct pack look broken.
    """
    problems: list[str] = []

    if not pack.globs:
        problems.append(f"pack {pack.domain!r} declares no discovery globs")

    seen_keys: set[str] = set()
    for stage in pack.stages:
        if stage.key in seen_keys:
            problems.append(f"duplicate stage key {stage.key!r}")
        seen_keys.add(stage.key)

    owners: dict[str, str] = {}
    for stage in pack.stages:
        for segment in stage.segments:
            previous = owners.get(segment)
            if previous is not None:
                problems.append(f"segment {segment!r} claimed by both {previous!r} and {stage.key!r}")
            owners[segment] = stage.key

    # A trunk endpoint may be a business stage key or a **layer label**:
    # the audit ring closes s8 → gov → s1, and `gov` is the layer, not a stage.
    known: set[str] = {LAYER_GOV, LAYER_BASE} | set(pack.stage_order)
    for source, target in pack.trunk:
        for endpoint in (source, target):
            if endpoint not in known:
                problems.append(f"trunk edge {source!r}->{target!r} names unknown stage {endpoint!r}")

    stage_keys = set(pack.stage_order)
    for plugin_id, stage_key in sorted(pack.stage_by_plugin.items()):
        if stage_key not in stage_keys:
            problems.append(f"stage_by_plugin[{plugin_id!r}] names unknown stage {stage_key!r}")

    ids = list(plugin_ids)
    if ids:
        for plugin_id in sorted(pack.stage_by_plugin):
            if plugin_id not in ids:
                problems.append(f"stage_by_plugin names {plugin_id!r}, which is not in this domain")

    names = list(folders)
    if names and not any(fnmatch.fnmatch(name, pattern) for name in names for pattern in pack.globs):
        problems.append(f"pack {pack.domain!r} globs {list(pack.globs)} match none of {len(names)} folders")
    return problems
