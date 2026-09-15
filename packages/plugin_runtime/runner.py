"""Isolated, allow-listed runtime for verified read-only built-in plugins."""

from __future__ import annotations

import hashlib
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from subprocess import CompletedProcess, TimeoutExpired, run
from typing import Any, Callable
from urllib.parse import unquote, urlparse
from uuid import UUID

from jsonschema import Draft202012Validator

from packages.plugin_runtime.layout import (
    VERIFIED_PLUGIN_IDS,
    declaration_dir,
    entrypoint,
    input_fingerprint,
    runtime_dir,
    runtime_module,
)
from packages.policy.engine import PolicyEngine

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class PluginRuntimeError(RuntimeError):
    """A verified plugin did not satisfy the runtime's safety boundary."""


class PluginPolicyDenied(PluginRuntimeError):
    """Policy did not authorize an invocation, so no child process was started."""


@dataclass(frozen=True, slots=True)
class ArtifactInput:
    artifact_id: UUID
    tenant_id: UUID
    uri: str
    media_type: str
    sha256: str
    size_bytes: int
    classification: str

    def as_payload(self) -> dict[str, object]:
        return {
            "artifact_id": str(self.artifact_id),
            "tenant_id": str(self.tenant_id),
            "uri": self.uri,
            "media_type": self.media_type,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "classification": self.classification,
        }


@dataclass(frozen=True, slots=True)
class PluginInvocation:
    tenant_id: UUID
    trace_id: UUID
    idempotency_key: str
    plugin_id: str
    capability: str
    payload: dict[str, Any]

    def __post_init__(self) -> None:
        if not isinstance(self.payload, dict):
            raise PluginRuntimeError("plugin payload must be an object")
        if not self.idempotency_key.strip():
            raise PluginRuntimeError("idempotency key is required")


@dataclass(frozen=True, slots=True)
class VerifiedBinding:
    plugin_id: str
    version: str
    capability: str
    timeout_seconds: int
    protocol_sha256: str
    manifest_sha256: str
    module: str
    entrypoint: str


@dataclass(frozen=True, slots=True)
class PluginExecutionResult:
    plugin_id: str
    plugin_version: str
    capability: str
    trace_id: str
    input_sha256: str
    runtime_code_sha256: str
    output: dict[str, Any]


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PluginRuntimeError(f"cannot read verified plugin descriptor: {path.name}") from exc
    if not isinstance(payload, dict):
        raise PluginRuntimeError(f"verified plugin descriptor must be an object: {path.name}")
    return payload


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _validate(payload: dict[str, Any], schema_name: str) -> None:
    schema_path = PROJECT_ROOT / "contracts" / "jsonschema" / schema_name
    schema = _read_json(schema_path)
    errors = sorted(Draft202012Validator(schema).iter_errors(payload), key=lambda error: list(error.path))
    if errors:
        raise PluginRuntimeError(f"invalid verified descriptor: {schema_name}: {errors[0].message}")


def load_verified_binding(plugin_id: str) -> VerifiedBinding:
    """Load one allow-listed built-in binding; never resolve an untrusted entrypoint.

    The allow list is code (:data:`VERIFIED_PLUGIN_IDS`), not directory
    discovery: a descriptor dropped on disk is not executable until its id is
    listed there.  The paths and module/entrypoint are then derived from the id
    by the tested convention in :mod:`packages.plugin_runtime.layout`.
    """

    if plugin_id not in VERIFIED_PLUGIN_IDS:
        raise PluginRuntimeError("plugin is not in the verified built-in allow list")
    declaration = declaration_dir(plugin_id)
    protocol_path = declaration / "plugin.protocol.json"
    manifest_path = declaration / "plugin.manifest.json"
    binding_path = declaration / "plugin.runtime-binding.json"
    module = runtime_module(plugin_id)
    target_entrypoint = entrypoint(plugin_id)
    protocol = _read_json(protocol_path)
    manifest = _read_json(manifest_path)
    binding = _read_json(binding_path)
    _validate(protocol, "unified-plugin-protocol.schema.json")
    _validate(manifest, "plugin-manifest.schema.json")
    _validate(binding, "plugin-runtime-binding.schema.json")
    protocol_hash = _sha256(protocol_path)
    manifest_hash = _sha256(manifest_path)
    if binding["protocol"] != {
        "id": protocol["id"], "version": protocol["version"], "sha256": protocol_hash,
    }:
        raise PluginRuntimeError("protocol binding does not match its verified descriptor")
    if binding["plugin"] != {
        "id": manifest["id"], "version": manifest["version"], "sha256": manifest_hash,
    }:
        raise PluginRuntimeError("manifest binding does not match its verified descriptor")
    if manifest["id"] != protocol["id"] or manifest["version"] != protocol["version"]:
        raise PluginRuntimeError("manifest and protocol identity do not match")
    if manifest["entrypoint"] != target_entrypoint:
        raise PluginRuntimeError("manifest entrypoint is not the verified built-in target")
    manifest_capabilities = list(manifest["capabilities"])
    protocol_capabilities = [item["id"] for item in protocol["capabilities"]]
    binding_capabilities = list(binding["capabilities"])
    if manifest_capabilities != protocol_capabilities or manifest_capabilities != binding_capabilities:
        raise PluginRuntimeError("capability declarations do not match across protocol binding")
    timeout = manifest["resources"]["timeout_seconds"]
    if not isinstance(timeout, int):
        raise PluginRuntimeError("verified manifest timeout is invalid")
    return VerifiedBinding(
        plugin_id=str(manifest["id"]),
        version=str(manifest["version"]),
        capability=str(manifest_capabilities[0]),
        timeout_seconds=timeout,
        protocol_sha256=protocol_hash,
        manifest_sha256=manifest_hash,
        module=module,
        entrypoint=target_entrypoint,
    )


