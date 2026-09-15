"""Layered layout for a composed plugin network, as data plus an SVG fragment.

Shared by the static renderer (``scripts/render-network.py``) and the local
workbench app, so the picture an author inspects and the picture the workbench
draws are produced by the same code — a rendering that drifts from the artifact
is worse than no rendering.

Encoding (deliberate, and validated — see ``docs`` in the dataviz palette):

* **column = business stage** (left→right); **colour = layer** (base / biz / gov).
  Only three colour categories, because a hue per stage would need ten and no
  palette survives that under colour-vision deficiency.  The categorical slots
  used here pass ``validate_palette.js --pairs all`` in both light and dark.
* a node is a **coloured dot plus an ink label**, never coloured text;
* edges are **solid for data, dashed for call** — a line-style channel;
* an island carries a dashed ring and a tag rather than a fourth hue.
"""
from __future__ import annotations

import html
from pathlib import Path
from typing import Any, Mapping

from .connectivity import classify_islands, flow_edges_by_plugin, island_summary
from .domain_pack import DomainPack, load_pack
from .roles import ROLE_NAMES_ZH, resolve_all
from .semantics import SemanticCatalog, load_semantics

LAYER_ORDER: tuple[str, ...] = ("base", "biz", "gov")
LAYER_LABEL: dict[str, str] = {
    "base": "数据支撑层", "biz": "核心业务循环层", "gov": "治理优化层",
}


def build_view(
    draft: Mapping[str, Any],
    *,
    domain: str | None = None,
    pack: DomainPack | None = None,
    catalog: SemanticCatalog | None = None,
) -> dict[str, Any]:
    """Everything the layout needs, computed from the draft alone."""
    from .composer import discover_plugins

    domain = domain or str(draft.get("domain") or "audit")
    pack = pack or load_pack(domain)
    catalog = catalog if catalog is not None else load_semantics()
    specs = discover_plugins(globs=pack.globs)

    roles = resolve_all(specs, catalog=catalog)
    islands = {
        i.plugin_id: i
        for i in classify_islands(
            specs, edges=flow_edges_by_plugin(dict(draft)), catalog=catalog,
        )
    }

    def _stage_of(plugin_id: str) -> str:
        """Column key for a plugin: its business stage, else its layer."""
        return pack.stage_of(plugin_id) or pack.layer_of(plugin_id)

    def _stage_name(plugin_id: str) -> str:
        """Human label for that column: the stage's name, else the layer's.

        A stage the pack does not know has no entry in ``stage_name``, so the
        layer label is used; ``svg_fragment`` falls back to the raw column key
        for anything still unnamed.
        """
        return pack.stage_name.get(_stage_of(plugin_id)) or LAYER_LABEL.get(
            pack.layer_of(plugin_id), ""
        )

    nodes = [
        {
            "node_instance_id": str(node["node_instance_id"]),
            "plugin_id": plugin_id,
            "label": plugin_id.split(".")[-1],
            "stage": _stage_of(plugin_id),
            "stage_name": _stage_name(plugin_id),
            "layer": pack.layer_of(plugin_id),
            "role": roles[plugin_id].role if plugin_id in roles else "review",
            "role_zh": ROLE_NAMES_ZH.get(
                roles[plugin_id].role if plugin_id in roles else "review", "—",
            ),
            "island": islands[plugin_id].category if plugin_id in islands else "",
            "island_reason": islands[plugin_id].detail if plugin_id in islands else "",
            "input_ports": [
                {"port_id": str(p.get("port_id") or ""), "schema_ref": str(p.get("schema_ref") or "")}
                for p in node.get("input_ports") or ()
            ],
            "output_ports": [
                {"port_id": str(p.get("port_id") or ""), "schema_ref": str(p.get("schema_ref") or "")}
                for p in node.get("output_ports") or ()
            ],
        }
        for node in draft.get("nodes") or ()
        for plugin_id in [str(node.get("plugin_id") or "")]
    ]
    edges = [
        {
            "edge_id": str(e.get("edge_id")),
            "source": str(e.get("source_instance")),
            "target": str(e.get("target_instance")),
            "source_port": str(e.get("source_port") or ""),
            "target_port": str(e.get("target_port") or ""),
            "class": str(e.get("edge_class") or "data"),
        }
        for e in draft.get("edges") or ()
    ]
    order = list(pack.stage_order) + [
        layer for layer in LAYER_ORDER if layer in {n["layer"] for n in nodes}
    ]
    # Every node must land in a column.  A stage the pack does not know — the
    # ``ungrouped_stage`` of a newly added plugin, or a plugin from another
    # domain's pack — is not in ``order``, and dropping it here used to make
    # ``svg_fragment`` raise ``KeyError`` on the node it then could not place.
    # A trailing column is the honest answer: showing the draft is this
    # module's job, and ``compile_plan`` is the only authority on validity.
    present: list[str] = [str(n["stage"]) for n in nodes]
    columns = [c for c in order if c in present]
    columns += sorted(set(present) - set(columns))
    return {
        "domain": domain,
        "domain_label": pack.name_zh,
        "goal": str(draft.get("goal") or ""),
        "plan_key": str(draft.get("plan_key") or ""),
        "nodes": nodes,
        "edges": edges,
        "columns": columns,
        "stage_names": {**pack.stage_name, **LAYER_LABEL},
        "islands": island_summary(tuple(islands.values())),
        "stats": {
            "nodes": len(nodes),
            "edges": len(edges),
            "data_edges": sum(1 for e in edges if e["class"] == "data"),
            "call_edges": sum(1 for e in edges if e["class"] == "call"),
            "seeds": len(draft.get("seed_inputs") or ()),
            "islands": len(islands),
        },
    }


