"""AIOps capability payloads: the bridge from ports to a runtime's envelope.

A port is not the same thing as the key a verified runtime reads.  The audit
side already had one explicit builder (``audit.finding.draft``); without the
aiops equivalents those plugins composed and compiled but could never execute —
``_build_payload`` fell back to the generic path, and every runtime rejected the
envelope with "input is missing".

Two of these ports are *bundle*-typed: the artifact they point at carries the
real references plus the budget knobs.  The builder forwards what the bundle
holds rather than rebuilding a reference (the runtime verifies uri/sha256/size
itself, so a rebuilt ref would fail verification).
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from packages.plugin_topology.ir import IRNode
from packages.plugin_topology.ports_executor import (
    CAPABILITY_PAYLOAD_BUILDERS,
    ArtifactRef,
    capability_parameters,
)

AIOPS_CAPABILITIES = (
    "aiops.alert.triage",
    "aiops.alert.correlate",
    "aiops.rca.rank",
    "aiops.remediation.propose",
    "aiops.recovery.verify",
    "aiops.postmortem.draft",
    "aiops.ticket.draft",
)


def _node(capability: str) -> IRNode:
    return IRNode(
        node_instance_id="n-001", plugin_id=capability, capability=capability,
    )


def _ref(tmp_path: Path, name: str = "a.json") -> ArtifactRef:
    payload = b'{"contract_id":"x","contract_version":"1.0.0"}'
    path = tmp_path / name
    path.write_bytes(payload)
    return ArtifactRef(
        uri=path.resolve().as_uri(),
        sha256=hashlib.sha256(payload).hexdigest(),
        size_bytes=len(payload),
    )


def _binding(ref: ArtifactRef) -> dict[str, Any]:
    return {"ref": ref, "adapter": None, "source_instance": "s-001", "source_port": "p"}


def _bundle(tmp_path: Path, name: str, data: dict[str, Any]) -> ArtifactRef:
    path = tmp_path / name
    path.write_text(json.dumps(data), encoding="utf-8")
    raw = path.read_bytes()
    return ArtifactRef(
        uri=path.resolve().as_uri(),
        sha256=hashlib.sha256(raw).hexdigest(),
        size_bytes=len(raw),
    )


# -- registration -----------------------------------------------------------

def test_every_aiops_capability_has_a_builder() -> None:
    missing = [c for c in AIOPS_CAPABILITIES if c not in CAPABILITY_PAYLOAD_BUILDERS]
    assert missing == [], "an aiops plugin without a builder composes but cannot execute"


# -- envelope shape ---------------------------------------------------------

def test_triage_payload_namespaces_the_event_reference(tmp_path: Path) -> None:
    ref = _ref(tmp_path)
    payload = CAPABILITY_PAYLOAD_BUILDERS["aiops.alert.triage"](
        _node("aiops.alert.triage"), {"alert-event": [_binding(ref)]}, tmp_path,
    )
    assert set(payload) == {"triage"}
    assert payload["triage"]["alert_event"]["uri"] == ref.uri
    assert payload["triage"]["alert_event"]["sha256"] == ref.sha256


def test_correlate_forwards_the_bundled_reference_and_budgets(tmp_path: Path) -> None:
    inner = _ref(tmp_path, "alerts.json")
    bundle = _bundle(tmp_path, "bundle.json", {
        "artifact": {"uri": inner.uri, "sha256": inner.sha256, "size_bytes": inner.size_bytes},
        "window_minutes": 30,
        "max_candidates": 7,
    })
    payload = CAPABILITY_PAYLOAD_BUILDERS["aiops.alert.correlate"](
        _node("aiops.alert.correlate"), {"alert-event-set": [_binding(bundle)]}, tmp_path,
    )
    assert payload["alerts"]["artifact"]["uri"] == inner.uri, "must forward, not rebuild"
    assert payload["alerts"]["window_minutes"] == 30, "the bundle's own value wins"
    assert payload["alerts"]["max_candidates"] == 7


def test_correlate_falls_back_to_declared_defaults(tmp_path: Path) -> None:
    inner = _ref(tmp_path, "alerts.json")
    bundle = _bundle(tmp_path, "bundle.json", {
        "artifact": {"uri": inner.uri, "sha256": inner.sha256, "size_bytes": inner.size_bytes},
    })
    payload = CAPABILITY_PAYLOAD_BUILDERS["aiops.alert.correlate"](
        _node("aiops.alert.correlate"), {"alert-event-set": [_binding(bundle)]}, tmp_path,
    )
    declared = capability_parameters("aiops.alert.correlate")
    assert payload["alerts"]["window_minutes"] == declared["window_minutes"]
    assert payload["alerts"]["max_candidates"] == declared["max_candidates"]


def test_rca_forwards_both_bundled_references(tmp_path: Path) -> None:
    incidents = _ref(tmp_path, "incidents.json")
    topology = _ref(tmp_path, "topology.json")
    bundle = _bundle(tmp_path, "rca.json", {
        "incident_set": {"uri": incidents.uri, "sha256": incidents.sha256, "size_bytes": incidents.size_bytes},
        "topology_graph": {"uri": topology.uri, "sha256": topology.sha256, "size_bytes": topology.size_bytes},
    })
    payload = CAPABILITY_PAYLOAD_BUILDERS["aiops.rca.rank"](
        _node("aiops.rca.rank"), {"rca-input": [_binding(bundle)]}, tmp_path,
    )
    assert payload["rca"]["incident_set"]["uri"] == incidents.uri
    assert payload["rca"]["topology_graph"]["uri"] == topology.uri
    declared = capability_parameters("aiops.rca.rank")
    assert payload["rca"]["max_hops"] == declared["max_hops"]


def test_remediation_uses_declared_budgets(tmp_path: Path) -> None:
    ref = _ref(tmp_path)
    payload = CAPABILITY_PAYLOAD_BUILDERS["aiops.remediation.propose"](
        _node("aiops.remediation.propose"), {"rca-candidates": [_binding(ref)]}, tmp_path,
    )
    declared = capability_parameters("aiops.remediation.propose")
    assert payload["remediation"]["max_playbooks"] == declared["max_playbooks"]
    assert payload["remediation"]["canary_scope"] == declared["canary_scope"]
    assert payload["remediation"]["rca_candidates"]["uri"] == ref.uri


def test_recovery_takes_two_series_plus_an_optional_proposal(tmp_path: Path) -> None:
    baseline = _ref(tmp_path, "baseline.json")
    observed = _ref(tmp_path, "observed.json")
    binding = {
        "baseline-metric-series": [_binding(baseline)],
        "observed-metric-series": [_binding(observed)],
    }
    payload = CAPABILITY_PAYLOAD_BUILDERS["aiops.recovery.verify"](
        _node("aiops.recovery.verify"), binding, tmp_path,
    )
    assert payload["recovery"]["baseline"]["uri"] == baseline.uri
    assert payload["recovery"]["observed"]["uri"] == observed.uri
    assert "proposal" not in payload["recovery"], "the proposal is optional"

    proposal = _ref(tmp_path, "proposal.json")
    with_proposal = dict(binding)
    with_proposal["remediation-proposal"] = [_binding(proposal)]
    payload = CAPABILITY_PAYLOAD_BUILDERS["aiops.recovery.verify"](
        _node("aiops.recovery.verify"), with_proposal, tmp_path,
    )
    assert payload["recovery"]["proposal"]["uri"] == proposal.uri


def test_postmortem_and_ticket_namespace_the_incident_proposal(tmp_path: Path) -> None:
    ref = _ref(tmp_path)
    postmortem = CAPABILITY_PAYLOAD_BUILDERS["aiops.postmortem.draft"](
        _node("aiops.postmortem.draft"), {"incident-proposal": [_binding(ref)]}, tmp_path,
    )
    assert postmortem["postmortem"]["incident_proposal"]["uri"] == ref.uri
    assert "verification" not in postmortem["postmortem"]

    ticket = CAPABILITY_PAYLOAD_BUILDERS["aiops.ticket.draft"](
        _node("aiops.ticket.draft"), {"incident-proposal": [_binding(ref)]}, tmp_path,
    )
    assert ticket["ticket"]["incident_proposal"]["uri"] == ref.uri


# -- fail-closed ------------------------------------------------------------

def test_a_missing_binding_is_a_clear_failure(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="has no bound producer artifact"):
        CAPABILITY_PAYLOAD_BUILDERS["aiops.alert.triage"](
            _node("aiops.alert.triage"), {}, tmp_path,
        )


def test_a_bundle_without_the_reference_is_rejected(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path, "rca.json", {"incident_set": {}})
    with pytest.raises(RuntimeError, match="no usable 'incident_set'"):
        CAPABILITY_PAYLOAD_BUILDERS["aiops.rca.rank"](
            _node("aiops.rca.rank"), {"rca-input": [_binding(bundle)]}, tmp_path,
        )


def test_an_unreadable_bundle_is_rejected(tmp_path: Path) -> None:
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    ref = ArtifactRef(uri=broken.resolve().as_uri(), sha256="a" * 64, size_bytes=9)
    with pytest.raises(RuntimeError, match="unreadable"):
        CAPABILITY_PAYLOAD_BUILDERS["aiops.alert.correlate"](
            _node("aiops.alert.correlate"), {"alert-event-set": [_binding(ref)]}, tmp_path,
        )


# -- the declared parameters -----------------------------------------------

def test_declared_parameters_are_in_range_for_their_runtimes() -> None:
    """The runtime rejects out-of-range values, so a bad declaration would turn
    into a runtime failure rather than a clear configuration error."""
    ranges = {
        ("aiops.alert.correlate", "window_minutes"): (1, 10080),
        ("aiops.alert.correlate", "max_candidates"): (1, 1000),
        ("aiops.rca.rank", "max_candidates"): (1, 100),
        ("aiops.rca.rank", "max_hops"): (1, 3),
        ("aiops.remediation.propose", "max_playbooks"): (1, 20),
        ("aiops.recovery.verify", "availability_target"): (0.0, 1.0),
        ("aiops.recovery.verify", "threshold_ratio"): (0.0, 10.0),
    }
    for (capability, name), (low, high) in ranges.items():
        value = capability_parameters(capability)[name]
        assert low <= value <= high, (capability, name, value)

    assert capability_parameters("aiops.remediation.propose")["canary_scope"] in {
        "single_node", "subset_10pct", "low_traffic",
    }


def test_an_undeclared_parameter_is_a_clear_failure(tmp_path: Path) -> None:
    """Fail-closed with a pointer to the artifact, not a bare KeyError."""
    from packages.plugin_topology.ports_executor import _required_param

    with pytest.raises(RuntimeError, match="capability-parameters.json"):
        _required_param("aiops.alert.triage", "nonexistent")
