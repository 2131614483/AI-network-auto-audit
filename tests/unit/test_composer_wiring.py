"""Composer wiring: priority order, forward-only stages, cycle-safe acceptance.

The rewrite this covers replaced a three-pass sweep whose behaviour was decided
by traversal order:

* each input went to whichever producer the walk reached first, so the caller's
  ``select`` list order — not the semantics — picked who fed whom;
* a cycle-closing candidate was added and then bulk-deleted, and because only
  schema-fallback edges were eligible for deletion, the whole fallback pass
  silently contributed nothing (measured: 44 generated, 0 surviving);
* ``schema_ref`` alone was enough to wire two ports, which connects
  ``ocr-image`` to ``masked-set``.

These tests pin the replacements: explicit priority, a cycle test *before*
acceptance, and no schema-only wiring unless explicitly asked for.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

from packages.ai_planner.composer import (
    ComposeError,
    PluginSpec,
    PortSpec,
    _stage_rank,
    compose_flow,
    discover_plugins,
)
from packages.ai_planner.semantics import AliasGroup, SemanticCatalog

ARCHIVE_DRIVER = (
    Path(__file__).resolve().parents[2]
    / "docs" / "全流程运行日志-20260912" / "10-驱动脚本" / "_ai_compose_demo.py"
)


def _data_edges(flow: dict) -> set[tuple[str, str, str, str]]:
    return {
        (e["source_instance"], e["source_port"], e["target_instance"], e["target_port"])
        for e in flow["edges"]
        if e.get("edge_class", "data") == "data"
    }


def _digest(name: str) -> str:
    """Stand-in contract digest for a synthetic port (see ``_plugin``)."""
    return hashlib.sha256(name.encode("utf-8")).hexdigest()


def _plugin(plugin_id: str, *, inputs=(), outputs=()) -> PluginSpec:
    def ports(names, direction):
        return tuple(
            PortSpec(port_id=n, schema_ref=f"{n}.schema.json", direction=direction,
                     schema_sha256=_digest(n))
            for n in names
        )
    return PluginSpec(
        plugin_id=plugin_id, capability=plugin_id, name=plugin_id, description="",
        lifecycle="verified", domains=("audit",),
        inputs=ports(inputs, "input"), outputs=ports(outputs, "output"),
    )


def _temp_plugin_root(tmp_path: Path, specs: dict[str, PluginSpec]) -> Path:
    """Materialize fake plugins as a real contract directory."""
    root = tmp_path / "builtin"
    for plugin_id, spec in specs.items():
        folder = root / f"audit_{plugin_id}".replace("-", "_")
        folder.mkdir(parents=True)
        ports = lambda items, direction: [  # noqa: E731 - local builder
            {
                "contract_id": p.port_id, "version": "1.0.0", "format": "artifact_ref",
                "delivery": "reference", "classification": "audit_confidential",
                "schema_ref": p.schema_ref,
                # Synthetic schemas are not on disk, so the port states its own
                # digest; a shipped protocol omits it and derives from the file.
                "schema_sha256": p.schema_sha256,
            }
            for p in (spec.inputs if direction == "input" else spec.outputs)
        ]
        (folder / "plugin.protocol.json").write_text(json.dumps({
            "id": plugin_id, "capabilities": [{
                "id": plugin_id, "inputs": ports(spec, "input"), "outputs": ports(spec, "output"),
            }],
        }), encoding="utf-8")
    return root


# -- determinism -----------------------------------------------------------

def test_wiring_is_deterministic_and_input_order_independent() -> None:
    ids = sorted(discover_plugins())
    first = compose_flow(goal="全流程", select=ids, plan_key="p")
    second = compose_flow(goal="全流程", select=ids, plan_key="p")
    assert _data_edges(first) == _data_edges(second)

    third = compose_flow(goal="全流程", select=list(reversed(ids)), plan_key="p")
    assert _data_edges(third) == _data_edges(first), "wiring must not depend on select order"


# -- priority --------------------------------------------------------------

def test_exact_contract_match_beats_an_alias_for_the_same_input(tmp_path: Path) -> None:
    """Both producers can fill ``b.thing``; the one that literally names it wins.

    An exact contract-id match is direct evidence; a reviewed alias is
    equivalent but indirect, so it must not win the tie.
    """
    specs = {
        "audit.probe.exact-src": _plugin("audit.probe.exact-src", outputs=("thing",)),
        "audit.probe.alias-src": _plugin("audit.probe.alias-src", outputs=("thing-alias",)),
        "audit.probe.sink": _plugin("audit.probe.sink", inputs=("thing",)),
    }
    root = _temp_plugin_root(tmp_path, specs)
    catalog = SemanticCatalog(alias_groups=(
        AliasGroup(canonical="thing", members=("thing-alias",), evidence="test fixture"),
    ))
    flow = compose_flow(
        goal="x", select=sorted(specs), plugin_root=root, plan_key="p", semantics=catalog,
    )
    edges = _data_edges(flow)
    assert ("exact-src-001", "thing", "sink-001", "thing") in edges
    assert not any(src == "alias-src-001" for src, _s, _t, _p in edges)


def test_by_kind_reports_how_each_edge_was_found() -> None:
    flow = compose_flow(goal="全流程", select=sorted(discover_plugins()), plan_key="p")
    by_kind = flow["wiring_report"]["data_edges_by_kind"]
    assert by_kind, "every discovered edge must be attributed to a match kind"
    assert set(by_kind) <= {"exact", "alias", "schema_fallback"}


def test_the_reviewed_aliases_are_what_supplies_the_alias_edges() -> None:
    """Dropping the catalog must lose exactly the alias-discovered edges, which
    is what makes the catalog's contribution measurable."""
    ids = sorted(discover_plugins())
    with_aliases = compose_flow(goal="全流程", select=ids, plan_key="p")
    without = compose_flow(goal="全流程", select=ids, plan_key="p", semantics=SemanticCatalog())
    gained = _data_edges(with_aliases) - _data_edges(without)
    assert gained, "the reviewed aliases must contribute edges"
    assert with_aliases["wiring_report"]["data_edges_by_kind"].get("alias", 0) == len(gained)


