"""audit.foundation.ocr-extract: document text extraction adapter.

The isolated stdlib child has no OCR engine, so binary images are NOT
fabricated into text: the plugin returns a fail-closed verdict asking for a
human / dedicated OCR pipeline.  Text and JSON inputs (e.g. pre-structured
scan payloads) are normalised into document-content.
"""
from __future__ import annotations

from typing import Any

from plugins.builtin._child_common import (
    InputRejected,
    allowed_roots,
    check_identity,
    read_verified_artifact,
)

PLUGIN_ID = "audit.foundation.ocr-extract"
CAPABILITY = "audit.foundation.ocr-extract"
_TEXT_SIGNATURES = (
    b"\x89PNG", b"\xff\xd8\xff", b"GIF8", b"BM", b"\x00\x00\x01\x00", b"%PDF",
)


def _looks_binary(content: bytes) -> bool:
    return any(content.startswith(sig) for sig in _TEXT_SIGNATURES)


def handle(envelope: dict[str, Any]) -> dict[str, Any]:
    check_identity(envelope, PLUGIN_ID, CAPABILITY)
    roots = allowed_roots()
    port = envelope.get("ocr-image")
    if not isinstance(port, dict) or not isinstance(port.get("artifact"), dict):
        raise InputRejected("ocr-image reference is missing")
    artifact = port["artifact"]
    content = read_verified_artifact(artifact, roots)

    if _looks_binary(content):
        return {
            "contract_id": "ocr-text",
            "contract_version": "1.0.0",
            "artifact": {"name": str(artifact.get("uri", "")), "uri": str(artifact.get("uri", ""))},
            "language": "unknown",
            "text": "",
            "extracted": False,
            "reason": "binary image; isolated stdlib child has no OCR engine; human/OCR pipeline required",
        }

    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise InputRejected("artifact is not UTF-8 text") from exc

    payload: dict[str, Any] = {"text": text, "language": "zh"}
    stripped = text.lstrip()
    if stripped.startswith(("{", "[")):
        import json

        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            raw_text = parsed.get("text")
            if isinstance(raw_text, str):
                payload = {"text": raw_text, "language": str(parsed.get("language") or "zh")}
                for key in ("source", "page", "doc_type"):
                    if key in parsed:
                        payload[key] = parsed[key]

    extracted_text = payload.get("text", "")
    return {
        "contract_id": "ocr-text",
        "contract_version": "1.0.0",
        "artifact": {"name": str(artifact.get("uri", "")), "uri": str(artifact.get("uri", ""))},
        "language": payload.get("language", "zh"),
        "text": extracted_text,
        "extracted": bool(extracted_text.strip()),
        **({key: value for key, value in payload.items() if key not in ("text", "language")}),
    }


if __name__ == "__main__":
    from plugins.builtin._child_common import child_main

    raise SystemExit(child_main(handle))
