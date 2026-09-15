"""Composer multi-instance support: addressing, replication, id clashes.

``node_instance_id`` already made two instances of one plugin expressible in the
IR and executable by the runtime, but the composer could not *address* them: a
chain hint resolved by plugin id, so it always bound the first instance and the
second sat unwired while the plan still compiled.  These tests pin the fix, the
replication API, and the id-collision guard.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from packages.ai_planner.composer import (
    ComposeError,
    PluginSpec,
    PortSpec,
    compile_flow,
    compose_flow,
)


def _digest(name: str) -> str:
    """A stand-in contract digest for a synthetic port.

    Synthetic ports name schemas that are deliberately *not* on disk, and the
    composer refuses to invent a digest for a schema it cannot read -- that
    refusal is the point (the old ``"a"*64`` placeholder let two divergent
    contracts compare equal).  Two ports that share a ``schema_ref`` therefore
    have to share this digest by hand, which is exactly what the real
    derivation would produce for them.
    """
    return hashlib.sha256(name.encode("utf-8")).hexdigest()


def _plugin(plugin_id: str, *, inputs=(), outputs=(), cardinality="one") -> PluginSpec:
    def ports(names, direction):
        return tuple(
            PortSpec(port_id=n, schema_ref=f"{n}.schema.json", direction=direction,
                     cardinality=cardinality, schema_sha256=_digest(n))
            for n in names
        )
    return PluginSpec(
        plugin_id=plugin_id, capability=plugin_id, name=plugin_id, description="",
        lifecycle="verified", domains=("audit",),
        inputs=ports(inputs, "input"), outputs=ports(outputs, "output"),
    )


def _root(tmp_path: Path, specs: dict[str, PluginSpec]) -> Path:
    root = tmp_path / "builtin"
    for plugin_id, spec in specs.items():
        folder = root / plugin_id.replace(".", "_").replace("-", "_")
        folder.mkdir(parents=True)
        def port_entries(items, direction):
            return [
                {
                    "contract_id": p.port_id, "version": "1.0.0", "format": "artifact_ref",
                    "delivery": "reference", "classification": "audit_confidential",
                    "schema_ref": p.schema_ref, "cardinality": p.cardinality,
                    # The port carries its own digest because this fixture's
                    # schemas are not on disk; a shipped protocol leaves the
                    # field out and the composer derives it from the file.
                    "schema_sha256": p.schema_sha256,
                }
                for p in items
            ]
        (folder / "plugin.protocol.json").write_text(json.dumps({
            "id": plugin_id, "capabilities": [{
                "id": plugin_id,
                "inputs": port_entries(spec.inputs, "input"),
                "outputs": port_entries(spec.outputs, "output"),
            }],
        }), encoding="utf-8")
    return root


def _data(flow: dict) -> set[tuple[str, str]]:
    return {
        (e["source_instance"], e["target_instance"])
        for e in flow["edges"]
        if e.get("edge_class", "data") == "data"
    }


# -- addressing -------------------------------------------------------------

def test_a_chain_hint_can_address_the_second_instance(tmp_path: Path) -> None:
    """The bug this fixes: resolving by plugin id bound the first instance
    regardless, so `x-002` could never be wired."""
    specs = {
        "audit.probe.src": _plugin("audit.probe.src", outputs=("thing",)),
        "audit.probe.sink": _plugin("audit.probe.sink", inputs=("thing",)),
    }
    root = _root(tmp_path, specs)
    selected = ["audit.probe.src", "audit.probe.sink", "audit.probe.src"]
    flow = compose_flow(
        goal="x", select=selected, plugin_root=root, plan_key="p",
        chain=[("src-002", "thing", "sink-001", "thing")],
    )
    assert _data(flow) == {("src-002", "sink-001")}


def test_addressing_a_plugin_id_with_several_instances_is_recorded(tmp_path: Path) -> None:
    """Resolving to the first instance is a policy; leaving it invisible is not.
    The report says a choice was made and which node won."""
    specs = {
        "audit.probe.src": _plugin("audit.probe.src", outputs=("thing",)),
        "audit.probe.sink": _plugin("audit.probe.sink", inputs=("thing",)),
    }
    root = _root(tmp_path, specs)
    selected = ["audit.probe.src", "audit.probe.sink", "audit.probe.src"]
    flow = compose_flow(
        goal="x", select=selected, plugin_root=root, plan_key="p",
        chain=[("audit.probe.src", "thing", "sink-001", "thing")],
    )
    ambiguous = flow["wiring_report"]["ambiguous_chain_targets"]
    assert ambiguous == [{
        "token": "audit.probe.src", "resolved_to": "src-001", "candidates": 2,
    }]


def test_an_unknown_chain_endpoint_is_rejected(tmp_path: Path) -> None:
    specs = {"audit.probe.sink": _plugin("audit.probe.sink", inputs=("thing",))}
    root = _root(tmp_path, specs)
    with pytest.raises(ComposeError, match="unknown plugin or instance"):
        compose_flow(
            goal="x", select=["audit.probe.sink"], plugin_root=root, plan_key="p",
            chain=[("nope", "thing", "audit.probe.sink", "thing")],
        )


# -- replication ------------------------------------------------------------

def test_instances_replicates_a_plugin_deterministically(tmp_path: Path) -> None:
    specs = {
        "audit.probe.src": _plugin("audit.probe.src", outputs=("thing",)),
        "audit.probe.sink": _plugin("audit.probe.sink", inputs=("thing",)),
    }
    root = _root(tmp_path, specs)
    flow = compose_flow(
        goal="x", select=sorted(specs), plugin_root=root, plan_key="p",
        instances={"audit.probe.src": 3},
    )
    ids = [n["node_instance_id"] for n in flow["nodes"]]
    assert ids.count("src-001") == 1
    assert {"src-002", "src-003"} <= set(ids)
    assert flow["wiring_report"]["replicated_plugins"] == {"audit.probe.src": 3}


def test_instance_k_pairs_with_instance_k(tmp_path: Path) -> None:
    """Replicating a chain once per batch must pair 001↔001, not cross-wire."""
    specs = {
        "audit.probe.src": _plugin("audit.probe.src", outputs=("thing",)),
        "audit.probe.mid": _plugin("audit.probe.mid", inputs=("thing",), outputs=("thing2",)),
        "audit.probe.sink": _plugin("audit.probe.sink", inputs=("thing2",)),
    }
    root = _root(tmp_path, specs)
    flow = compose_flow(
        goal="x", select=sorted(specs), plugin_root=root, plan_key="p",
        instances={"audit.probe.src": 2, "audit.probe.mid": 2, "audit.probe.sink": 2},
    )
    edges = _data(flow)
    assert ("src-001", "mid-001") in edges and ("src-002", "mid-002") in edges
    assert ("mid-001", "sink-001") in edges and ("mid-002", "sink-002") in edges
    assert ("src-002", "mid-001") not in edges


def test_a_replication_count_below_one_is_rejected(tmp_path: Path) -> None:
    specs = {"audit.probe.src": _plugin("audit.probe.src", outputs=("thing",))}
    root = _root(tmp_path, specs)
    with pytest.raises(ComposeError, match="must be >= 1"):
        compose_flow(
            goal="x", select=["audit.probe.src"], plugin_root=root, plan_key="p",
            instances={"audit.probe.src": 0},
        )


def test_replication_stays_deterministic(tmp_path: Path) -> None:
    specs = {
        "audit.probe.src": _plugin("audit.probe.src", outputs=("thing",)),
        "audit.probe.sink": _plugin("audit.probe.sink", inputs=("thing",)),
    }
    root = _root(tmp_path, specs)
    kwargs = dict(select=sorted(specs), plugin_root=root, plan_key="p",
                  instances={"audit.probe.src": 2})
    first = compose_flow(goal="x", **kwargs)
    second = compose_flow(goal="x", **kwargs)
    assert [n["node_instance_id"] for n in first["nodes"]] == [
        n["node_instance_id"] for n in second["nodes"]
    ]
    assert _data(first) == _data(second)


# -- id clashes -------------------------------------------------------------

def test_two_plugins_sharing_a_last_segment_get_distinct_instance_ids(tmp_path: Path) -> None:
    """`audit.field.workpaper-build` and `audit.evidence.workpaper-build` both
    slug to `workpaper-build`; without disambiguation the plan mints the same
    instance id twice and fails to compile for a reason unrelated to the plan."""
    specs = {
        "audit.field.workpaper-build": _plugin("audit.field.workpaper-build", outputs=("draft",)),
        "audit.evidence.workpaper-build": _plugin("audit.evidence.workpaper-build", inputs=("draft",)),
    }
    root = _root(tmp_path, specs)
    flow = compose_flow(goal="x", select=sorted(specs), plugin_root=root, plan_key="p")
    ids = [n["node_instance_id"] for n in flow["nodes"]]
    assert len(ids) == len(set(ids)), "instance ids must be unique"
    compile_flow(flow)  # must not raise


def test_two_instances_of_one_plugin_are_not_treated_as_a_clash(tmp_path: Path) -> None:
    """Counting occurrences instead of distinct owners would rename every
    replicated instance out from under the caller."""
    specs = {
        "audit.probe.src": _plugin("audit.probe.src", outputs=("thing",)),
        "audit.probe.sink": _plugin("audit.probe.sink", inputs=("thing",)),
    }
    root = _root(tmp_path, specs)
    selected = ["audit.probe.src", "audit.probe.sink", "audit.probe.src"]
    flow = compose_flow(goal="x", select=selected, plugin_root=root, plan_key="p")
    ids = {n["node_instance_id"] for n in flow["nodes"]}
    assert "src-002" in ids, f"expected plain instance ids, got {sorted(ids)}"


# -- cardinality passthrough ------------------------------------------------

def test_declared_cardinality_reaches_the_compiled_port(tmp_path: Path) -> None:
    specs = {"audit.probe.merge": _plugin("audit.probe.merge", inputs=("thing",), cardinality="many")}
    root = _root(tmp_path, specs)
    flow = compose_flow(goal="x", select=["audit.probe.merge"], plugin_root=root, plan_key="p")
    plan = compile_flow(flow)
    assert plan.nodes[0].input_ports[0].cardinality == "many"
