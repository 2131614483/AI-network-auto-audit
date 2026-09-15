from __future__ import annotations

import hashlib
from pathlib import Path
from uuid import uuid4

import pytest

from packages.plugin_runtime.runner import (
    ArtifactInput,
    IsolatedPluginRuntime,
    PluginInvocation,
    PluginPolicyDenied,
    PluginRuntimeError,
)
from packages.policy.engine import PolicyEngine


def _artifact(path: Path) -> ArtifactInput:
    content = path.read_bytes()
    return ArtifactInput(
        artifact_id=uuid4(),
        tenant_id=uuid4(),
        uri=path.resolve().as_uri(),
        media_type="text/markdown",
        sha256=hashlib.sha256(content).hexdigest(),
        size_bytes=len(content),
        classification="internal",
    )


def _invocation(path: Path) -> tuple[PluginInvocation, ArtifactInput]:
    artifact = _artifact(path)
    invocation = PluginInvocation(
        tenant_id=artifact.tenant_id,
        trace_id=uuid4(),
        idempotency_key="runtime-unit-markdown-v1",
        plugin_id="knowledge.document-ingestion",
        capability="knowledge.extract.document",
        payload={"artifact": artifact.as_payload(), "max_characters": 4096},
    )
    return invocation, artifact


def test_isolated_runtime_reads_a_declared_local_artifact_and_returns_auditable_output(tmp_path: Path) -> None:
    source = tmp_path / "memo.md"
    source.write_text("# 审计备忘\n\n可追溯的本地只读样本。\n", encoding="utf-8")

    runtime = IsolatedPluginRuntime(allowed_roots=(tmp_path,))
    invocation, _ = _invocation(source)
    result = runtime.invoke(invocation, PolicyEngine(allow=["knowledge.extract.document"]))

    assert result.plugin_id == "knowledge.document-ingestion"
    assert result.capability == "knowledge.extract.document"
    assert result.output["content"].startswith("# 审计备忘")
    assert result.output["source_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert len(result.runtime_code_sha256) == 64
    assert result.trace_id


def test_isolated_runtime_does_not_start_a_child_when_policy_denies(tmp_path: Path) -> None:
    source = tmp_path / "memo.md"
    source.write_text("拒绝前不得读取。", encoding="utf-8")
    runtime = IsolatedPluginRuntime(allowed_roots=(tmp_path,))
    invocation, _ = _invocation(source)

    with pytest.raises(PluginPolicyDenied):
        runtime.invoke(invocation, PolicyEngine(deny=["knowledge.extract.document"]))


def test_isolated_runtime_rejects_an_artifact_outside_its_declared_read_root(tmp_path: Path) -> None:
    allowed_root = tmp_path / "allowed"
    allowed_root.mkdir()
    source = tmp_path / "outside.md"
    source.write_text("越界文件。", encoding="utf-8")
    runtime = IsolatedPluginRuntime(allowed_roots=(allowed_root,))
    invocation, _ = _invocation(source)

    with pytest.raises(PluginRuntimeError, match="declared read roots"):
        runtime.invoke(invocation, PolicyEngine(allow=["knowledge.extract.document"]))


def test_isolated_runtime_runs_ledger_quality_in_a_child_process(tmp_path: Path) -> None:
    source = tmp_path / "ledger-2026-01.csv"
    source.write_text(
        "entry_id,date,account_code,description,debit_amount,credit_amount\n"
        "V-01,2026-01-05,1001,借方,1000.00,0.00\n"
        "V-01,2026-01-05,8001,贷方,0.00,900.00\n",
        encoding="utf-8",
    )
    content = source.read_bytes()
    artifact = ArtifactInput(
        artifact_id=uuid4(),
        tenant_id=uuid4(),
        uri=source.resolve().as_uri(),
        media_type="text/csv",
        sha256=hashlib.sha256(content).hexdigest(),
        size_bytes=len(content),
        classification="audit_confidential",
    )
    invocation = PluginInvocation(
        tenant_id=artifact.tenant_id,
        trace_id=uuid4(),
        idempotency_key="runtime-unit-ledger-v1",
        plugin_id="audit.ledger-quality",
        capability="audit.ledger.validate",
        payload={
            "ledger": {
                "artifact": artifact.as_payload(),
                "schema_mapping_version": "1.0.0",
                "period": "2026-01",
            }
        },
    )
    runtime = IsolatedPluginRuntime(allowed_roots=(tmp_path,))
    result = runtime.invoke(invocation, PolicyEngine(allow=["audit.ledger.validate"]))

    assert result.plugin_id == "audit.ledger-quality"
    assert result.capability == "audit.ledger.validate"
    assert result.output["contract_id"] == "audit-quality-candidates"
    assert result.output["ledger_sha256"] == artifact.sha256
    assert result.input_sha256 == artifact.sha256
    assert len(result.runtime_code_sha256) == 64
    assert any(candidate["rule_key"] == "unbalanced_entry" for candidate in result.output["candidates"])


def test_isolated_runtime_rejects_a_tampered_artifact_reference(tmp_path: Path) -> None:
    source = tmp_path / "memo.md"
    source.write_text("哈希校验。", encoding="utf-8")
    invocation, artifact = _invocation(source)
    tampered = ArtifactInput(
        artifact_id=artifact.artifact_id,
        tenant_id=artifact.tenant_id,
        uri=artifact.uri,
        media_type=artifact.media_type,
        sha256="0" * 64,
        size_bytes=artifact.size_bytes,
        classification=artifact.classification,
    )
    runtime = IsolatedPluginRuntime(allowed_roots=(tmp_path,))
    invalid = PluginInvocation(
        tenant_id=invocation.tenant_id,
        trace_id=invocation.trace_id,
        idempotency_key=invocation.idempotency_key,
        plugin_id=invocation.plugin_id,
        capability=invocation.capability,
        payload={"artifact": tampered.as_payload(), "max_characters": 4096},
    )

    with pytest.raises(PluginRuntimeError, match="sha256"):
        runtime.invoke(invalid, PolicyEngine(allow=["knowledge.extract.document"]))