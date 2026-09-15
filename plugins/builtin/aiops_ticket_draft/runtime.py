"""Isolated implementation for the verified read-only aiops ticket-draft plugin.

R3 受审批输出：只读已确认 incident-proposal 事件，按固定目标系统生成确定性、
可追溯的外部工单草稿。草稿永不自动发送：实际发送必须满足目标系统白名单与
人工审批；插件本身无网络、不写基础设施，落库由治理服务在人工审批后完成。
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

MAX_INPUT_BYTES = 10 * 1024 * 1024
DEFAULT_TARGET_SYSTEM = "jira"
MAX_INCIDENTS_LIMIT = 64
MAX_EVIDENCE_REFS = 64
SEVERITY_TO_PRIORITY = {"critical": "P1", "high": "P2", "medium": "P3", "low": "P4"}


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
        raise InputRejected("incident-proposal URI is missing")
    parsed = urlparse(uri)
    if parsed.scheme != "file" or parsed.netloc not in {"", "localhost"}:
        raise InputRejected("incident-proposal URI must be local")
    raw_path = unquote(parsed.path)
    if raw_path.startswith("/") and len(raw_path) >= 3 and raw_path[2] == ":":
        raw_path = raw_path[1:]
    try:
        resolved = Path(raw_path).resolve(strict=True)
    except OSError as exc:
        raise InputRejected("incident-proposal file is unavailable") from exc
    if not resolved.is_file():
        raise InputRejected("incident-proposal is not a regular file")
    try:
        next(root for root in roots if resolved.is_relative_to(root))
    except StopIteration as exc:
        raise InputRejected("incident-proposal is outside declared read roots") from exc
    return resolved


def _timestamp_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _validated_target_system(raw: object) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise InputRejected("target_system is missing")
    lowered = raw.strip().lower()
    if not lowered:
        raise InputRejected("target_system is empty")
    if any(not (char.isalnum() and char.isascii()) for char in lowered.replace("_", "").replace("-", "").replace(".", "")):
        raise InputRejected("target_system must be a fixed ascii identifier")
    return lowered


def handle(envelope: dict[str, Any]) -> dict[str, Any]:
    if envelope.get("protocol") != "audit-network-plugin-child-v1":
        raise InputRejected("unsupported child protocol")
    if envelope.get("plugin_id") != "aiops.ticket-draft":
        raise InputRejected("unexpected plugin identity")
    if envelope.get("capability") != "aiops.ticket.draft":
        raise InputRejected("unexpected capability")
    ticket_input = envelope.get("ticket")
    if not isinstance(ticket_input, dict):
        raise InputRejected("ticket input is missing")
    artifact = ticket_input.get("incident_proposal")
    if not isinstance(artifact, dict):
        raise InputRejected("incident-proposal artifact reference is missing")
    path = _resolve_file(artifact.get("uri"), _allowed_roots())
    content = path.read_bytes()
    if len(content) > MAX_INPUT_BYTES:
        raise InputRejected("incident-proposal exceeds local read budget")
    expected_size = artifact.get("size_bytes")
    if not isinstance(expected_size, int) or expected_size != len(content):
        raise InputRejected("incident-proposal size does not match reference")
    expected_hash = artifact.get("sha256")
    actual_hash = hashlib.sha256(content).hexdigest()
    if not isinstance(expected_hash, str) or actual_hash.lower() != expected_hash.lower():
        raise InputRejected("incident-proposal sha256 does not match reference")

    target_system = _validated_target_system(ticket_input.get("target_system", DEFAULT_TARGET_SYSTEM))

    try:
        proposal = json.loads(content.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InputRejected("incident-proposal must be UTF-8 JSON") from exc
    if not isinstance(proposal, dict):
        raise InputRejected("incident-proposal must be a single JSON object")
    if proposal.get("contract_id") != "incident-proposal" or proposal.get("contract_version") != "1.0.0":
        raise InputRejected("incident-proposal contract version is unsupported")
    if proposal.get("status") != "proposed":
        raise InputRejected("incident-proposal is not confirmed")
    proposal_id = proposal.get("proposal_id")
    if not isinstance(proposal_id, str) or not proposal_id:
        raise InputRejected("incident-proposal proposal_id is missing")
    triage = proposal.get("triage")
    if not isinstance(triage, dict):
        raise InputRejected("incident-proposal triage is missing")
    severity = triage.get("severity")
    if severity not in SEVERITY_TO_PRIORITY:
        raise InputRejected("incident-proposal severity is outside the fixed enum")
    affected_system = triage.get("affected_system")
    if not isinstance(affected_system, str) or not affected_system.strip():
        raise InputRejected("incident-proposal affected_system is missing")
    summary = triage.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        raise InputRejected("incident-proposal summary is missing")
    review_reason = triage.get("review_reason")
    if not isinstance(review_reason, str) or not review_reason.strip():
        raise InputRejected("incident-proposal review_reason is missing")
    grouping_key = triage.get("grouping_key")
    if not isinstance(grouping_key, str) or not grouping_key.strip():
        raise InputRejected("incident-proposal grouping_key is missing")
    raw_evidence = triage.get("evidence_refs")
    if raw_evidence is None:
        raw_evidence = []
    if (
        not isinstance(raw_evidence, list)
        or len(raw_evidence) > MAX_EVIDENCE_REFS
        or any(not isinstance(ref, str) or not ref for ref in raw_evidence)
    ):
        raise InputRejected("incident-proposal evidence_refs are outside the fixed budget")
    evidence_refs = list(raw_evidence)

    priority = SEVERITY_TO_PRIORITY[severity]
    incident_refs = [f"incident:{proposal_id}"]

    ticket_seed = "|".join([actual_hash, proposal_id, target_system])
    ticket_id = hashlib.sha256(ticket_seed.encode("utf-8")).hexdigest()[:16]

    title = f"【{priority}】{affected_system} {summary[:64]}"
    description = "\n".join(
        [
            f"{summary}",
            "",
            f"优先级映射：{severity} → {priority}。",
            "",
            f"参考票据：incident:{proposal_id}。",
            f"复核理由：{review_reason}。",
            "证据：",
            *[f"- {ref}" for ref in evidence_refs],
        ]
    )

    return {
        "contract_id": "ticket-draft",
        "contract_version": "1.0.0",
        "ticket_id": ticket_id,
        "status": "draft",
        "target_system": target_system,
        "title": title,
        "description": description,
        "priority": priority,
        "incident_refs": incident_refs,
        "evidence_refs": evidence_refs,
        "summary": {
            "incidents": len(incident_refs),
            "priority": priority,
            "evidence_refs": len(evidence_refs),
            "truncated": False,
        },
        "signature": {
            "drafted_by": "aiops.ticket-draft@0.1.0",
            "sent": False,
            "sender": None,
        },
        "constraints": {
            "no_auto_send": True,
            "requires_target_whitelist": True,
            "requires_human_approval": True,
            "immutable_source": True,
        },
        "provenance": {
            "source_sha256": actual_hash,
            "target_system": target_system,
            "plugin": "aiops.ticket-draft@0.1.0",
            "draft_time": _timestamp_utc(),
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