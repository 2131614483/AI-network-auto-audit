"""Plugin Topology M5 unit tests: ISO gate, governed inputs, bridge materializer,
per-node execution ledger (fake runtime), fail-closed node termination.

Nothing here touches a database or starts a real child process; the runtime is
a recording fake and the cursor records every ledger INSERT.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from packages.plugin_runtime.db_artifacts import ArtifactInput
from packages.plugin_runtime.runner import PluginRuntimeError, resolve_file_uri
from packages.plugin_topology.contracts import validate_ledger
from packages.plugin_topology.isolated import (
    DEFAULT_BRIDGE_MATERIALIZERS,
    InputSource,
    IsolatedChainExecutor,
    IsolatedGateError,
    verify_input_source,
)
from packages.policy.engine import PolicyEngine

_TENANT = uuid4()
_TRACE = uuid4()


class _FakeRuntime:
    """Recording fake for IsolatedPluginRuntime; fails on demand."""

    def __init__(self, *, deny: set[str] | None = None) -> None:
        self.deny = set(deny or ())
        self.calls: list[tuple[str, str, str]] = []
        self.allowed_roots: tuple[Path, ...] = (Path.cwd(),)

    def invoke(self, invocation, policy):  # noqa: ANN001 - fake mirrors runner signature
        self.calls.append((invocation.capability, invocation.plugin_id, invocation.trace_id))
        if invocation.capability in self.deny:
            raise PluginRuntimeError("simulated runtime failure")
        checksum = hashlib.sha256(f"{invocation.capability}-ok".encode("utf-8")).hexdigest()
        return SimpleNamespace(
            plugin_id=invocation.plugin_id,
            plugin_version="1.0.0",
            capability=invocation.capability,
            trace_id=str(invocation.trace_id),
            input_sha256=checksum,
            runtime_code_sha256=checksum,
            output={
                "contract_id": invocation.capability,
                "ledger_sha256": checksum,
                "summary": {
                    "total_rows": 3,
                    "truncated": False,
                    "duplicate_rows": 0,
                    "out_of_period_rows": 0,
                    "unbalanced_entries": 0,
                },
                "candidates": [],
                "ok": True,
            },
        )


class _FakeCursor:
    def __init__(self) -> None:
        self.rows: list[tuple[str, object]] = []

    def execute(self, sql: str, params: object = None) -> None:  # noqa: ANN001
        self.rows.append((sql, params))


def _capability_plugins() -> dict[str, str]:
    return {
        "audit.ledger.validate": "audit.ledger-quality",
        "quant.research-note.draft": "quant.research-note-draft",
    }


def _default_intents() -> list[dict]:
    return [
        {
            "slot_key": "audit.ledger.validate",
            "capability": "audit.ledger.validate",
            "side_effects": "read_only",
            "isolation": "isolated_subprocess",
            "policy_decision": "allowed",
            "status": "policy_allowed",
        },
        {
            "slot_key": "quant.research-note.draft",
            "capability": "quant.research-note.draft",
            "side_effects": "read_only",
            "isolation": "isolated_subprocess",
            "policy_decision": "allowed",
            "status": "policy_allowed",
        },
    ]


def _default_nodes() -> list[dict]:
    return [
        {"slot_key": "audit.ledger.validate", "ordinal": 0, "expected_output": ["audit-quality-candidates"]},
        {"slot_key": "quant.research-note.draft", "ordinal": 1, "expected_output": ["experiment-evaluation"]},
    ]


def _default_bindings() -> list[dict]:
    return [
        {
            "producer_slot": "audit.ledger.validate",
            "consumer_slot": "quant.research-note.draft",
            "relation_type": "bridge",
            "bind_mode": "bridge_ref",
            "contract_ref": "contracts/jsonschema/audit-quality-candidates@1",
        }
    ]


def _ledger_input_source(tmp_path: Path) -> InputSource:
    artifact = tmp_path / "ledger.json"
    artifact.write_text(json.dumps({"ledger": {"rows": 3}}), encoding="utf-8")
    return InputSource(
        capability="audit.ledger.validate",
        artifact=ArtifactInput(
            artifact_id=uuid4(),
            tenant_id=_TENANT,
            uri=artifact.resolve().as_uri(),
            media_type="application/json",
            sha256=hashlib.sha256(artifact.read_bytes()).hexdigest(),
            size_bytes=artifact.stat().st_size,
            classification="audit_ledger",
        ),
        payload={"ledger": {"artifact": artifact.resolve().as_uri(), "sha256": ""}},
    )


def _executor(
    *,
    runtime: _FakeRuntime | None = None,
    policy: PolicyEngine | None = None,
    intents: list[dict] | None = None,
    nodes: list[dict] | None = None,
    bindings: list[dict] | None = None,
    chain_key: str | None = None,
) -> IsolatedChainExecutor:
    chain_key = chain_key or "chain-" + uuid4().hex[:16]
    return IsolatedChainExecutor(
        chain_key=chain_key,
        chain_id=uuid4(),
        tenant_id=_TENANT,
        trace_id=str(_TRACE),
        planner_version="1.0.0",
        chain_checksum="0" * 64,
        policy=policy or PolicyEngine(
            allow=["topology.chain.execute", "topology.chain.execute.isolated"]
        ),
        nodes=nodes if nodes is not None else _default_nodes(),
        intents=intents if intents is not None else _default_intents(),
        port_bindings=bindings if bindings is not None else _default_bindings(),
        runtime=runtime or _FakeRuntime(),
        staging_root=Path("."),
        idempotency_key="unit-iso",
        capability_plugins=_capability_plugins(),
    )


# -- ISO gate (fail-closed, zero child calls) --------------------------------


def test_iso_gate_blocks_when_an_intent_is_not_read_only() -> None:
    bad = _executor()
    bad.intents[1]["side_effects"] = "write_data"
    with pytest.raises(IsolatedGateError, match="not read_only"):
        bad.iso_gate()
    assert bad.runtime.calls == []


def test_iso_gate_blocks_when_an_intent_is_not_isolated_subprocess() -> None:
    bad = _executor()
    bad.intents[0]["isolation"] = "shared_memory"
    with pytest.raises(IsolatedGateError, match="not isolated_subprocess"):
        bad.iso_gate()
    assert bad.runtime.calls == []


def test_iso_gate_blocks_unverified_capability() -> None:
    bad = _executor()
    bad.intents[0]["capability"] = "cap.unknown.tool"
    with pytest.raises(IsolatedGateError, match="not a verified built-in plugin"):
        bad.iso_gate()
    assert bad.runtime.calls == []


def test_iso_gate_requires_both_policies() -> None:
    only_execute = PolicyEngine(allow=["topology.chain.execute"])
    denied = _executor(policy=only_execute)
    with pytest.raises(IsolatedGateError, match="topology.chain.execute.isolated"):
        denied.iso_gate()


def test_iso_gate_blocks_when_approval_is_still_pending() -> None:
    pending = _executor()
    pending.intents[0]["policy_decision"] = "requires_approval"
    pending.intents[0]["status"] = "materialized"
    with pytest.raises(IsolatedGateError, match="approval pending"):
        pending.iso_gate()
    assert pending.runtime.calls == []


def test_iso_gate_blocks_denied_intent() -> None:
    denied = _executor()
    denied.intents[1]["policy_decision"] = "denied"
    denied.intents[1]["status"] = "denied"
    with pytest.raises(IsolatedGateError, match="intent denied"):
        denied.iso_gate()


# -- governed input verification ----------------------------------------------


def _artifact_input(tmp_path: Path, name: str, raw: bytes, *, sha256: str | None = None, size: int | None = None) -> ArtifactInput:
    path = tmp_path / name
    path.write_bytes(raw)
    return ArtifactInput(
        artifact_id=uuid4(),
        tenant_id=_TENANT,
        uri=path.resolve().as_uri(),
        media_type="application/json",
        sha256=sha256 if sha256 is not None else hashlib.sha256(raw).hexdigest(),
        size_bytes=size if size is not None else len(raw),
        classification="audit_ledger",
    )


def test_verify_input_source_rejects_stale_sha256(tmp_path: Path) -> None:
    raw = b'{"x": 1}'
    source = InputSource(
        capability="cap.audit.ledger-quality",
        artifact=_artifact_input(tmp_path, "in.json", raw, sha256="0" * 64),
        payload={"ledger": {"artifact": "locked"}},
    )
    with pytest.raises(IsolatedGateError, match="sha256 does not match"):
        verify_input_source(source, roots=(tmp_path,))


def test_verify_input_source_rejects_mismatched_size(tmp_path: Path) -> None:
    raw = b'{"x": 1}'
    source = InputSource(
        capability="cap.audit.ledger-quality",
        artifact=_artifact_input(tmp_path, "in.json", raw, size=len(raw) + 1),
        payload={"ledger": {"artifact": "locked"}},
    )
    with pytest.raises(IsolatedGateError, match="size does not match"):
        verify_input_source(source, roots=(tmp_path,))


def test_verify_input_source_rejects_artifact_outside_read_roots(tmp_path: Path) -> None:
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_bytes(b"{}")
    source = InputSource(
        capability="cap.audit.ledger-quality",
        artifact=ArtifactInput(
            artifact_id=uuid4(),
            tenant_id=_TENANT,
            uri=outside.resolve().as_uri(),
            media_type="application/json",
            sha256=hashlib.sha256(b"{}").hexdigest(),
            size_bytes=2,
            classification="audit_ledger",
        ),
        payload={"ledger": {"artifact": "locked"}},
    )
    with pytest.raises(IsolatedGateError, match="outside declared read roots"):
        verify_input_source(source, roots=(allowed,))


def test_verify_input_source_accepts_an_unmodified_artifact(tmp_path: Path) -> None:
    raw = b'{"rows": 1}'
    source = InputSource(
        capability="cap.audit.ledger-quality",
        artifact=_artifact_input(tmp_path, "in.json", raw),
        payload={"ledger": {"artifact": "locked"}},
    )
    verify_input_source(source, roots=(tmp_path,))
    assert source.payload["ledger"]["artifact"] == "locked"


# -- per-node execution ledger -------------------------------------------------


def _stage(executor: IsolatedChainExecutor, tmp_path: Path) -> IsolatedChainExecutor:
    """Pin staging + read roots to tmp_path so governed inputs verify in-scope."""
    executor.staging_root = Path(tmp_path)
    executor.input_roots = (Path(tmp_path).resolve(),)
    return executor


def test_execute_runs_every_node_and_writes_succeeded_entries(tmp_path: Path) -> None:
    runtime = _FakeRuntime()
    cur = _FakeCursor()
    executor = _stage(_executor(runtime=runtime), tmp_path)
    executor.input_sources = {"audit.ledger.validate": _ledger_input_source(tmp_path)}
    result = executor.execute(None, cur)
    assert result["mode"] == "isolated"
    assert result["status"] == "succeeded"
    assert [item["status"] for item in result["entries"]] == ["succeeded", "succeeded"]
    assert [item["slot_key"] for item in result["entries"]] == [
        "audit.ledger.validate",
        "quant.research-note.draft",
    ]
    assert len(cur.rows) == 2
    assert [call[0] for call in runtime.calls] == ["audit.ledger.validate", "quant.research-note.draft"]
    for row in result["entries"]:
        validate_ledger(row)


def test_failed_node_stops_later_nodes_and_never_downgrades(tmp_path: Path) -> None:
    runtime = _FakeRuntime(deny={"audit.ledger.validate"})
    cur = _FakeCursor()
    executor = _stage(_executor(runtime=runtime), tmp_path)
    executor.input_sources = {"audit.ledger.validate": _ledger_input_source(tmp_path)}
    result = executor.execute(None, cur)
    assert result["status"] == "failed"
    entries = result["entries"]
    assert len(entries) == 1  # the failing node terminated every later node
    assert entries[0]["status"] == "failed"
    assert entries[0]["mode"] == "isolated"
    assert "plugin_id" not in entries[0]
    assert entries[0]["output_checksum"] == hashlib.sha256(b"").hexdigest()
    assert [call[0] for call in runtime.calls] == ["audit.ledger.validate"]


def test_successful_entries_carry_real_run_metadata(tmp_path: Path) -> None:
    runtime = _FakeRuntime()
    cur = _FakeCursor()
    executor = _stage(_executor(runtime=runtime), tmp_path)
    executor.input_sources = {"audit.ledger.validate": _ledger_input_source(tmp_path)}
    result = executor.execute(None, cur)
    entry = result["entries"][0]
    assert entry["plugin_id"] == "audit.ledger-quality"
    assert entry["plugin_version"] == "1.0.0"
    assert len(entry["input_sha256"]) == 64
    assert len(entry["runtime_code_sha256"]) == 64
    assert len(entry["output_artifact_refs"]) == 1
    ref = entry["output_artifact_refs"][0]
    assert ref["sha256"] == entry["output_checksum"]
    assert ref["media_type"] == "application/json"

    insert_sql, params = cur.rows[0]
    assert "topology.execution_ledger" in insert_sql
    assert params[15] == "audit.ledger-quality"  # plugin_id column position


# -- bridge materializer --------------------------------------------------------


def test_bridge_materializer_derives_evaluation_with_locked_sha256(tmp_path: Path) -> None:
    producer = tmp_path / "producer-out.json"
    payload = {
        "contract_id": "audit-quality-candidates",
        "ledger_sha256": "a" * 64,
        "summary": {"total_rows": 10, "truncated": False},
        "candidates": [],
    }
    producer.write_bytes(json.dumps(payload).encode("utf-8"))
    builder = DEFAULT_BRIDGE_MATERIALIZERS["quant.research-note.draft"]
    result = builder(producer, tmp_path, _TENANT)
    evaluation = result["research_note"]["evaluation"]
    ref_path = resolve_file_uri(evaluation["uri"])
    assert ref_path.is_relative_to(tmp_path.resolve())
    assert hashlib.sha256(ref_path.read_bytes()).hexdigest() == evaluation["sha256"]
    assert evaluation["size_bytes"] == ref_path.stat().st_size
    materialized = json.loads(ref_path.read_text(encoding="utf-8"))
    assert materialized["status"] == "proposed"
    assert materialized["promotion"]["recommendation"] == "promote"
    assert materialized["experiment_id"].startswith("experiment-")


def test_bridge_materializer_rejects_producer_without_ledger_reference(tmp_path: Path) -> None:
    producer = tmp_path / "producer-out.json"
    producer.write_bytes(json.dumps({"contract_id": "audit-quality-candidates"}).encode("utf-8"))
    builder = DEFAULT_BRIDGE_MATERIALIZERS["quant.research-note.draft"]
    with pytest.raises(IsolatedGateError, match="lacks a ledger integrity reference"):
        builder(producer, tmp_path, _TENANT)