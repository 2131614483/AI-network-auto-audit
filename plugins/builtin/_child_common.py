"""Shared helpers for audit network child runtimes (stdlib only).

The child runs with ``python -I`` so it must not import project packages.
Each runtime duplicates the small root/sha verification block locally; this
module is copied per runtime to stay fully self-contained.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

MAX_INPUT_BYTES = 64 * 1024 * 1024


class InputRejected(ValueError):
    pass


def allowed_roots() -> tuple[Path, ...]:
    try:
        raw = json.loads(os.environ["AUDIT_PLUGIN_READ_ROOTS"])
    except (KeyError, json.JSONDecodeError) as exc:
        raise InputRejected("read roots are unavailable") from exc
    if not isinstance(raw, list) or not raw:
        raise InputRejected("read roots are invalid")
    return tuple(Path(str(item)).resolve() for item in raw)


def resolve_artifact(artifact: object, roots: tuple[Path, ...]) -> Path:
    if not isinstance(artifact, dict):
        raise InputRejected("artifact reference is missing")
    uri = artifact.get("uri")
    if not isinstance(uri, str):
        raise InputRejected("artifact URI is missing")
    parsed = urlparse(uri)
    if parsed.scheme != "file" or parsed.netloc not in {"", "localhost"}:
        raise InputRejected("artifact URI must be local")
    raw_path = unquote(parsed.path)
    if raw_path.startswith("/") and len(raw_path) >= 3 and raw_path[2] == ":":
        raw_path = raw_path[1:]
    try:
        resolved = Path(raw_path).resolve(strict=True)
    except OSError as exc:
        raise InputRejected("artifact file is unavailable") from exc
    if not resolved.is_file():
        raise InputRejected("artifact is not a regular file")
    try:
        next(root for root in roots if resolved.is_relative_to(root))
    except StopIteration as exc:
        raise InputRejected("artifact is outside declared read roots") from exc
    return resolved


def read_verified_artifact(artifact: object, roots: tuple[Path, ...]) -> bytes:
    path = resolve_artifact(artifact, roots)
    content = path.read_bytes()
    if len(content) > MAX_INPUT_BYTES:
        raise InputRejected("artifact exceeds local read budget")
    size = artifact.get("size_bytes")
    if not isinstance(size, int) or size != len(content):
        raise InputRejected("artifact size does not match reference")
    digest = artifact.get("sha256")
    actual = hashlib.sha256(content).hexdigest()
    if not isinstance(digest, str) or actual.lower() != digest.lower():
        raise InputRejected("artifact sha256 does not match reference")
    return content


def read_artifact_json(artifact: object, roots: tuple[Path, ...]) -> Any:
    content = read_verified_artifact(artifact, roots)
    try:
        return json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InputRejected("artifact is not valid UTF-8 JSON") from exc


def read_artifact_csv(artifact: object, roots: tuple[Path, ...]) -> list[dict[str, str]]:
    import csv
    import io as _io

    content = read_verified_artifact(artifact, roots)
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise InputRejected("artifact is not UTF-8 CSV") from exc
    reader = csv.DictReader(_io.StringIO(text))
    return [dict(row) for row in reader]


def child_main(handle) -> int:  # type: ignore[no-untyped-def]
    import sys

    try:
        envelope = json.loads(sys.stdin.read())
        if not isinstance(envelope, dict):
            raise InputRejected("child envelope must be an object")
        output = handle(envelope)
        sys.stdout.buffer.write(json.dumps({"ok": True, "output": output}, ensure_ascii=False).encode("utf-8"))
        return 0
    except (InputRejected, json.JSONDecodeError) as exc:
        sys.stdout.buffer.write(
            json.dumps({"ok": False, "error": {"code": "invalid_input", "message": str(exc)}}, ensure_ascii=False).encode("utf-8")
        )
        return 0


def check_identity(envelope: dict[str, Any], plugin_id: str, capability: str) -> None:
    if envelope.get("protocol") != "audit-network-plugin-child-v1":
        raise InputRejected("unsupported child protocol")
    if envelope.get("plugin_id") != plugin_id:
        raise InputRejected("unexpected plugin identity")
    if envelope.get("capability") != capability:
        raise InputRejected("unexpected capability")