# -- forward-only stages ---------------------------------------------------

def test_no_data_edge_runs_backwards_through_the_business_stages() -> None:
    flow = compose_flow(goal="全流程", select=sorted(discover_plugins()), plan_key="p")
    plugin_of = {n["node_instance_id"]: n["plugin_id"] for n in flow["nodes"]}
    for source, _sp, target, _tp in _data_edges(flow):
        assert _stage_rank(plugin_of[source]) <= _stage_rank(plugin_of[target]), (source, target)


def test_a_backward_candidate_is_refused() -> None:
    """A later-stage producer must not feed an earlier-stage consumer."""
    assert _stage_rank("audit.remedy.remedy-close") > _stage_rank("audit.mandate.demand-collect")


# -- cycle-safe acceptance -------------------------------------------------

def test_dropped_candidates_are_recorded_with_a_reason() -> None:
    flow = compose_flow(goal="全流程", select=sorted(discover_plugins()), plan_key="p")
    for dropped in flow["wiring_report"]["dropped_data_edges"]:
        assert dropped["reason"] in {"dropped_cycle", "dropped_duplicate_producer"}
        assert dropped["source_instance"] and dropped["target_instance"]


def test_duplicate_producers_are_recorded_when_a_port_takes_one_producer(tmp_path: Path) -> None:
    """A ``cardinality='one'`` input must still pick exactly one producer, and
    the losers must be visible so the choice is auditable rather than an
    artefact of iteration order."""
    specs = {
        "audit.probe.p1": _plugin("audit.probe.p1", outputs=("thing",)),
        "audit.probe.p2": _plugin("audit.probe.p2", outputs=("thing",)),
        "audit.probe.sink": _plugin("audit.probe.sink", inputs=("thing",)),
    }
    root = _temp_plugin_root(tmp_path, specs)
    flow = compose_flow(goal="x", select=sorted(specs), plugin_root=root, plan_key="p")
    in_edges = [e for e in flow["edges"] if e.get("target_port") == "thing"]
    assert len(in_edges) == 1, "a `one` port takes a single producer"
    duplicates = [
        d for d in flow["wiring_report"]["dropped_data_edges"]
        if d["reason"] == "dropped_duplicate_producer"
    ]
    assert len(duplicates) == 1, "the losing producer must be recorded"


def test_a_many_port_accepts_several_producers(tmp_path: Path) -> None:
    """A merge/collector input declares ``cardinality='many'`` in its contract;
    the composer must honour it instead of reporting the second source as a
    duplicate.  Hardcoding `one` made fan-in structurally impossible."""
    specs = {
        "audit.probe.p1": _plugin("audit.probe.p1", outputs=("thing",)),
        "audit.probe.p2": _plugin("audit.probe.p2", outputs=("thing",)),
        "audit.probe.merge": _plugin("audit.probe.merge", inputs=("thing",)),
    }
    root = _temp_plugin_root(tmp_path, specs)
    # declare the merge input as `many`
    protocol_path = next(root.glob("*merge*/plugin.protocol.json"))
    protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
    protocol["capabilities"][0]["inputs"][0]["cardinality"] = "many"
    protocol_path.write_text(json.dumps(protocol), encoding="utf-8")

    flow = compose_flow(goal="x", select=sorted(specs), plugin_root=root, plan_key="p")
    in_edges = [e for e in flow["edges"] if e.get("target_port") == "thing"]
    assert len(in_edges) == 2
    assert not [
        d for d in flow["wiring_report"]["dropped_data_edges"]
        if d["reason"] == "dropped_duplicate_producer"
    ]


