"""Isolated implementation for the verified read-only alert-correlation plugin."""

from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

MAX_INPUT_BYTES = 100 * 1024 * 1024
MAX_ALERTS = 500_000
DEFAULT_GROUPING_KEYS = ("affected_system",)
SEVERITY_RANK = {"high": 3, "medium": 2, "low": 1}
SEVERITY_ALIASES = {"高": "high", "中": "medium", "低": "low", "严重": "high", "warning": "medium", "info": "low"}
TIMESTAMP_FORMATS = ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d")


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
        raise InputRejected("alert set URI is missing")
    parsed = urlparse(uri)
    if parsed.scheme != "file" or parsed.netloc not in {"", "localhost"}:
        raise InputRejected("alert set URI must be local")
    raw_path = unquote(parsed.path)
    if raw_path.startswith("/") and len(raw_path) >= 3 and raw_path[2] == ":":
        raw_path = raw_path[1:]
    try:
        resolved = Path(raw_path).resolve(strict=True)
    except OSError as exc:
        raise InputRejected("alert set file is unavailable") from exc
    if not resolved.is_file():
        raise InputRejected("alert set is not a regular file")
    try:
        next(root for root in roots if resolved.is_relative_to(root))
    except StopIteration as exc:
        raise InputRejected("alert set is outside declared read roots") from exc
    return resolved


def _parse_timestamp(raw: object) -> datetime | None:
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    for fmt in TIMESTAMP_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _severity(raw: object) -> str:
    if not isinstance(raw, str):
        return "medium"
    text = raw.strip().lower()
    if text in SEVERITY_ALIASES:
        return SEVERITY_ALIASES[text]
    if text in SEVERITY_RANK:
        return text
    return "medium"


def _alert_id(alert: dict[str, Any]) -> str:
    for key in ("ticket_id", "alert_id", "id", "event_id"):
        value = alert.get(key)
        if isinstance(value, str) and value:
            return value
    return json.dumps(alert, ensure_ascii=False, sort_keys=True)


def handle(envelope: dict[str, Any]) -> dict[str, Any]:
    if envelope.get("protocol") != "audit-network-plugin-child-v1":
        raise InputRejected("unsupported child protocol")
    if envelope.get("plugin_id") != "aiops.alert-correlation":
        raise InputRejected("unexpected plugin identity")
    if envelope.get("capability") != "aiops.alert.correlate":
        raise InputRejected("unexpected capability")
    alerts_input = envelope.get("alerts")
    if not isinstance(alerts_input, dict):
        raise InputRejected("alert event set is missing")
    artifact = alerts_input.get("artifact")
    if not isinstance(artifact, dict):
        raise InputRejected("alert artifact reference is missing")
    path = _resolve_file(artifact.get("uri"), _allowed_roots())
    content = path.read_bytes()
    if len(content) > MAX_INPUT_BYTES:
        raise InputRejected("alert set exceeds local read budget")
    expected_size = artifact.get("size_bytes")
    if not isinstance(expected_size, int) or expected_size != len(content):
        raise InputRejected("alert set size does not match reference")
    expected_hash = artifact.get("sha256")
    actual_hash = hashlib.sha256(content).hexdigest()
    if not isinstance(expected_hash, str) or actual_hash.lower() != expected_hash.lower():
        raise InputRejected("alert set sha256 does not match reference")
    window_minutes = alerts_input.get("window_minutes")
    if not isinstance(window_minutes, int) or not (1 <= window_minutes <= 10080):
        raise InputRejected("window_minutes is outside the fixed budget")
    max_candidates = alerts_input.get("max_candidates")
    if not isinstance(max_candidates, int) or not (1 <= max_candidates <= 1000):
        raise InputRejected("max_candidates is outside the fixed budget")
    grouping_keys = alerts_input.get("grouping_keys") or list(DEFAULT_GROUPING_KEYS)
    if not isinstance(grouping_keys, list) or len(grouping_keys) > 4 or len(set(grouping_keys)) != len(grouping_keys):
        raise InputRejected("grouping_keys are outside the fixed budget")

    try:
        parsed = json.loads(content.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InputRejected("alert set must be UTF-8 JSON") from exc
    if not isinstance(parsed, list):
        raise InputRejected("alert set must be a JSON array of events")
    if len(parsed) > MAX_ALERTS:
        raise InputRejected("alert set exceeds the fixed event budget")

    events: list[tuple[datetime, dict[str, Any], str, str, str]] = []
    for alert in parsed:
        if not isinstance(alert, dict):
            raise InputRejected("each alert must be an object")
        timestamp = _parse_timestamp(alert.get("timestamp"))
        if timestamp is None:
            raise InputRejected("each alert must carry a parseable timestamp")
        alert_ref = _alert_id(alert)
        severity = _severity(alert.get("severity"))
        group_values: list[str] = []
        for key in grouping_keys:
            value = alert.get(key)
            group_values.append(str(value).strip() if value is not None else "")
        correlation_key = "|".join(f"{key}={value}" for key, value in zip(grouping_keys, group_values))
        events.append((timestamp, alert, alert_ref, severity, correlation_key))

    events.sort(key=lambda item: item[0])
    incidents: list[dict[str, Any]] = []
    window = timedelta(minutes=window_minutes)
    for timestamp, _alert, alert_ref, severity, correlation_key in events:
        placed = False
        for incident in incidents:
            if incident["correlation_key"] != correlation_key:
                continue
            if abs(timestamp - incident["_last_seen"]) <= window:
                incident["_last_seen"] = max(incident["_last_seen"], timestamp)
                incident["_first_seen"] = min(incident["_first_seen"], timestamp)
                incident["alert_refs"].append(alert_ref)
                if SEVERITY_RANK[severity] > SEVERITY_RANK[incident["_severity"]]:
                    incident["_severity"] = severity
                placed = True
                break
        if not placed:
            incidents.append(
                {
                    "incident_id": f"INC-{len(incidents) + 1:04d}",
                    "correlation_key": correlation_key,
                    "alert_refs": [alert_ref],
                    "_first_seen": timestamp,
                    "_last_seen": timestamp,
                    "_severity": severity,
                }
            )

    candidates: list[dict[str, Any]] = []
    truncated = len(incidents) > max_candidates
    for incident in incidents[:max_candidates]:
        count = len(incident["alert_refs"])
        span_hours = (incident["_last_seen"] - incident["_first_seen"]).total_seconds() / 3600.0
        confidence = min(1.0, 0.4 + 0.1 * count + 0.1 * max(0.0, 1.0 - span_hours / max(1.0, window_minutes / 60.0)))
        candidates.append(
            {
                "incident_id": incident["incident_id"],
                "correlation_key": incident["correlation_key"],
                "alert_refs": incident["alert_refs"],
                "first_seen": incident["_first_seen"].isoformat(),
                "last_seen": incident["_last_seen"].isoformat(),
                "severity": incident["_severity"],
                "count": count,
                "confidence": round(confidence, 6),
                "evidence": {
                    "window_minutes": window_minutes,
                    "grouping_keys": grouping_keys,
                    "source_sha256": actual_hash,
                },
            }
        )
    return {
        "contract_id": "incident-candidate-set",
        "contract_version": "1.0.0",
        "alert_set_sha256": actual_hash,
        "window_minutes": window_minutes,
        "summary": {
            "alerts_processed": len(events),
            "candidate_count": len(candidates),
            "truncated": truncated,
        },
        "candidates": candidates,
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