def svg_fragment(view: Mapping[str, Any], *, interactive: bool = False) -> tuple[str, int, int]:
    """The SVG body plus its canvas size.

    ``interactive`` adds ``data-*`` hooks and a hit-area rect per node so a host
    page can attach click handlers; the geometry is identical either way.
    """
    row, pad_x, pad_y, col_w = 26, 18, 46, 185
    columns = list(view["columns"])
    by_col: dict[str, list[Mapping[str, Any]]] = {c: [] for c in columns}
    for node in view["nodes"]:
        by_col[node["stage"]].append(node)
    for bucket in by_col.values():
        bucket.sort(key=lambda n: (n["layer"], n["plugin_id"]))
    tallest = max((len(b) for b in by_col.values()), default=1)
    height = tallest * row + pad_y * 2 + 40
    # the last column's label extends past its dot, so the right gutter is sized
    # to the longest label rather than to pad_x
    width = len(columns) * col_w + pad_x + 175

    pos: dict[str, tuple[float, float]] = {}
    for ci, col in enumerate(columns):
        bucket = by_col[col]
        top = pad_y + 34 + (tallest - len(bucket)) * row / 2
        for ri, node in enumerate(bucket):
            pos[node["node_instance_id"]] = (pad_x + ci * col_w + 14, top + ri * row)

    parts: list[str] = []
    for ci, col in enumerate(columns):
        head_x = pad_x + ci * col_w + 14
        parts.append(
            f'<text class="colhead" x="{head_x}" y="24">{html.escape(view["stage_names"].get(col, col))}'
            f'<tspan class="colcount" dx="6">({len(by_col[col])})</tspan></text>'
            f'<line class="colrule" x1="{head_x}" y1="32" x2="{head_x}" y2="{height - pad_y}"/>'
        )
    # Edges first, so nodes sit on top.  A long edge is bowed away from the node
    # row: drawn straight it would run through every label between its ends,
    # which is the "label clipped by a mark" anti-pattern.  Control points sit
    # close to the ends so the curve dives clear of the label quickly, and the
    # bow's sign alternates so two long edges in one band do not coincide.
    for index, edge in enumerate(view["edges"]):
        if edge["source"] not in pos or edge["target"] not in pos:
            continue
        x1, y1 = pos[edge["source"]]
        x2, y2 = pos[edge["target"]]
        span = abs(x2 - x1)
        kind = "edge edge-call" if edge["class"] == "call" else "edge edge-data"
        hook = f' data-edge-id="{html.escape(edge["edge_id"])}"' if interactive else ""
        if span < col_w:
            dx = max(24.0, span * 0.45)
            d = f"M{x1 + 7},{y1} C{x1 + dx},{y1} {x2 - dx},{y2} {x2 - 9},{y2}"
        else:
            bow = min(78.0, span * 0.13) * (1 if index % 2 == 0 else -1)
            off = max(52.0, min(130.0, span * 0.22))
            d = f"M{x1 + 7},{y1} C{x1 + off},{y1 + bow} {x2 - off},{y2 + bow} {x2 - 9},{y2}"
        parts.append(f'<path class="{kind}"{hook} d="{d}"/>')
    for node in view["nodes"]:
        x, y = pos[node["node_instance_id"]]
        nid = html.escape(node["node_instance_id"])
        hook = f' data-node-id="{nid}"' if interactive else ""
        ring = ' class="dot dot-island"' if node["island"] else f' class="dot dot-{node["layer"]}"'
        parts.append(f'<circle{ring} cx="{x}" cy="{y}" r="5"/>')
        parts.append(f'<text class="nlabel" x="{x + 12}" y="{y + 4}">{html.escape(node["label"])}</text>')
        parts.append(
            f'<text class="nrole" x="{x + 12}" y="{y + 15}">{html.escape(node["role_zh"])}'
            f'{" · 孤岛" if node["island"] else ""}</text>'
        )
        if interactive:
            # a hit target bigger than the mark, so the dot is easy to click
            parts.append(
                f'<rect class="hit"{hook} x="{x - 6}" y="{y - 11}" width="150" height="22" '
                f'rx="4"><title>{nid}</title></rect>'
            )
    return "\n".join(parts), width, height


def table_rows(view: Mapping[str, Any]) -> list[dict[str, str]]:
    """The table view — the same data as the picture, for readers who need text.

    Rows follow the picture's own column order rather than sorting the stage
    keys: ``s1..s8`` then ``base``/``gov`` is the business order, while a
    lexicographic sort prints ``base`` and ``gov`` first and quietly reorders
    the narrative the figure tells.
    """
    rank = {column: index for index, column in enumerate(view["columns"])}
    return [
        {
            "node_instance_id": n["node_instance_id"],
            "plugin_id": n["plugin_id"],
            "stage": n["stage_name"],
            "role": n["role_zh"],
            "island": n["island_reason"] or "—",
        }
        for n in sorted(
            view["nodes"],
            key=lambda n: (rank.get(n["stage"], len(rank)), n["plugin_id"]),
        )
    ]


def drafts_root() -> Path:
    return Path(__file__).resolve().parents[2] / ".data" / "drafts"