def test_every_required_input_is_either_wired_or_seeded() -> None:
    """The graph must never leave a required input dangling."""
    flow = compose_flow(goal="全流程", select=sorted(discover_plugins()), plan_key="p")
    wired = {(t, p) for _s, _sp, t, p in _data_edges(flow)}
    seeded = {tuple(pair) for pair in flow["seed_inputs"]}
    specs = discover_plugins()
    plugin_of = {n["node_instance_id"]: n["plugin_id"] for n in flow["nodes"]}
    for node_id, plugin_id in plugin_of.items():
        for port in specs[plugin_id].inputs:
            if port.required:
                assert (node_id, port.port_id) in wired | seeded, (node_id, port.port_id)


# -- schema fallback is opt-in and off by default --------------------------

def test_schema_fallback_is_off_by_default() -> None:
    flow = compose_flow(goal="全流程", select=sorted(discover_plugins()), plan_key="p")
    assert "schema_fallback" not in flow["wiring_report"]["data_edges_by_kind"]


def test_schema_fallback_would_connect_unrelated_objects() -> None:
    """Documents *why* it is off.  If this ever stops being true the default can
    be revisited; until then enabling it means knowingly wiring noise."""
    specs = discover_plugins()
    flow = compose_flow(
        goal="全流程", select=sorted(specs), plan_key="p", allow_schema_fallback=True,
    )
    fallback = flow["wiring_report"]["data_edges_by_kind"].get("schema_fallback", 0)
    assert fallback > 0, "the flag must actually do something"
    plugin_of = {n["node_instance_id"]: n["plugin_id"] for n in flow["nodes"]}
    pairs = {
        (plugin_of[e["source_instance"]], plugin_of[e["target_instance"]])
        for e in flow["edges"]
    }
    # measured nonsense: an encrypted blob offered as the photo/audio input of
    # unrelated field plugins, purely because both sides carry the catch-all
    # `artifact-ref.schema.json`
    assert ("audit.foundation.data-encrypt", "audit.field.evidence-photo") in pairs


# -- chain hints -----------------------------------------------------------

def test_chain_hint_unknown_port_error_is_preserved() -> None:
    with pytest.raises(ComposeError, match="unknown port"):
        compose_flow(
            goal="x", select=sorted(discover_plugins()), plan_key="p",
            chain=[("audit.ledger-quality", "no-such-port", "audit.finding-draft", "finding-set")],
        )


def test_chain_hint_contract_mismatch_error_is_preserved() -> None:
    with pytest.raises(ComposeError, match="contract mismatch"):
        compose_flow(
            goal="x", select=sorted(discover_plugins()), plan_key="p",
            chain=[("audit.ledger-quality", "audit-quality-candidates",
                    "audit.report-draft", "finding-set")],
        )


@pytest.mark.skipif(not ARCHIVE_DRIVER.exists(), reason="archive driver not present")
def test_the_hand_written_main_chain_is_now_redundant() -> None:
    """The strongest statement about this rewrite: the composer derives the
    **same** data wiring the archive's 65 hand-written chain hints encode, so the
    chain is no longer load-bearing.

    It used to differ by one edge — ``suspicion-merge``'s input, where two
    producers legitimately compete.  Declaring that input ``cardinality='many'``
    (it is a merge node) lets both in, which is why the two configurations now
    agree exactly.
    """
    source = ARCHIVE_DRIVER.read_text(encoding="utf-8")

    def block(name: str) -> str:
        return re.search(rf"^{name} = \[(.*?)^\]", source, re.S | re.M).group(1)

    chain = [tuple(m) for m in re.findall(
        r'\("([^"]+)",\s*"([^"]+)",\s*"([^"]+)",\s*"([^"]+)"\)', block("CHAIN"),
    )]
    selected = re.findall(r'"([^"]+)"', block("VERIFIED"))
    assert len(chain) == 65 and len(selected) == 100

    with_chain = _data_edges(compose_flow(goal="g", select=selected, chain=chain, plan_key="p"))
    without = _data_edges(compose_flow(goal="g", select=selected, chain=None, plan_key="p"))
    assert with_chain == without
