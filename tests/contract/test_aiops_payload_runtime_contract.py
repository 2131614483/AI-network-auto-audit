"""The builder must satisfy the runtime, not merely look plausible.

These tests close the loop the unit tests cannot: they build a payload with the
registered builder and hand it to the **real plugin runtime in process**.  Before
the aiops builders existed, every one of these capabilities failed at the
runtime with "input is missing" while still composing and compiling cleanly — a
gap that only shows up when the two halves are actually joined.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from packages.plugin_topology.ir import IRNode
from packages.plugin_topology.ports_executor import CAPABILITY_PAYLOAD_BUILDERS, ArtifactRef

PLUGINS = Path(__file__).resolve().parents[2] / "plugins" / "builtin"


def _load_runtime(folder: str) -> ModuleType:
    path = PLUGINS / folder / "runtime.py"
    spec = importlib.util.spec_from_file_location(f"_probe_{folder}", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _artifact(tmp_path: Path, name: str, data: dict[str, Any]) -> ArtifactRef:
    path = tmp_path / name
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    raw = path.read_bytes()
    return ArtifactRef(
        uri=path.resolve().as_uri(),
        sha256=hashlib.sha256(raw).hexdigest(),
        size_bytes=len(raw),
    )


def _ref_dict(ref: ArtifactRef) -> dict[str, Any]:
    return {"uri": ref.uri, "sha256": ref.sha256, "size_bytes": ref.size_bytes}


def _binding(ref: ArtifactRef) -> dict[str, Any]:
    return {"ref": ref, "adapter": None, "source_instance": "s-001", "source_port": "p"}


def _envelope(capability: str, payload: dict[str, Any], plugin_id: str) -> dict[str, Any]:
    return {
        "protocol": "audit-network-plugin-child-v1",
        "plugin_id": plugin_id,
        "capability": capability,
        **payload,
    }


@pytest.fixture(autouse=True)
def _read_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUDIT_PLUGIN_READ_ROOTS", json.dumps([str(tmp_path.resolve())]))


# -- triage -----------------------------------------------------------------

def _alert_event() -> dict[str, Any]:
    return {
        "contract_id": "alert-event",
        "contract_version": "1.0.0",
        "event_id": "EV-1",
        "fingerprint": "a" * 64,
        "occurred_at": "2026-01-15T10:00:00",
        "severity": "high",
        "summary": "支付网关 5xx 激增",
        "source": "prometheus",
    }


def test_the_triage_builder_satisfies_the_triage_runtime(tmp_path: Path) -> None:
    ref = _artifact(tmp_path, "alert-event.json", _alert_event())
    payload = CAPABILITY_PAYLOAD_BUILDERS["aiops.alert.triage"](
        IRNode(node_instance_id="n-001", plugin_id="aiops.alert-triage",
               capability="aiops.alert.triage"),
        {"alert-event": [_binding(ref)]},
        tmp_path,
    )
    runtime = _load_runtime("aiops_alert_triage")
    output = runtime.handle(_envelope("aiops.alert.triage", payload, "aiops.alert-triage"))
    assert output["contract_id"] == "incident-proposal"
    assert output["contract_version"] == "1.0.0"
    assert output["proposal_id"]


def test_the_triage_runtime_rejects_a_payload_built_for_the_generic_path(tmp_path: Path) -> None:
    """The control: without the capability builder the generic path binds
    ``payload["alert-event"]``, and the runtime refuses it.  That is exactly the
    failure the aiops builders remove."""
    ref = _artifact(tmp_path, "alert-event.json", _alert_event())
    runtime = _load_runtime("aiops_alert_triage")
    generic = {"alert-event": _ref_dict(ref)}
    with pytest.raises(runtime.InputRejected, match="triage input is missing"):
        runtime.handle(_envelope("aiops.alert.triage", generic, "aiops.alert-triage"))


# -- ticket: chained from triage's real output ------------------------------
# Hand-writing the incident-proposal fixture would only prove the fixture is
# self-consistent.  Feeding triage's *actual* output to ticket proves the two
# runtimes interoperate — which is the property that matters.

def test_the_triage_output_feeds_the_ticket_builder_and_runtime(tmp_path: Path) -> None:
    event_ref = _artifact(tmp_path, "alert-event.json", _alert_event())
    triage_payload = CAPABILITY_PAYLOAD_BUILDERS["aiops.alert.triage"](
        IRNode(node_instance_id="n-001", plugin_id="aiops.alert-triage",
               capability="aiops.alert.triage"),
        {"alert-event": [_binding(event_ref)]},
        tmp_path,
    )
    triage_output = _load_runtime("aiops_alert_triage").handle(
        _envelope("aiops.alert.triage", triage_payload, "aiops.alert-triage")
    )
    assert triage_output["contract_id"] == "incident-proposal"

    proposal_ref = _artifact(tmp_path, "incident-proposal.json", triage_output)
    ticket_payload = CAPABILITY_PAYLOAD_BUILDERS["aiops.ticket.draft"](
        IRNode(node_instance_id="n-002", plugin_id="aiops.ticket-draft",
               capability="aiops.ticket.draft"),
        {"incident-proposal": [_binding(proposal_ref)]},
        tmp_path,
    )
    ticket_output = _load_runtime("aiops_ticket_draft").handle(
        _envelope("aiops.ticket.draft", ticket_payload, "aiops.ticket-draft")
    )
    assert ticket_output["contract_id"] == "ticket-draft"
    assert ticket_output["contract_version"] == "1.0.0"


# -- the whole aiops chain, executed ---------------------------------------
# Each runtime is driven with a payload produced by the registered builder, and
# each output is fed to the next stage through its builder.  This is what
# "the framework generalises" has to mean in practice: not that aiops *composes*,
# but that it runs.

_RUNTIME_CACHE: dict[str, ModuleType] = {}


def _runtime(folder: str) -> ModuleType:
    if folder not in _RUNTIME_CACHE:
        _RUNTIME_CACHE[folder] = _load_runtime(folder)
    return _RUNTIME_CACHE[folder]


def _run(capability: str, plugin_id: str, folder: str, bindings: dict[str, Any], tmp_path: Path) -> dict[str, Any]:
    payload = CAPABILITY_PAYLOAD_BUILDERS[capability](
        IRNode(node_instance_id="n-001", plugin_id=plugin_id, capability=capability),
        bindings, tmp_path,
    )
    return _runtime(folder).handle(_envelope(capability, payload, plugin_id))


def _alert_set() -> list[dict[str, Any]]:
    return [
        {"timestamp": "2026-01-15T10:00:00", "severity": "high",
         "affected_system": "payment-gateway", "summary": "5xx 激增"},
        {"timestamp": "2026-01-15T10:01:00", "severity": "high",
         "affected_system": "payment-gateway", "summary": "5xx 激增"},
    ]


def _incidents() -> list[dict[str, Any]]:
    return [{
        "incident_id": "INC-1",
        "fault_description": "支付网关 5xx 激增",
        "affected_system": "payment-gateway",
        "equipment_type": "gateway",
    }]


def _topology() -> dict[str, Any]:
    return {
        "nodes_by_type": {
            "service": [
                {"id": "payment-gateway", "text": "支付网关 服务 5xx 失败"},
                {"id": "db-primary", "text": "主库 连接池 超时"},
            ],
            "equipment": [{"id": "gw-01", "text": "网关设备 硬件"}],
        },
        "edges": {
            "depends_on": [
                {"source": "payment-gateway", "target": "db-primary",
                 "confidence": 0.8, "strength": 0.7},
                {"source": "gw-01", "target": "payment-gateway",
                 "confidence": 0.6, "strength": 0.5},
            ],
        },
    }


def _series(metric: str, values: list[float]) -> dict[str, Any]:
    return {
        "contract_id": "metric-series",
        "contract_version": "1.0.0",
        "series_id": f"{metric}-1",
        "metric": metric,
        "unit": "ms",
        "window_minutes": 60,
        "points": [{"at": f"2026-01-15T10:{i:02d}:00", "value": v} for i, v in enumerate(values)],
    }


def test_the_whole_aiops_incident_chain_executes(tmp_path: Path) -> None:
    """triage / correlate / rca-ranker / remediation / recovery / postmortem /
    ticket — all seven run, chained through their builders."""
    # 1. triage: an alert event in, an incident proposal out
    event = _artifact(tmp_path, "alert-event.json", _alert_event())
    proposal = _run("aiops.alert.triage", "aiops.alert-triage", "aiops_alert_triage",
                    {"alert-event": [_binding(event)]}, tmp_path)
    assert proposal["contract_id"] == "incident-proposal"

    # 2. correlate: a bundle-typed alert set in
    alert_set_ref = _artifact(tmp_path, "alerts.json", _alert_set())
    alerts_bundle = _artifact(tmp_path, "alert-event-set.json", {
        **_ref_dict(alert_set_ref), "contract_id": "alert-event-set",
        "contract_version": "1.0.0", "artifact": _ref_dict(alert_set_ref),
    })
    correlated = _run("aiops.alert.correlate", "aiops.alert-correlation", "aiops_alert_correlation",
                      {"alert-event-set": [_binding(alerts_bundle)]}, tmp_path)
    assert correlated["contract_id"] == "incident-candidate-set"

    # 3. rca-ranker: a bundle carrying two references
    incidents_ref = _artifact(tmp_path, "incidents.json", _incidents())
    topology_ref = _artifact(tmp_path, "topology.json", _topology())
    rca_bundle = _artifact(tmp_path, "rca-input.json", {
        "contract_id": "rca-input", "contract_version": "1.0.0",
        "incident_set": _ref_dict(incidents_ref),
        "topology_graph": _ref_dict(topology_ref),
    })
    candidates = _run("aiops.rca.rank", "aiops.rca-ranker", "aiops_rca_ranker",
                      {"rca-input": [_binding(rca_bundle)]}, tmp_path)
    assert candidates["contract_id"] == "rca-candidates"

    # 4. remediation: the previous stage's real output
    candidates_ref = _artifact(tmp_path, "rca-candidates.json", candidates)
    remediation = _run("aiops.remediation.propose", "aiops.playbook-proposer",
                       "aiops_playbook_proposer",
                       {"rca-candidates": [_binding(candidates_ref)]}, tmp_path)
    assert remediation["contract_id"] == "remediation-proposal"

    # 5. recovery: baseline + observed series, plus the optional proposal
    baseline_ref = _artifact(tmp_path, "baseline.json", _series("latency_p95", [120.0, 130.0, 125.0]))
    observed_ref = _artifact(tmp_path, "observed.json", _series("latency_p95", [110.0, 115.0, 112.0]))
    remediation_ref = _artifact(tmp_path, "remediation-proposal.json", remediation)
    verification = _run("aiops.recovery.verify", "aiops.recovery-verifier", "aiops_recovery_verifier", {
        "baseline-metric-series": [_binding(baseline_ref)],
        "observed-metric-series": [_binding(observed_ref)],
        "remediation-proposal": [_binding(remediation_ref)],
    }, tmp_path)
    assert verification["contract_id"] == "recovery-verification"

    # 6. postmortem: the incident proposal and the verification
    proposal_ref = _artifact(tmp_path, "incident-proposal.json", proposal)
    verification_ref = _artifact(tmp_path, "recovery-verification.json", verification)
    postmortem = _run("aiops.postmortem.draft", "aiops.postmortem-draft", "aiops_postmortem_draft", {
        "incident-proposal": [_binding(proposal_ref)],
        "recovery-verification": [_binding(verification_ref)],
    }, tmp_path)
    assert postmortem["contract_id"] == "postmortem-draft"

    # 7. ticket: the same incident proposal, a different terminal product
    ticket = _run("aiops.ticket.draft", "aiops.ticket-draft", "aiops_ticket_draft",
                  {"incident-proposal": [_binding(proposal_ref)]}, tmp_path)
    assert ticket["contract_id"] == "ticket-draft"
    assert ticket["contract_version"] == "1.0.0"
