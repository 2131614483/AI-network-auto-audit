"""audit.field.meeting-minutes: distil meeting audio segments into a
document-content minutes note.

Consumes a meeting payload (artifact-ref JSON) with speaker segments and
emits a document-content note whose key points are sentences containing
decision markers (决议/决定/待办/要求). Nothing else is invented. Read-only.
"""
from __future__ import annotations

from typing import Any

from plugins.builtin._child_common import (
    InputRejected,
    allowed_roots,
    check_identity,
    child_main,
    read_artifact_json,
    read_verified_artifact,
)

PLUGIN_ID = "audit.field.meeting-minutes"
CAPABILITY = "audit.field.meeting-minutes"
MAX_SEGMENTS = 20_000
_MARKERS = ("决议", "决定", "待办", "要求", "明确")


def handle(envelope: dict[str, Any]) -> dict[str, Any]:
    check_identity(envelope, PLUGIN_ID, CAPABILITY)
    port = envelope.get("meeting-audio")
    if not isinstance(port, dict) or not isinstance(port.get("artifact"), dict):
        raise InputRejected("meeting-audio reference is missing")
    artifact = port["artifact"]
    roots = allowed_roots()
    read_verified_artifact(artifact, roots)
    payload = read_artifact_json(artifact, roots)
    segments = payload.get("segments")
    if not isinstance(segments, list):
        raise InputRejected("meeting-audio requires segments array")

    transcript = []
    key_points = []
    for segment in segments[:MAX_SEGMENTS]:
        if not isinstance(segment, dict):
            continue
        speaker = str(segment.get("speaker") or "")
        text = str(segment.get("text") or "")
        if not text.strip():
            continue
        transcript.append({"speaker": speaker, "text": text, "ts": str(segment.get("ts") or "")})
        if any(marker in text for marker in _MARKERS):
            key_points.append({"speaker": speaker, "text": text})
    if not transcript:
        raise InputRejected("meeting-audio contains no transcript text")

    return {
        "contract_id": "document-content", "contract_version": "1.0.0",
        "artifact": {
            "title": f"会议纪要（{payload.get('meeting_id') or 'meeting'}）",
            "meeting_id": str(payload.get("meeting_id") or ""),
            "summary": {"segments": len(transcript), "key_points": len(key_points)},
            "key_points": key_points,
            "transcript": transcript,
        },
        "language": "zh-CN",
    }


if __name__ == "__main__":
    raise SystemExit(child_main(handle))
