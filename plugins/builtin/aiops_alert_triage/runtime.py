"""Isolated implementation for the verified read-only alert-triage plugin."""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

MAX_INPUT_BYTES = 10 * 1024 * 1024
SEVERITY_RANK = {"critical": 4, "high": 3, "medium": 2, "low": 1, "info": 0}
TIMESTAMP_FORMATS = (
    "%Y-%m-%dT%H:%M:%S.%f%z",
    "%Y-%m-%dT%H:%M:%S%z",
    "%Y-%m-%dT%H:%M:%S.%f",
    "%Y-%m-%d %H:%M:%S.%f",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d",
)


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
        raise InputRejected("alert event URI is missing")
    parsed = urlparse(uri)
    if parsed.scheme != "file" or parsed.netloc not in {"", "localhost"}:
        raise InputRejected("alert event URI must be local")
    raw_path = unquote(parsed.path)
    if raw_path.startswith("/") and len(raw_path) >= 3 and raw_path[2] == ":":
        raw_path = raw_path[1:]
    try:
        resolved = Path(raw_path).resolve(strict=True)
    except OSError as exc:
        raise InputRejected("alert event file is unavailable") from exc
    if not resolved.is_file():
        raise InputRejected("alert event is not a regular file")
    try:
        next(root for root in roots if resolved.is_relative_to(root))
    except StopIteration as exc:
        raise InputRejected("alert event is outside declared read roots") from exc
    return resolved


def _parse_timestamp(raw: object) -> str | None:
    from datetime import datetime  # local import keeps the child runtime lean

    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text:
        return None
    for fmt in TIMESTAMP_FORMATS:
        try:
            datetime.strptime(text, fmt)
            return text
        except ValueError:
            continue
    return None


def _severity(raw: object) -> str:
    if not isinstance(raw, str):
        raise InputRejected("alert event severity is missing")
    text = raw.strip().lower()
    if text not in SEVERITY_RANK:
        raise InputRejected("alert event severity is outside the fixed enum")
    return text


def handle(envelope: dict[str, Any]) -> dict[str, Any]:
    if envelope.get("protocol") != "audit-network-plugin-child-v1":
        raise InputRejected("unsupported child protocol")
    if envelope.get("plugin_id") != "aiops.alert-triage":
        raise InputRejected("unexpected plugin identity")
    if envelope.get("capability") != "aiops.alert.triage":
        raise InputRejected("unexpected capability")
    triage_input = envelope.get("triage")
    if not isinstance(triage_input, dict):
        raise InputRejected("alert triage input is missing")
    artifact = triage_input.get("alert_event")
    if not isinstance(artifact, dict):
        raise InputRejected("alert event artifact reference is missing")
    path = _resolve_file(artifact.get("uri"), _allowed_roots())
    content = path.read_bytes()
    if len(content) > MAX_INPUT_BYTES:
        raise InputRejected("alert event exceeds local read budget")
    expected_size = artifact.get("size_bytes")
    if not isinstance(expected_size, int) or expected_size != len(content):
        raise InputRejected("alert event size does not match reference")
    expected_hash = artifact.get("sha256")
    actual_hash = hashlib.sha256(content).hexdigest()
    if not isinstance(expected_hash, str) or actual_hash.lower() != expected_hash.lower():
        raise InputRejected("alert event sha256 does not match reference")

    try:
        event = json.loads(content.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InputRejected("alert event must be UTF-8 JSON") from exc
    if not isinstance(event, dict):
        raise InputRejected("alert event must be a single JSON object")
    if event.get("contract_id") != "alert-event" or event.get("contract_version") != "1.0.0":
        raise InputRejected("alert event contract version is unsupported")
    event_id = event.get("event_id")
    if not isinstance(event_id, str) or not event_id:
        raise InputRejected("alert event event_id is missing")
    fingerprint = event.get("fingerprint")
    if not isinstance(fingerprint, str) or len(fingerprint) != 64:
        raise InputRejected("alert event fingerprint must be a 64-char sha256")
    if fingerprint.lower() != fingerprint:
        fingerprint = fingerprint.lower()
    occurred_at = _parse_timestamp(event.get("occurred_at"))
    if occurred_at is None:
        raise InputRejected("alert event occurred_at must be parseable")
    severity = _severity(event.get("severity"))
    summary = event.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        raise InputRejected("alert event summary is missing")
    source = event.get("source")
    if not isinstance(source, str) or not source:
        raise InputRejected("alert event source is missing")

    declared_keys = event.get("grouping_keys")
    if declared_keys is None:
        declared_keys = []
    if not isinstance(declared_keys, list) or len(declared_keys) > 4 or len(set(declared_keys)) != len(declared_keys):
        raise InputRejected("alert event grouping_keys are outside the fixed budget")
    grouping_key_parts: list[str] = []
    for key in declared_keys:
        value = event.get(key)
        grouping_key_parts.append(f"{key}={str(value).strip() if value is not None else ''}")
    if not grouping_key_parts:
        grouping_key_parts.append(f"source={source}")
    grouping_key = "|".join(grouping_key_parts)

    labels = event.get("labels")
    if not isinstance(labels, dict):
        labels = {}
    case = str(labels.get("case") or "") if labels else ""
    affected_system = f"{source}"
    if case:
        affected_system = f"{case}/{source}"
    proposal_id = hashlib.sha256(f"{fingerprint}|{grouping_key}".encode("utf-8")).hexdigest()[:16]
    evidence_refs: list[str] = []
    uri = artifact.get("uri")
    if isinstance(uri, str):
        evidence_refs.append(uri)

    return {
        "contract_id": "incident-proposal",
        "contract_version": "1.0.0",
        "proposal_id": proposal_id,
        "alert_fingerprint": fingerprint,
        "status": "proposed",
        "triage": {
            "grouping_key": grouping_key,
            "severity": severity,
            "affected_system": affected_system,
            "summary": summary,
            "evidence_refs": evidence_refs,
            "review_reason": "单源告警，需结合故障工单与日志人工复核",
        },
        "constraints": {
            "no_execution": True,
            "no_playbook": True,
            "requires_human_approval": True,
        },
        "provenance": {
            "event_id": event_id,
            "occurred_at": occurred_at,
            "source_sha256": actual_hash,
            "plugin": "aiops.alert-triage@0.1.0",
        },
    }


def main() -> int:
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


if __name__ == "__main__":
    raise SystemExit(main())
