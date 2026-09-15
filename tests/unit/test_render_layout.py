"""``render.py``: the picture must be the draft, and both hosts must draw it alike.

``packages/ai_planner/render.py`` is the single layout shared by the static
renderer (``scripts/render-network.py``) and the local workbench app
(``scripts/network-workbench.py``).  Nothing referenced it, so the module whose
whole justification is "what you see is what would run" had no test behind it —
and two real defects were living in that gap:

* a node whose plugin the pack cannot place (an unmapped segment, or a plugin
  from another domain read out of a hand-edited draft) was dropped from
  ``columns`` and then blew up ``svg_fragment`` with ``KeyError``;
* the two hosts each carry a copy of the stylesheet, so a class the layout emits
  could go unstyled in one of them with nobody noticing.

The contracts pinned here:

* the view covers the draft exactly — no node dropped, no node invented;
* encoding is a channel, not decoration: a node is a **coloured dot plus ink
  labels**, and data/call edges differ by **line style**, never by hue;
* the interactive variant changes hooks only — the geometry is unchanged;
* everything the layout emits is styled by *both* hosts, and they style the same
  set (their two stylesheets are one contract in two copies).
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import pytest

from packages.ai_planner.domain_pack import available_domains, load_pack
from packages.ai_planner.render import LAYER_ORDER, build_view, svg_fragment, table_rows
from packages.ai_planner.workbench import new_draft

ROOT = Path(__file__).resolve().parents[2]
DOMAINS = ("audit", "aiops", "quant", "knowledge")

_HOSTS = {
    "scripts/render-network.py": ROOT / "scripts" / "render-network.py",
    "scripts/network-workbench.py": ROOT / "scripts" / "network-workbench.py",
}

#: ``dot`` is a namespace prefix, not a painted class: every mark is written
#: ``dot dot-<layer>`` or ``dot dot-island`` and the colour comes from the
#: suffixed class, so ``dot`` itself legitimately has no rule in either host.
_NAMESPACE_ONLY = frozenset({"dot"})

_CLASS_ATTR = re.compile(r'class="([^"]+)"')
_CSS_CLASS = re.compile(r"\.([A-Za-z][A-Za-z0-9_-]*)")
_STYLE_BLOCK = re.compile(r"<style>(.*?)</style>", re.S)


@lru_cache(maxsize=1)
def _emitted_classes() -> frozenset[str]:
    """Every CSS class the layout can put on the wire, over all domains."""
    names: set[str] = set()
    for domain in DOMAINS:
        view = build_view(new_draft(domain, "全流程"))
        for interactive in (False, True):
            svg, _, _ = svg_fragment(view, interactive=interactive)
            for attr in _CLASS_ATTR.findall(svg):
                names.update(attr.split())
    return frozenset(names)


def _styled_classes(path: Path) -> set[str]:
    text = path.read_text(encoding="utf-8")
    return {
        name
        for block in _STYLE_BLOCK.findall(text)
        for name in _CSS_CLASS.findall(block)
    }


@pytest.fixture(scope="module")
def aiops_draft() -> dict:
    return new_draft("aiops", "告警处置闭环")


def _aiops_handbook(edges: list[dict], nodes: list[dict] | None = None) -> dict:
    """A small, hand-written aiops draft — the workbench takes edits like this."""
    return {
        "schema_version": "1.0.0",
        "domain": "aiops",
        "goal": "手写草稿",
        "plan_key": "plan-handbook",
        "nodes": nodes
        if nodes is not None
        else [
            {"node_instance_id": "triage-001", "plugin_id": "aiops.alert-triage",
             "input_ports": [], "output_ports": []},
            {"node_instance_id": "corr-001", "plugin_id": "aiops.alert-correlation",
             "input_ports": [], "output_ports": []},
        ],
        "edges": edges,
        "seed_inputs": [],
    }


# ---------------------------------------------------------------------------
# the view is the draft
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("domain", DOMAINS)
def test_the_view_covers_the_draft_exactly(domain: str) -> None:
    draft = new_draft(domain, "全流程")
    view = build_view(draft)
    assert [n["node_instance_id"] for n in view["nodes"]] == [
        str(n["node_instance_id"]) for n in draft["nodes"]
    ]
    assert [n["plugin_id"] for n in view["nodes"]] == [
        str(n["plugin_id"]) for n in draft["nodes"]
    ]
    assert [e["edge_id"] for e in view["edges"]] == [
        str(e["edge_id"]) for e in draft["edges"]
    ]
    assert view["domain"] == domain
    assert view["domain_label"] == load_pack(domain).name_zh


def test_the_domain_defaults_to_the_draft_when_the_caller_omits_it(aiops_draft: dict) -> None:
    assert build_view(aiops_draft)["domain"] == "aiops"


def test_an_unknown_domain_is_refused_rather_than_guessed(aiops_draft: dict) -> None:
    """A missing pack must not silently render as "a domain with no plugins"."""
    with pytest.raises(FileNotFoundError):
        build_view(aiops_draft, domain="no-such-domain")


@pytest.mark.parametrize("domain", DOMAINS)
def test_every_node_lands_in_a_column_and_no_column_is_empty(domain: str) -> None:
    view = build_view(new_draft(domain, "全流程"))
    stages = [n["stage"] for n in view["nodes"]]
    assert set(stages) <= set(view["columns"])
    for column in view["columns"]:
        assert stages.count(column) >= 1, f"column {column!r} is drawn with no node in it"


@pytest.mark.parametrize("domain", DOMAINS)
def test_columns_keep_the_pack_order(domain: str) -> None:
    """Reading order is the business order; a scramble would misdescribe the flow."""
    pack = load_pack(domain)
    columns = build_view(new_draft(domain, "全流程"))["columns"]
    assert [c for c in columns if c in pack.stage_order] == [
        c for c in pack.stage_order if c in columns
    ]


def test_a_plugin_the_pack_cannot_place_is_drawn_not_dropped() -> None:
    """Regression: the ungrouped stage used to fall out of ``columns``.

    ``stage_of`` never returns ``None`` for a business plugin, so a plugin whose
    segment the pack does not map lands on ``ungrouped_stage``.  That key is in
    no stage list, so ``columns`` skipped it and ``svg_fragment`` raised
    ``KeyError`` while looking the node's column up.  A draft is exactly the
    thing that can be wrong — showing it is this module's job, and
    ``compile_plan`` is the only authority on validity.
    """
    pack = load_pack("aiops")
    draft = _aiops_handbook(
        edges=[],
        nodes=[{"node_instance_id": "outsider-001", "plugin_id": "quant.factor-compute",
                "input_ports": [], "output_ports": []}],
    )
    view = build_view(draft)
    node = view["nodes"][0]
    assert node["stage"] == pack.ungrouped_stage
    assert node["stage"] in view["columns"]
    assert view["columns"][-1] == pack.ungrouped_stage, "a stage the pack does not know sorts last"

    svg, _, _ = svg_fragment(view)
    assert '<circle class="dot dot-biz" cx=' in svg
    assert ">factor-compute<" in svg

    assert view["stats"]["nodes"] == 1
    assert table_rows(view)[0]["plugin_id"] == "quant.factor-compute"


@pytest.mark.parametrize("domain", DOMAINS)
def test_stats_are_the_view_counted(domain: str) -> None:
    view = build_view(new_draft(domain, "全流程"))
    stats = view["stats"]
    assert stats["nodes"] == len(view["nodes"])
    assert stats["edges"] == len(view["edges"])
    assert stats["data_edges"] == sum(1 for e in view["edges"] if e["class"] == "data")
    assert stats["call_edges"] == sum(1 for e in view["edges"] if e["class"] == "call")
    assert stats["data_edges"] + stats["call_edges"] == stats["edges"]


def test_table_rows_describe_the_same_nodes_in_the_picture_s_order(aiops_draft: dict) -> None:
    view = build_view(aiops_draft)
    rows = table_rows(view)
    assert sorted(r["node_instance_id"] for r in rows) == sorted(
        n["node_instance_id"] for n in view["nodes"]
    )
    rank = {column: index for index, column in enumerate(view["columns"])}
    by_id = {n["node_instance_id"]: n for n in view["nodes"]}
    keys = [
        (rank[by_id[r["node_instance_id"]]["stage"]], by_id[r["node_instance_id"]]["plugin_id"])
        for r in rows
    ]
    assert keys == sorted(keys), "the table reads in the picture's column order"
    assert all(r["stage"] for r in rows), "a row without a stage name is unreadable"


def test_the_table_keeps_the_business_order_of_the_audit_layers() -> None:
    """``s1..s8`` then the layers — a lexicographic sort of the keys would print
    ``base`` and ``gov`` first and reorder the whole narrative."""
    view = build_view(new_draft("audit", "全流程"))
    pack = load_pack("audit")
    order = [h["stage"] for h in table_rows(view)]
    first_layer = next(
        (i for i, h in enumerate(order) if h in ("数据支撑层", "治理优化层")), None
    )
    first_stage_name = pack.stage_name[pack.stage_order[0]]
    if first_layer is not None:
        assert order.index(first_stage_name) < first_layer


# ---------------------------------------------------------------------------
# encoding: a channel per meaning
# ---------------------------------------------------------------------------

def test_svg_draws_one_coloured_dot_and_two_ink_labels_per_node(aiops_draft: dict) -> None:
    view = build_view(aiops_draft)
    svg, _, _ = svg_fragment(view)
    layers = {n["layer"] for n in view["nodes"]}
    assert layers and layers <= set(LAYER_ORDER)
    for layer in sorted(layers):
        assert f'class="dot dot-{layer}"' in svg
    assert svg.count("<circle") == len(view["nodes"])
    assert svg.count('class="nlabel"') == len(view["nodes"])
    assert svg.count('class="nrole"') == len(view["nodes"])


def test_no_text_carries_its_own_colour(aiops_draft: dict) -> None:
    """A hue per stage would need ten; the palette only survives three layers.

    So colour lives on the dot and the label is ink.  An inline ``fill`` on a
    <text> element is the drift this forbids.
    """
    svg, _, _ = svg_fragment(build_view(aiops_draft))
    for element in re.findall(r"<text\b[^>]*>", svg):
        assert "fill=" not in element, element


def test_an_island_is_a_dashed_ring_and_a_tag_not_a_fourth_hue() -> None:
    view = build_view(new_draft("quant", "全流程"))
    islands = [n for n in view["nodes"] if n["island"]]
    assert islands, "the quant draft is expected to contain an island"
    svg, _, _ = svg_fragment(view)
    assert svg.count('class="dot dot-island"') == len(islands)
    assert svg.count("· 孤岛") == len(islands)
    for layer in {n["layer"] for n in view["nodes"] if not n["island"]}:
        assert f'class="dot dot-{layer}"' in svg


def test_data_edges_are_solid_and_call_edges_are_dashed() -> None:
    draft = _aiops_handbook(edges=[
        {"edge_id": "e1", "source_instance": "triage-001", "target_instance": "corr-001",
         "source_port": "in", "target_port": "in", "edge_class": "data"},
        {"edge_id": "e2", "source_instance": "corr-001", "target_instance": "triage-001",
         "source_port": "out", "target_port": "out", "edge_class": "call"},
    ])
    view = build_view(draft)
    assert view["stats"]["data_edges"] == 1
    assert view["stats"]["call_edges"] == 1
    svg, _, _ = svg_fragment(view)
    assert svg.count('class="edge edge-data"') == 1
    assert svg.count('class="edge edge-call"') == 1
    # the channel is dash-vs-solid, and it is spelled once per host stylesheet
    for path in _HOSTS.values():
        css = path.read_text(encoding="utf-8")
        call_rule = re.search(r"\.edge-call[^{]*\{([^}]*)\}", css)
        data_rule = re.search(r"\.edge-data[^{]*\{([^}]*)\}", css)
        assert call_rule is not None and "dasharray" in call_rule.group(1), path.name
        assert data_rule is not None and "dasharray" not in data_rule.group(1), path.name


def test_every_wired_edge_becomes_a_path_and_a_dangling_one_is_skipped() -> None:
    draft = _aiops_handbook(edges=[
        {"edge_id": "e1", "source_instance": "triage-001", "target_instance": "corr-001",
         "source_port": "in", "target_port": "in", "edge_class": "data"},
        {"edge_id": "e2", "source_instance": "triage-001", "target_instance": "ghost-001",
         "source_port": "in", "target_port": "in", "edge_class": "data"},
    ])
    view = build_view(draft)
    svg, _, _ = svg_fragment(view)
    # the dangling edge is still counted — it is in the draft — but cannot be drawn
    assert view["stats"]["edges"] == 2
    assert svg.count("<path") == 1
    assert svg.count("<circle") == 2


def test_labels_and_ids_are_escaped() -> None:
    """The SVG is assembled by string concatenation, so every interpolation is
    an injection site; a hand-edited draft is untrusted input."""
    draft = _aiops_handbook(
        edges=[],
        nodes=[{"node_instance_id": '<img src=x onerror="1">',
                "plugin_id": "aiops.field.<script>alert(1)</script>",
                "input_ports": [], "output_ports": []}],
    )
    view = build_view(draft)
    svg, _, _ = svg_fragment(view)
    assert "<script>" not in svg
    assert "&lt;script&gt;" in svg
    interactive, _, _ = svg_fragment(view, interactive=True)
    assert "<img" not in interactive
    assert "&lt;img src=x onerror=&quot;1&quot;&gt;" in interactive


def test_the_svg_is_deterministic(aiops_draft: dict) -> None:
    """Regenerable from the artifacts, or the picture is decoration."""
    view = build_view(aiops_draft)
    assert svg_fragment(view) == svg_fragment(view)


def test_a_draft_with_no_nodes_renders_an_empty_fragment_without_crashing() -> None:
    view = build_view(_aiops_handbook(edges=[], nodes=[]))
    assert view["nodes"] == []
    assert view["columns"] == []
    svg, width, height = svg_fragment(view)
    assert svg == ""
    assert width > 0 and height > 0
    assert table_rows(view) == []


# ---------------------------------------------------------------------------
# the interactive variant: hooks only
# ---------------------------------------------------------------------------

def test_the_interactive_variant_changes_hooks_only(aiops_draft: dict) -> None:
    """A hit target must not move the picture it is over."""
    view = build_view(aiops_draft)
    plain, width, height = svg_fragment(view)
    interactive, iwidth, iheight = svg_fragment(view, interactive=True)
    assert (width, height) == (iwidth, iheight)

    stripped = re.sub(r'<rect class="hit".*?</rect>', "", interactive, flags=re.S)
    stripped = re.sub(r' data-edge-id="[^"]*"', "", stripped)
    assert [line for line in stripped.split("\n") if line.strip()] == plain.split("\n")


def test_the_interactive_variant_gives_every_mark_a_hit_target(aiops_draft: dict) -> None:
    view = build_view(aiops_draft)
    interactive, _, _ = svg_fragment(view, interactive=True)
    assert interactive.count('class="hit"') == len(view["nodes"])
    assert len(re.findall(r'data-node-id="', interactive)) == len(view["nodes"])
    assert len(re.findall(r'data-edge-id="', interactive)) == len(view["edges"])


# ---------------------------------------------------------------------------
# the two hosts cannot drift
# ---------------------------------------------------------------------------

def test_every_class_the_layout_emits_is_styled_by_both_hosts() -> None:
    emitted = set(_emitted_classes()) - _NAMESPACE_ONLY
    assert emitted, "the layout should emit something"
    for name, path in _HOSTS.items():
        missing = emitted - _styled_classes(path)
        assert not missing, f"{name} draws {sorted(missing)} with no rule behind it"


def test_the_two_hosts_style_the_same_render_classes() -> None:
    """They are one stylesheet in two copies; a class defined in only one of
    them is how "the picture the author inspects" and "the picture the
    workbench draws" stop being the same picture."""
    render_classes = set(_emitted_classes()) - _NAMESPACE_ONLY
    (name_a, path_a), (name_b, path_b) = _HOSTS.items()
    styled_a, styled_b = _styled_classes(path_a), _styled_classes(path_b)
    assert not (styled_a & render_classes) - (styled_b & render_classes), (
        f"{name_a} styles a render class {name_b} does not"
    )
    assert not (styled_b & render_classes) - (styled_a & render_classes), (
        f"{name_b} styles a render class {name_a} does not"
    )


def test_every_domain_pack_on_disk_is_renderable() -> None:
    """A pack the renderer cannot load is a domain nobody can look at — this is
    what kept the quant and knowledge plugins out of the picture."""
    assert set(DOMAINS) <= set(available_domains())
    for domain in available_domains():
        view = build_view(new_draft(domain, "全流程"))
        assert view["nodes"], f"{domain} composed no nodes"
        svg, _, _ = svg_fragment(view)
        assert svg