def verified_builtin_ids() -> list[str]:
    return sorted(VERIFIED_PLUGIN_IDS)


def _bootstrap(module: str) -> str:
    return (
        "import runpy, sys; "
        "sys.path.insert(0, sys.argv[1]); "
        f"runpy.run_module({module!r}, run_name='__main__')"
    )


def _minimal_environment(allowed_roots: tuple[Path, ...]) -> dict[str, str]:
    environment = {"PYTHONUTF8": "1", "AUDIT_PLUGIN_READ_ROOTS": json.dumps([str(root) for root in allowed_roots])}
    for name in ("SYSTEMROOT", "WINDIR", "COMSPEC"):
        if value := os.environ.get(name):
            environment[name] = value
    return environment


class IsolatedPluginRuntime:
    """Runs a verified read-only plugin in a fresh Python child process."""

    def __init__(
        self,
        *,
        allowed_roots: tuple[Path, ...],
        log_sink: Callable[[dict[str, object]], None] | None = None,
    ) -> None:
        roots = tuple(root.resolve() for root in allowed_roots)
        if not roots:
            raise ValueError("at least one read root is required")
        self.allowed_roots = roots
        self.log_sink = log_sink

    def _emit_stderr(self, completed: CompletedProcess[bytes], invocation: PluginInvocation) -> None:
        """CW2: stderr is a first-class diagnostic stream (方案 8.4); it was
        captured but never saved before.  Emits a structured event to the
        configured sink; a missing sink never raises inside invoke."""
        if self.log_sink is None or not completed.stderr:
            return
        text = completed.stderr.decode("utf-8", errors="replace")
        if not text.strip():
            return
        try:
            self.log_sink(
                {
                    "stream": "stderr",
                    "producer_id": invocation.plugin_id,
                    "producer_epoch": 1,
                    "message": text[:2000],
                    "level": "warning",
                    "source": "isolated-plugin",
                }
            )
        except Exception:
            # a log sink failure must not change plugin execution semantics
            return

    def invoke(self, invocation: PluginInvocation, policy: PolicyEngine) -> PluginExecutionResult:
        binding = load_verified_binding(invocation.plugin_id)
        if invocation.capability != binding.capability:
            raise PluginRuntimeError("capability is not declared by the verified plugin")
        try:
            input_sha256_fn = input_fingerprint(invocation.plugin_id)
        except KeyError as exc:
            raise PluginRuntimeError("verified built-in input fingerprint is invalid") from exc
        input_sha256 = str(input_sha256_fn(invocation.payload))
        decision = policy.evaluate(
            invocation.capability,
            {
                "plugin_id": invocation.plugin_id,
                "artifact_sha256": input_sha256,
            },
            "read_only",
            side_effects="read_only",
        )
        if decision.decision != "allow":
            raise PluginPolicyDenied(f"policy gateway blocked capability: {decision.reason}")
        envelope = {
            "protocol": "audit-network-plugin-child-v1",
            "plugin_id": invocation.plugin_id,
            "capability": invocation.capability,
            "trace_id": str(invocation.trace_id),
            **invocation.payload,
        }
        try:
            completed: CompletedProcess[bytes] = run(
                [sys.executable, "-I", "-c", _bootstrap(binding.module), str(PROJECT_ROOT)],
                cwd=PROJECT_ROOT,
                env=_minimal_environment(self.allowed_roots),
                # ASCII-only envelope: the child runs with ``-I`` so locale codecs
                # (e.g. GBK on Chinese Windows) would mangle raw UTF-8 payloads.
                input=json.dumps(envelope, ensure_ascii=True).encode("utf-8"),
                stdout=-1,
                stderr=-1,
                check=False,
                timeout=binding.timeout_seconds,
            )
        except TimeoutExpired as exc:
            raise PluginRuntimeError("isolated plugin exceeded its declared timeout") from exc
        if completed.returncode != 0:
            self._emit_stderr(completed, invocation)
            raise PluginRuntimeError("isolated plugin process failed")
        try:
            response = json.loads(completed.stdout.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PluginRuntimeError("isolated plugin returned an invalid JSON envelope") from exc
        if not isinstance(response, dict) or response.get("ok") is not True:
            error = response.get("error") if isinstance(response, dict) else None
            message = error.get("message") if isinstance(error, dict) else "runtime_error"
            if not isinstance(message, str) or len(message) > 240:
                message = "runtime_error"
            raise PluginRuntimeError(f"isolated plugin rejected input: {message}")
        output = response.get("output", response.get("document"))
        if not isinstance(output, dict):
            raise PluginRuntimeError("isolated plugin omitted its output")
        # CW2: keep the stdout result protocol, but never drop diagnostics
        self._emit_stderr(completed, invocation)
        runtime_path = runtime_dir(invocation.plugin_id) / "runtime.py"
        return PluginExecutionResult(
            plugin_id=binding.plugin_id,
            plugin_version=binding.version,
            capability=binding.capability,
            trace_id=str(invocation.trace_id),
            input_sha256=input_sha256,
            runtime_code_sha256=_sha256(runtime_path),
            output=output,
        )


def resolve_file_uri(uri: str) -> Path:
    parsed = urlparse(uri)
    if parsed.scheme != "file" or parsed.netloc not in {"", "localhost"}:
        raise PluginRuntimeError("artifact URI must be a local file URI")
    raw_path = unquote(parsed.path)
    if raw_path.startswith("/") and len(raw_path) >= 3 and raw_path[2] == ":":
        raw_path = raw_path[1:]
    return Path(raw_path)