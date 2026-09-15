"""audit.field.interview-record: structure interview Q&A into a
document-content note.

Consumes an interview payload (artifact-ref JSON) and emits a
document-content note with one chapter per interviewee. Points are derived
only from answered questions; unanswered ones are omitted from the notes but
counted. Read-only.
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

PLUGIN_ID = "audit.field.interview-record"
CAPABILITY = "audit.field.interview-record"
MAX_INTERVIEWS = 5_000


def handle(envelope: dict[str, Any]) -> dict[str, Any]:
    check_identity(envelope, PLUGIN_ID, CAPABILITY)
    port = envelope.get("interview-input")
    if not isinstance(port, dict) or not isinstance(port.get("artifact"), dict):
        raise InputRejected("interview-input reference is missing")
    artifact = port["artifact"]
    roots = allowed_roots()
    read_verified_artifact(artifact, roots)
    payload = read_artifact_json(artifact, roots)
    interviews = payload.get("interviews")
    if not isinstance(interviews, list):
        raise InputRejected("interview-input requires interviews array")

    chapters = []
    total_q = 0
    answered = 0
    for index, interview in enumerate(interviews[:MAX_INTERVIEWS], start=1):
        if not isinstance(interview, dict):
            continue
        qa = interview.get("qa")
        if not isinstance(qa, list):
            continue
        points = []
        for item in qa:
            if not isinstance(item, dict):
                continue
            total_q += 1
            answer = item.get("a")
            if answer:
                answered += 1
                points.append({"question": str(item.get("q") or ""),
                               "answer": str(answer)})
        chapters.append({
            "heading": f"{interview.get('person') or f'访谈对象{index}'}（{interview.get('role') or '未注明'}）",
            "body": {"date": str(interview.get("date") or ""),
                     "answered_points": points,
                     "unanswered_count": len(qa) - len(points)},
        })
    if not chapters:
        raise InputRejected("interview-input contains no interviews with Q&A")

    return {
        "contract_id": "document-content", "contract_version": "1.0.0",
        "artifact": {
            "title": f"访谈记录（{payload.get('period') or 'period'}）",
            "summary": {"interviewees": len(chapters), "total_questions": total_q,
                        "answered": answered},
            "chapters": chapters,
        },
        "language": "zh-CN",
    }


if __name__ == "__main__":
    raise SystemExit(child_main(handle))
