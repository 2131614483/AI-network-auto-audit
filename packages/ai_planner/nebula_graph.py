"""Dynamic knowledge-nebula graph for the desktop.

This is the single authoritative builder behind the
``GET /api/v1/topology/plugin-nebula`` endpoint and the desktop
"知识星云" independent window.  It never executes a plugin: it only reads the
on-disk contract directory via :func:`discover_plugins` and groups the result
into the audit network's three layers / eight business stages.

Design goals
------------
* **New plugins appear automatically.** Whenever a new ``audit-*`` plugin
  folder with a ``plugin.protocol.json`` is dropped under ``plugins/builtin``
  (lifecycle ``contract_only`` or ``verified``), the next graph build lists
  it — no hand-edited frontend table.
* **A plugin is never hidden.** A business plugin whose id segment is not yet
  known falls into an explicit ``s0`` (未分组) stage instead of disappearing,
  so an unfamiliar plugin stays visible and can be classified later.
* Pure / side-effect free: no DB, no network, no policy — the API layer wraps
  it with tenant + trace + Policy Gateway.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from packages.plugin_topology.derivation import BUILTIN_DIR, CatalogEntry, load_directory

from .composer import discover_plugins

# Re-exported: these used to be defined here and callers import them from this
# module.  The logic now lives in `domain_pack` so a second domain can reuse it.
from .domain_pack import (  # noqa: F401
    LAYER_BASE,
    LAYER_BIZ,
    LAYER_GOV,
    DomainPack,
    group_segment,
    id_segment,
    load_pack,
)

# The audit pack reproduces the values that used to be hardcoded here, verbatim.
# Deriving the module constants from it keeps every existing import working while
# making the knowledge data-driven — and lets a second pack (aiops) prove the
# framework is not audit-specific.
DEFAULT_PACK: DomainPack = load_pack()

# (stage key, 中文名, 电子云色相, id 第二段语义段集合 → 归属该业务阶段)
STAGES: tuple[tuple[str, str, str, tuple[str, ...]], ...] = tuple(
    (stage.key, stage.name_zh, stage.color, stage.segments)
    for stage in sorted(DEFAULT_PACK.stages, key=lambda s: s.order)
)
STAGE_ORDER: list[str] = [s[0] for s in STAGES]
SEGMENT_TO_STAGE: dict[str, str] = {
    segment: stage[0] for stage in STAGES for segment in stage[3]
}
STAGE_NAME = {stage[0]: stage[1] for stage in STAGES}
STAGE_COLOR = {stage[0]: stage[2] for stage in STAGES}

GOV_SEGMENT = DEFAULT_PACK.governance_segment or "govern"
BASE_SEGMENT = DEFAULT_PACK.support_segment or "foundation"
UNGROUPED_STAGE = DEFAULT_PACK.ungrouped_stage

# 全流程管控穿透节点（与树状组网 ctrl 标记一致）
CONTROL_SEGMENTS = set(DEFAULT_PACK.control_segments)

# 主干环流：s1→…→s8→治理环→s1（电子云中轨闭环）
TRUNK: list[tuple[str, str]] = [(source, target) for source, target in DEFAULT_PACK.trunk]


def classify(plugin_id: str) -> tuple[str, str | None]:
    """Map a plugin id to ``(layer, stage)``.

    layer is one of gov/base/biz; stage is the business stage key for biz
    plugins (``s0`` fallback) and ``None`` for gov/base layers.

    Delegates to the loaded pack, so the same call works for any domain once its
    pack is loaded.  Kept here because callers import it from this module.
    """
    return DEFAULT_PACK.classify(plugin_id)


def _is_control(plugin_id: str) -> bool:
    return DEFAULT_PACK.is_control(plugin_id)


def build_nebula_graph(
    plugin_root: Path | str | None = None,
    lifecycle: str | None = None,
    domain: str | None = None,
) -> dict[str, Any]:
    """Build the nebula graph from the live on-disk plugin directory.

    ``lifecycle=None`` returns the full contract directory (so a freshly
    authored ``contract_only`` plugin already shows up); pass
    ``lifecycle="verified"`` to restrict to runnable plugins.  ``domain`` scopes
    the graph to one domain pack (``audit`` / ``aiops`` / ``quant`` / ``knowledge``);
    omit it for the audit pack, which is the default the desktop renders.
    """
    root = Path(plugin_root) if plugin_root is not None else None
    specs = discover_plugins(root, lifecycle, domain=domain)
    ordered_ids = sorted(specs)

    # Design-time version + derivation catalog.  ``discover_plugins`` is scoped
    # by the domain pack's globs, so the catalog can hold plugins outside this
    # graph (another domain's); every derivation edge is filtered to ``specs``
    # below rather than assuming the two sets agree.
    #
    # ``load_directory`` is deliberately strict (it also backs a validation
    # gate), so it is guarded here: the graph is a read-only projection and a
    # malformed descriptor must not take the endpoint down.  ``discover_plugins``
    # already skips such files, so the graph still renders without lineage.
    try:
        catalog: dict[str, CatalogEntry] = load_directory(root or BUILTIN_DIR)
    except (OSError, ValueError):
        catalog = {}

    # contract_id -> plugin ids that OUTPUT that data contract (deterministic
    # order follows sorted plugin ids).  Used to wire subject-to-subject
    # data-interface edges: a producer's output contract feeds every plugin
    # that declares the same contract as an input (same rule as composer).
    producers_by_contract: dict[str, list[str]] = {}
    for plugin_id in ordered_ids:
        for port in specs[plugin_id].outputs:
            bucket = producers_by_contract.setdefault(port.port_id, [])
            if plugin_id not in bucket:
                bucket.append(plugin_id)

    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, str]] = []
    layer_counter: Counter[str] = Counter()
    lifecycle_counter: Counter[str] = Counter()
    used_stages: set[str] = set()
    dataflow_pairs: set[tuple[str, str, str]] = set()
    cross_pairs: set[tuple[str, str]] = set()
    derivation_pairs: set[tuple[str, str]] = set()

    for plugin_id in ordered_ids:
        spec = specs[plugin_id]
        layer, stage = classify(plugin_id)
        input_contracts = sorted({port.port_id for port in spec.inputs})
        output_contracts = sorted({port.port_id for port in spec.outputs})
        entry = catalog.get(plugin_id)
        declared = entry.derivation if entry is not None else None
        base_id = declared.plugin_id if declared is not None else ""
        node = {
            "id": plugin_id,
            "name": spec.name or plugin_id,
            "capability": spec.capability,
            "layer": layer,
            "stage": stage,
            "lifecycle": spec.lifecycle,
            "ctrl": _is_control(plugin_id),
            "inputs": input_contracts,
            "outputs": output_contracts,
            "invokes": sorted(spec.invokes),
            # The plugin's declared version, and the plugin version it was
            # derived from (empty when it declares no base).
            "version": entry.version if entry is not None else "",
            "derived_from": base_id,
        }
        nodes.append(node)
        layer_counter[layer] += 1
        lifecycle_counter[spec.lifecycle] += 1
        if base_id and base_id in specs:
            derivation_pairs.add((plugin_id, base_id))
            edges.append({
                "source": plugin_id, "target": base_id,
                "type": "derivation", "contract": "",
            })
        if layer == LAYER_BIZ and stage:
            used_stages.add(stage)
            # 阶段簇心 → 其下业务插件（电子云团归属边）
            edges.append({"source": stage, "target": plugin_id, "type": "cluster", "contract": ""})
        # 主体数据接口边：每个输入契约的每个生产者 → 当前插件
        for contract_id in input_contracts:
            for source_id in producers_by_contract.get(contract_id, []):
                if source_id == plugin_id:
                    continue
                pair = (source_id, plugin_id, contract_id)
                if pair in dataflow_pairs:
                    continue
                dataflow_pairs.add(pair)
                edges.append({
                    "source": source_id, "target": plugin_id,
                    "type": "dataflow", "contract": contract_id,
                })
        # 跨层能力调用边：本插件声明 invokes 的支撑层（foundation）能力
        for invoked in sorted(spec.invokes):
            if invoked == plugin_id or invoked not in specs:
                continue  # 安全跳过未知 / 自引用目标
            if classify(invoked)[0] != LAYER_BASE:
                continue  # cross 只连业务/治理 → 支撑层
            cross_pair = (plugin_id, invoked)
            if cross_pair in cross_pairs:
                continue
            cross_pairs.add(cross_pair)
            edges.append({
                "source": plugin_id, "target": invoked,
                "type": "cross", "contract": "",
            })

    stages: list[dict[str, Any]] = [
        {"key": key, "name": STAGE_NAME[key], "color": STAGE_COLOR[key], "order": idx + 1}
        for idx, key in enumerate(STAGE_ORDER)
        if key in used_stages or key == STAGE_ORDER[0]  # 至少保留 s1 作为骨架
    ]
    if UNGROUPED_STAGE in used_stages:
        stages.append({"key": UNGROUPED_STAGE, "name": "未分组新插件",
                       "color": "#9aa6b8", "order": 0})

    return {
        "stages": stages,
        "nodes": nodes,
        "edges": edges,
        "trunk": [{"source": a, "target": b, "type": "trunk", "contract": ""} for a, b in TRUNK],
        "layers": [
            {"key": "core", "name": "审计大脑", "kind": "nucleus"},
            {"key": LAYER_GOV, "name": "治理优化层", "kind": "inner_orbit"},
            {"key": LAYER_BIZ, "name": "核心业务循环层", "kind": "middle_orbit"},
            {"key": LAYER_BASE, "name": "数据支撑层", "kind": "outer_orbit"},
        ],
        "stats": {
            "total": len(nodes),
            "by_layer": dict(layer_counter),
            "by_lifecycle": dict(lifecycle_counter),
            "stages": len([s for s in stages if s["key"] != UNGROUPED_STAGE]),
            "dataflow_edges": len(dataflow_pairs),
            "cross_edges": len(cross_pairs),
            "derivation_edges": len(derivation_pairs),
            "ungrouped": layer_counter.get(LAYER_BIZ, 0)
            and sum(1 for n in nodes if n["stage"] == UNGROUPED_STAGE),
        },
    }
