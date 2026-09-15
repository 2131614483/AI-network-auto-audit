"""Isolated implementation for the verified read-only aiops postmortem-draft plugin.

R3 受审批输出：只读已确认 incident-proposal 事件与可选恢复核验账本，确定性地
生成可追溯复盘草稿。草稿永不自动发布：实际发布必须人工审批；插件本身无网络、
不写基础设施，落库由治理服务在人工审批后完成。
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
MAX_EVIDENCE_REFS = 64
ACTION_PRIORITY = {"critical": "high", "high": "high", "medium": "medium", "low": "low"}


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
    return resolved


def _timestamp_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read_artifact(artifact: Any, roots: tuple[Path, ...], label: str) -> tuple[str, dict[str, Any]]:
    if not isinstance(artifact, dict) or not isinstance(artifact.get("uri"), str):
        raise InputRejected(f"{label} artifact reference is missing")
    path = _resolve_file(artifact.get("uri"), roots)
    content = path.read_bytes()
    if len(content) > MAX_INPUT_BYTES:
        raise InputRejected(f"{label} exceeds local read budget")
    expected_size = artifact.get("size_bytes")
    if not isinstance(expected_size, int) or expected_size != len(content):
        raise InputRejected(f"{label} size does not match reference")
    expected_hash = artifact.get("sha256")
    actual_hash = hashlib.sha256(content).hexdigest()
    if not isinstance(expected_hash, str) or actual_hash.lower() != expected_hash.lower():
        raise InputRejected(f"{label} sha256 does not match reference")
    try:
        payload = json.loads(content.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InputRejected(f"{label} must be UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise InputRejected(f"{label} must be a single JSON object")
    return actual_hash, payload


def handle(envelope: dict[str, Any]) -> dict[str, Any]:
    if envelope.get("protocol") != "audit-network-plugin-child-v1":
        raise InputRejected("unsupported child protocol")
    if envelope.get("plugin_id") != "aiops.postmortem-draft":
        raise InputRejected("unexpected plugin identity")
    if envelope.get("capability") != "aiops.postmortem.draft":
        raise InputRejected("unexpected capability")
    postmortem_input = envelope.get("postmortem")
    if not isinstance(postmortem_input, dict):
        raise InputRejected("postmortem input is missing")

    roots = _allowed_roots()
    incident_artifact = postmortem_input.get("incident_proposal")
    incident_hash, proposal = _read_artifact(incident_artifact, roots, "incident-proposal")
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
    if severity not in ACTION_PRIORITY:
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

    verification_hash = ""
    verification_refs: list[str] = []
    verification = postmortem_input.get("verification")
    if verification is not None:
        verification_hash, verification_payload = _read_artifact(verification, roots, "recovery-verification")
        if (
            verification_payload.get("contract_id") != "recovery-verification"
            or verification_payload.get("contract_version") != "1.0.0"
        ):
            raise InputRejected("recovery-verification contract version is unsupported")
        verification_id = verification_payload.get("verification_id")
        if not isinstance(verification_id, str) or not verification_id:
            raise InputRejected("recovery-verification verification_id is missing")
        if verification_payload.get("status") not in {"recovered", "not_recovered", "insufficient_data"}:
            raise InputRejected("recovery-verification status is outside the fixed enum")
        verification_refs = [f"verification:{verification_id}"]

    seed = "|".join([incident_hash, proposal_id, verification_hash])
    postmortem_id = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]

    title = f"【复盘】{affected_system} {summary[:64]}"
    overview = "\n".join([f"{summary}", "", f"复核理由：{review_reason}。"])
    timeline_steps = [
        {"step": "事件归因", "sequence": 1, "reference": f"incident:{proposal_id}"},
    ]
    if verification_refs:
        timeline_steps.append({"step": "恢复核验", "sequence": 2, "reference": verification_refs[0]})
    action_priority = ACTION_PRIORITY[severity]
    action_items = [
        {
            "action": f"复核确认 {affected_system} 根本原因并补充证据",
            "owner": "reviewer",
            "priority": action_priority,
        }
    ]

    return {
        "contract_id": "postmortem-draft",
        "contract_version": "1.0.0",
        "postmortem_id": postmortem_id,
        "status": "draft",
        "incident_refs": [f"incident:{proposal_id}"],
        "title": title,
        "overview": overview,
        "timeline_steps": timeline_steps,
        "root_cause_candidates": [grouping_key],
        "action_items": action_items,
        "verification_refs": verification_refs,
        "evidence_refs": evidence_refs,
        "summary": {
            "incidents": 1,
            "severity": severity,
            "verification_refs": len(verification_refs),
            "truncated": False,
        },
        "signature": {
            "drafted_by": "aiops.postmortem-draft@0.1.0",
            "published": False,
            "publisher": None,
        },
        "constraints": {
            "no_auto_publish": True,
            "requires_human_approval": True,
            "immutable_source": True,
        },
        "provenance": {
            "incident_sha256": incident_hash,
            "verification_sha256": verification_hash,
            "plugin": "aiops.postmortem-draft@0.1.0",
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