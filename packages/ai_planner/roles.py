"""Node roles: what a plugin *is* in the network, not what it transforms.

The network is not a set of interchangeable data-transform nodes.  The user's
framing, which the codebase's own data corroborates:

* some plugins are **shells** — they take external/raw input and normalise,
  clean, map or validate it before the business chain can use it;
* some are **reusable services** — called from many places rather than sitting
  at one point on the chain.  The 34 ``invokes`` declarations in the shipped
  directory are exactly this: 34 callers, 15 callees, **all 15 in the
  ``foundation`` layer**, and no foundation→foundation edges.  That is a service
  call graph, not a dataflow;
* some are **schedulers** (workflow engine, dispatch, scheduling) that sequence
  work rather than produce an object;
* some are **judgment** nodes that turn an object into a verdict;
* some are **terminal products** that legitimately have no consumer.

Why the role matters for wiring: it decides **which class of edge the node
participates in**, and therefore whether a missing upstream is a defect or a
declared design choice.  Treating a permission-control service as "an island
missing input" and force-wiring it is how you get ``ocr-image ← masked-set``.

Resolution order (highest wins), mirroring how the design treats semantics:
``declared`` (plugin.protocol.json, author-owned) > ``pack_roles`` (domain pack
override) > :func:`derive_role` (deterministic heuristics).

Roles are never written into the plan's node JSON — they are a composition-time
concept, so ``execution_hash`` is unaffected.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping

from .composer import PluginSpec
from .semantics import SemanticCatalog

# --- the vocabulary -------------------------------------------------------

ROLE_INGRESS = "ingress"
ROLE_ADAPTER = "adapter"
ROLE_VALIDATOR = "validator"
ROLE_SERVICE = "service"
ROLE_SCHEDULER = "scheduler"
ROLE_DECISION = "decision"
ROLE_TRANSFORM = "transform"
ROLE_SINK = "sink"
ROLE_REVIEW = "review"

ROLES: tuple[str, ...] = (
    ROLE_INGRESS, ROLE_ADAPTER, ROLE_VALIDATOR, ROLE_SERVICE,
    ROLE_SCHEDULER, ROLE_DECISION, ROLE_TRANSFORM, ROLE_SINK, ROLE_REVIEW,
)

ROLE_NAMES_ZH: dict[str, str] = {
    ROLE_INGRESS: "数据接入",
    ROLE_ADAPTER: "外壳/归一",
    ROLE_VALIDATOR: "校验",
    ROLE_SERVICE: "可复用服务",
    ROLE_SCHEDULER: "调度",
    ROLE_DECISION: "判断",
    ROLE_TRANSFORM: "变换/分析",
    ROLE_SINK: "终端产出",
    ROLE_REVIEW: "待人工判定",
}

#: Where a curated role assignment lives.  Written by the AI-assisted curator
#: (``role_curator.py``) and read here, so the artifact takes effect everywhere
#: without each caller threading it through.
DEFAULT_ROLE_MAP_PATH = Path(__file__).resolve().parents[2] / "contracts" / "semantics" / "role-map.json"


def load_role_map(path: Path | None = None) -> dict[str, str]:
    """``plugin_id -> role`` from the curated artifact; ``{}`` when absent.

    Only *accepted* entries are honoured, so a rejected proposal can be recorded
    for audit without ever changing how the network is classified.  A missing
    file is the correct default: the heuristics decide, exactly as before.
    """
    source = Path(path) if path is not None else DEFAULT_ROLE_MAP_PATH
    if not source.exists():
        return {}
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    entries = raw.get("roles") if isinstance(raw, Mapping) else None
    if not isinstance(entries, Mapping):
        return {}
    out: dict[str, str] = {}
    for plugin_id, entry in entries.items():
        if not isinstance(entry, Mapping):
            continue
        role = str(entry.get("role") or "")
        if role in ROLES and role != ROLE_REVIEW and entry.get("accepted", True):
            out[str(plugin_id)] = role
    return out

#: Roles whose *input* is external or caller-supplied by design.  For these a
#: missing upstream is a declared behaviour, not a gap.
ROLES_EXTERNAL_BY_DESIGN: frozenset[str] = frozenset({
    ROLE_INGRESS, ROLE_ADAPTER, ROLE_VALIDATOR, ROLE_SERVICE, ROLE_SCHEDULER,
})

#: The edge class a role participates in.  Anything absent uses ``data``.
ROLE_EDGE_CLASS: dict[str, str] = {
    ROLE_SERVICE: "call",
    ROLE_SCHEDULER: "control",
}

#: The only role a ``call`` edge may target / a ``control`` edge may target.
CALL_TARGET_ROLE = ROLE_SERVICE
CONTROL_TARGET_ROLE = ROLE_SCHEDULER

# --- derivation -----------------------------------------------------------

_SCHEDULER_MARKERS: tuple[str, ...] = (
    "workflow-engine", "workflow_engine", "-dispatch", "-schedule", "staff-schedule",
    "plan-version-control", "investigation-plan", "team-forming", "remedy-plan-review",
    "report-multi-review", "extension-approve", "confirm-letter", "cross-dept-inquiry",
    "auditee-feedback", "remedy-close", "material-submit", "remedy-dispatch",
)

_DECISION_MARKERS: tuple[str, ...] = (
    "-judge", "-grade", "-assign", "-rank", "-score", "-review", "-verify",
    "-detect", "-locate", "-match", "-evaluate", "risk-matrix-build", "risk-heatmap-draw",
)

_INGRESS_MARKERS: tuple[str, ...] = (
    "ocr-extract", "multi-source-collect", "evidence-photo", "interview-record",
    "site-checkin-track", "meeting-minutes", "audit-log", "-input", "document-ingestion",
)

_ADAPTER_MARKERS: tuple[str, ...] = (
    "biz-standardize", "finance-clean", "master-mapping", "data-mask", "data-encrypt",
    "metadata-manage", "nlp-process", "tag-manage", "voucher-drilldown",
    "remedy-publish", "notice-mask-publish", "workpaper-encrypt-store",
)

_VALIDATOR_MARKERS: tuple[str, ...] = (
    "quality-check", "evidence-verify", "report-data-check", "workpaper-reconcile",
    "material-precheck", "snapshot-guard", "journal-anomaly", "ledger-quality",
)


@dataclass(frozen=True, slots=True)
class RoleAssignment:
    role: str
    source: str        # "declared" | "pack" | "derived"
    reason: str
    facets: tuple[str, ...] = ()
    """Secondary aspects the primary role cannot express.

    ``ocr-extract`` is a ``service`` (something ``invokes`` it) *and* an
    ``ingress`` (its only input is an external image).  The primary role decides
    which edge class it participates in; the facet is kept so island
    classification can say "this is a service that also ingests", instead of
    silently dropping half the truth.
    """


def derive_facets(
    plugin_id: str, spec: PluginSpec, signals: RoleSignals, primary: str,
) -> tuple[str, ...]:
    """Secondary role aspects, deterministic and only where evidence is structural."""
    facets: list[str] = []
    tail = plugin_id.split(".")[-1]

    def hits(markers: Iterable[str]) -> bool:
        return any(m == tail or m in plugin_id or m in tail for m in markers)

    input_ids = {port.port_id for port in spec.inputs}
    # The ingress facet is only informative when the primary role *hides* it:
    # a service that also ingests, or a scheduler that waits on external input.
    # Attaching it to every plugin whose input is external produced 41 facets —
    # a facet on 40% of the network tells the reader nothing.
    if primary in (ROLE_SERVICE, ROLE_SCHEDULER) and input_ids and input_ids <= signals.external_inputs:
        facets.append(ROLE_INGRESS)
    if primary != ROLE_SCHEDULER and hits(_SCHEDULER_MARKERS):
        facets.append(ROLE_SCHEDULER)
    if primary != ROLE_SERVICE and plugin_id in signals.invoked_by:
        facets.append(ROLE_SERVICE)
    return tuple(dict.fromkeys(facets))


@dataclass(frozen=True, slots=True)
class RoleSignals:
    """Structural evidence about a plugin, gathered from the directory.

    Every field is a *fact* about the shipped contracts, not a naming guess —
    which is why the derivation below prefers these over id markers.  An early
    name-marker version mislabelled ``asset-check`` (its output is a
    ``dataset-validation`` report: it is a validator) and ``ocr-extract``
    (image in, text out: it is an ingress).
    """

    invoked_by: Mapping[str, frozenset[str]] = field(default_factory=dict)
    """plugin id -> the plugin ids that declare ``invokes`` pointing at it."""

    external_inputs: frozenset[str] = frozenset()
    """Contract ids declared (or measured) to have no producer in the network."""

    consumed_outputs: frozenset[str] = frozenset()
    """**Canonical** output contract ids some plugin consumes — i.e. not terminal."""

    alias_of: Mapping[str, str] = field(default_factory=dict)
    """contract id -> canonical business object, so aliased outputs are not read
    as dead ends."""

    validation_schema: str = "dataset-validation.schema.json"
    """Output schema meaning "this node validates rather than transforms"."""


def _derive_primary(
    plugin_id: str, spec: PluginSpec, signals: RoleSignals | None = None,
) -> RoleAssignment:
    """Evidence-first, deterministic fallback for when nothing declares a role.

    Order is deliberate: structural evidence (who calls me, what shape is my
    output, does anyone consume me) outranks id markers, and markers only break
    ties.  ``foundation`` is *not* used as a blanket rule — it would swallow
    shells and ingresses that happen to live in the support layer.
    """
    signals = signals or RoleSignals()
    tail = plugin_id.split(".")[-1]
    outputs = {port.port_id for port in spec.outputs}

    def hits(markers: Iterable[str]) -> str | None:
        return next((m for m in markers if m == tail or m in plugin_id or m in tail), None)

    # 1. someone declares `invokes` → this is a reusable service.  Structural,
    #    and exactly the 15-callee set in the shipped directory.
    callers = signals.invoked_by.get(plugin_id)
    if callers:
        names = "、".join(sorted(c.split(".")[-1] for c in callers)[:3])
        more = f" 等 {len(callers)} 个" if len(callers) > 3 else ""
        return RoleAssignment(
            ROLE_SERVICE, "derived",
            f"被 {names}{more} 通过 invokes 调用 —— 可复用服务",
        )

    # 2. scheduling is behavioural and cannot be read off the ports
    if (marker := hits(_SCHEDULER_MARKERS)) is not None:
        return RoleAssignment(ROLE_SCHEDULER, "derived", f"调度语义标记 {marker!r}")

    # 3. validators.  Deliberately only the *specific* id markers: two broader
    #    signals were tried and both are unsound — output schema
    #    `dataset-validation.schema.json` is reused by the foundation utilities
    #    (so it labels biz-standardize a validator), while requiring a
    #    verdict-shaped output name misses genuine verifiers like asset-check
    #    and inventory-count.  "validates vs transforms" is not reliably
    #    derivable, so anything not clearly marked goes to review.
    if (marker := hits(_VALIDATOR_MARKERS)) is not None and spec.inputs:
        return RoleAssignment(ROLE_VALIDATOR, "derived", f"校验语义标记 {marker!r}")

    # 4. nobody consumes me.  This is NOT proof of a terminal product: "no
    #    consumer" is also exactly the symptom of a wiring gap, and the two are
    #    indistinguishable from the directory alone.  Claiming `sink` here would
    #    relabel real gaps as design choices, so it goes to review instead.
    if outputs and not ({signals.alias_of.get(o, o) for o in outputs} & signals.consumed_outputs):
        return RoleAssignment(
            ROLE_REVIEW, "derived",
            "输出无任何消费方（已按别名归一）—— 「终端产物」与「下游缺口」无法从契约目录区分，交人工判定",
        )

    # 5. my input is external/raw → I am the mouth of the network
    input_ids = {port.port_id for port in spec.inputs}
    if input_ids and input_ids <= signals.external_inputs:
        marker = hits(_INGRESS_MARKERS)
        if marker is not None or not spec.outputs:
            return RoleAssignment(ROLE_INGRESS, "derived", f"全部输入均为外部输入（{marker or '无产出'}）")
        return RoleAssignment(ROLE_INGRESS, "derived", "全部输入均为外部输入 —— 数据接入")

    # 6. shells / normalisers
    if (marker := hits(_ADAPTER_MARKERS)) is not None:
        return RoleAssignment(ROLE_ADAPTER, "derived", f"外壳语义标记 {marker!r}")

    # 7. judgments — markers only, this is the least structural category
    if (marker := hits(_DECISION_MARKERS)) is not None:
        return RoleAssignment(ROLE_DECISION, "derived", f"判断语义标记 {marker!r}")

    # 8. everything else that consumes and produces is a transform
    return RoleAssignment(ROLE_TRANSFORM, "derived", "默认：消费业务对象并产出新对象")


def derive_role(
    plugin_id: str, spec: PluginSpec, signals: RoleSignals | None = None,
) -> RoleAssignment:
    """Primary role + secondary facets, both derived from structural evidence."""
    signals = signals or RoleSignals()
    primary = _derive_primary(plugin_id, spec, signals)
    facets = derive_facets(plugin_id, spec, signals, primary.role)
    return RoleAssignment(primary.role, primary.source, primary.reason, facets)


def derive_signals(
    specs: Mapping[str, PluginSpec],
    *,
    external_inputs: Iterable[str] = (),
    alias_of: Mapping[str, str] | None = None,
) -> RoleSignals:
    """Gather the structural evidence :func:`derive_role` relies on.

    ``external_inputs`` should be the reviewed list from the semantics catalog.
    When omitted, "nobody produces it" is measured from the directory itself —
    a sound fallback, since a contract with no producer must be seeded.

    ``alias_of`` maps a contract id to its canonical business object.  It must
    be supplied, or producers of an *aliased* contract look like dead ends: the
    hand-written chain's own ``project-scheme → scheme-request`` edge is proof
    that ``project-scheme`` has a consumer, and only the alias table knows that.
    """
    invoked_by: dict[str, set[str]] = {}
    for plugin_id, spec in specs.items():
        for invoked in spec.invokes:
            if invoked in specs and invoked != plugin_id:
                invoked_by.setdefault(invoked, set()).add(plugin_id)

    alias_of = alias_of or {}
    produced: set[str] = set()
    consumed: set[str] = set()
    produced_canonical: set[str] = set()
    consumed_canonical: set[str] = set()
    for spec in specs.values():
        for port in spec.outputs:
            produced.add(port.port_id)
            produced_canonical.add(alias_of.get(port.port_id, port.port_id))
        for port in spec.inputs:
            consumed.add(port.port_id)
            consumed_canonical.add(alias_of.get(port.port_id, port.port_id))

    declared_external = {alias_of.get(c, c) for c in external_inputs}
    unfillable = {c for c in consumed if c not in produced}
    return RoleSignals(
        invoked_by={target: frozenset(callers) for target, callers in invoked_by.items()},
        external_inputs=frozenset(declared_external | unfillable),
        consumed_outputs=frozenset(consumed_canonical & produced_canonical),
        alias_of=dict(alias_of),
    )


def resolve_role(
    plugin_id: str,
    spec: PluginSpec,
    *,
    declared: str | None = None,
    pack_roles: Mapping[str, str] | None = None,
    signals: RoleSignals | None = None,
) -> RoleAssignment:
    """Resolution order: declared (protocol) > pack override > derived.

    A declared/overridden primary role still gets **derived facets** — the
    secondary aspects are structural facts (who calls me, what my input is), so
    an author overriding the primary role should not silently lose them.
    """
    signals = signals or RoleSignals()
    if declared:
        if declared not in ROLES:
            raise ValueError(f"plugin {plugin_id}: unknown declared role {declared!r}")
        return RoleAssignment(
            declared, "declared", "plugin.protocol.json 显式声明",
            derive_facets(plugin_id, spec, signals, declared),
        )
    override = (pack_roles or {}).get(plugin_id)
    if override:
        if override not in ROLES:
            raise ValueError(f"plugin {plugin_id}: unknown pack role {override!r}")
        return RoleAssignment(
            override, "pack", "领域包覆写",
            derive_facets(plugin_id, spec, signals, override),
        )
    return derive_role(plugin_id, spec, signals)


def resolve_all(
    specs: Mapping[str, PluginSpec],
    *,
    declared: Mapping[str, str] | None = None,
    pack_roles: Mapping[str, str] | None = None,
    signals: RoleSignals | None = None,
    catalog: SemanticCatalog | None = None,
) -> dict[str, RoleAssignment]:
    """Resolve every plugin's role, computing the shared signals once.

    Pass the reviewed :class:`~packages.ai_planner.semantics.SemanticCatalog` as
    ``catalog`` so aliased outputs count as consumed; without it, producers of
    an aliased contract (``project-scheme``, ``report-frame``, …) are misread as
    dead ends and land in the review pile for no reason.

    ``pack_roles`` falls back to the curated :func:`load_role_map`, so a
    reviewed or AI-curated role assignment takes effect everywhere without every
    caller having to thread it through.  An absent map means the heuristics
    decide, exactly as before.
    """
    if signals is None:
        external: Iterable[str] = getattr(catalog, "external_inputs", ()) if catalog else ()
        alias_of: dict[str, str] = {}
        for group in getattr(catalog, "alias_groups", ()) if catalog else ():
            for member in group.all_ids:
                alias_of[member] = group.canonical
        signals = derive_signals(specs, external_inputs=external, alias_of=alias_of)
    roles = pack_roles if pack_roles is not None else load_role_map()
    return {
        plugin_id: resolve_role(
            plugin_id, spec,
            declared=(declared or {}).get(plugin_id),
            pack_roles=roles,
            signals=signals,
        )
        for plugin_id, spec in specs.items()
    }
