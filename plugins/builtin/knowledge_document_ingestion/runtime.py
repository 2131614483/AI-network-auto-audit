"""Child-process entrypoint for the fixed local document-ingestion implementation."""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

MAX_INPUT_BYTES = 50 * 1024 * 1024
SUPPORTED_SUFFIXES = frozenset({".md", ".txt", ".json", ".csv"})


class InputRejected(ValueError):
    """The parent passed an input outside the fixed read-only contract."""


def _allowed_roots() -> tuple[Path, ...]:
    try:
        raw_roots = json.loads(os.environ["AUDIT_PLUGIN_READ_ROOTS"])
    except (KeyError, json.JSONDecodeError) as exc:
        raise InputRejected("read roots are unavailable") from exc
    if not isinstance(raw_roots, list) or not raw_roots:
        raise InputRejected("read roots are invalid")
    return tuple(Path(str(raw)).resolve() for raw in raw_roots)


def _resolve_file(uri: object, roots: tuple[Path, ...]) -> Path:
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
    if resolved.suffix.lower() not in SUPPORTED_SUFFIXES:
        raise InputRejected("artifact media type is not supported by this plugin")
    return resolved


def handle(envelope: dict[str, Any]) -> dict[str, Any]:
    if envelope.get("protocol") != "audit-network-plugin-child-v1":
        raise InputRejected("unsupported child protocol")
    if envelope.get("plugin_id") != "knowledge.document-ingestion":
        raise InputRejected("unexpected plugin identity")
    if envelope.get("capability") != "knowledge.extract.document":
        raise InputRejected("unexpected capability")
    artifact = envelope.get("artifact")
    if not isinstance(artifact, dict):
        raise InputRejected("artifact reference is missing")
    path = _resolve_file(artifact.get("uri"), _allowed_roots())
    content = path.read_bytes()
    expected_size = artifact.get("size_bytes")
    if not isinstance(expected_size, int) or expected_size != len(content):
        raise InputRejected("artifact size does not match reference")
    if len(content) > MAX_INPUT_BYTES:
        raise InputRejected("artifact exceeds local read budget")
    expected_hash = artifact.get("sha256")
    actual_hash = hashlib.sha256(content).hexdigest()
    if not isinstance(expected_hash, str) or actual_hash.lower() != expected_hash.lower():
        raise InputRejected("artifact sha256 does not match reference")
    max_characters = envelope.get("max_characters")
    if not isinstance(max_characters, int) or not 1 <= max_characters <= 250_000:
        raise InputRejected("max_characters is invalid")
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise InputRejected("artifact must be UTF-8 text in Phase 1") from exc
    extracted = text[:max_characters]
    return {
        "contract_id": "document-content",
        "contract_version": "1.0.0",
        "source_uri": artifact["uri"],
        "source_sha256": actual_hash,
        "media_type": artifact.get("media_type"),
        "content": extracted,
        "characters": len(extracted),
        "truncated": len(text) > len(extracted),
        "locator": {"char_start": 0, "char_end": len(extracted)},
    }


def main() -> int:
    try:
        envelope = json.loads(sys.stdin.read())
        if not isinstance(envelope, dict):
            raise InputRejected("child envelope must be an object")
        document = handle(envelope)
        sys.stdout.buffer.write(json.dumps({"ok": True, "document": document}, ensure_ascii=False).encode("utf-8"))
        return 0
    except (InputRejected, json.JSONDecodeError) as exc:
        sys.stdout.buffer.write(json.dumps({"ok": False, "error": {"code": "invalid_input", "message": str(exc)}}, ensure_ascii=False).encode("utf-8"))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
