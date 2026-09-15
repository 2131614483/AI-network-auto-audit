"""Isolated implementation for the verified read-only audit workpaper-export plugin.

R3 受审批输出：只读已确认 Finding 集合，生成确定性、可追溯的底稿导出草稿。
导出永不覆盖历史报告；落库由治理服务在人工审批后以独立身份完成。
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
DEFAULT_MAX_FINDINGS = 500
MAX_FINDINGS_LIMIT = 500
MAX_EVIDENCE_REFS_PER_FINDING = 64
SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}


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
        raise InputRejected("finding-set URI is missing")
    parsed = urlparse(uri)
    if parsed.scheme != "file" or parsed.netloc not in {"", "localhost"}:
        raise InputRejected("finding-set URI must be local")
    raw_path = unquote(parsed.path)
    if raw_path.startswith("/") and len(raw_path) >= 3 and raw_path[2] == ":":
        raw_path = raw_path[1:]
    try:
        resolved = Path(raw_path).resolve(strict=True)
    except OSError as exc:
        raise InputRejected("finding-set file is unavailable") from exc
    if not resolved.is_file():
        raise InputRejected("finding-set is not a regular file")
    try:
        next(root for root in roots if resolved.is_relative_to(root))
    except StopIteration as exc:
        raise InputRejected("finding-set is outside declared read roots") from exc
    return resolved


def _timestamp_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _validated_finding(raw: object) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise InputRejected("finding-set findings must be objects")
    finding_id = raw.get("finding_id")
    if not isinstance(finding_id, str) or not finding_id:
        raise InputRejected("finding-set finding_id is missing")
    title = raw.get("title")
    if not isinstance(title, str) or not title.strip():
        raise InputRejected("finding-set title is missing")
    severity = raw.get("severity")
    if severity not in SEVERITY_ORDER:
        raise InputRejected("finding-set severity is outside the fixed enum")
    if raw.get("status") != "confirmed":
        raise InputRejected("finding-set finding is not confirmed")
    claim_id = raw.get("claim_id")
    if not isinstance(claim_id, str) or not claim_id:
        raise InputRejected("finding-set claim_id is missing")
    claim = raw.get("claim")
    if not isinstance(claim, str) or not claim.strip():
        raise InputRejected("finding-set claim is missing")
    reviewer_label = raw.get("reviewer_label")
    if not isinstance(reviewer_label, str) or not reviewer_label.strip():
        raise InputRejected("finding-set reviewer_label is missing")
    evidence_refs = raw.get("evidence_refs")
    if evidence_refs is None:
        evidence_refs = []
    if (
        not isinstance(evidence_refs, list)
        or len(evidence_refs) > MAX_EVIDENCE_REFS_PER_FINDING
        or any(not isinstance(ref, str) or not ref for ref in evidence_refs)
    ):
        raise InputRejected("finding-set evidence_refs are outside the fixed budget")
    confirmed_at = raw.get("confirmed_at")
    if not isinstance(confirmed_at, str) or not confirmed_at.strip():
        raise InputRejected("finding-set confirmed_at is missing")
    return {
        "finding_id": finding_id,
        "title": title,
        "severity": severity,
        "claim_id": claim_id,
        "claim": claim,
        "reviewer_label": reviewer_label,
        "evidence_refs": list(evidence_refs),
        "confirmed_at": confirmed_at,
    }


def handle(envelope: dict[str, Any]) -> dict[str, Any]:
    if envelope.get("protocol") != "audit-network-plugin-child-v1":
        raise InputRejected("unsupported child protocol")
    if envelope.get("plugin_id") != "audit.workpaper-export":
        raise InputRejected("unexpected plugin identity")
    if envelope.get("capability") != "audit.workpaper.export":
        raise InputRejected("unexpected capability")
    workpaper_input = envelope.get("workpaper")
    if not isinstance(workpaper_input, dict):
        raise InputRejected("workpaper input is missing")
    artifact = workpaper_input.get("finding_set")
    if not isinstance(artifact, dict):
        raise InputRejected("finding-set artifact reference is missing")
    path = _resolve_file(artifact.get("uri"), _allowed_roots())
    content = path.read_bytes()
    if len(content) > MAX_INPUT_BYTES:
        raise InputRejected("finding-set exceeds local read budget")
    expected_size = artifact.get("size_bytes")
    if not isinstance(expected_size, int) or expected_size != len(content):
        raise InputRejected("finding-set size does not match reference")
    expected_hash = artifact.get("sha256")
    actual_hash = hashlib.sha256(content).hexdigest()
    if not isinstance(expected_hash, str) or actual_hash.lower() != expected_hash.lower():
        raise InputRejected("finding-set sha256 does not match reference")

    max_findings = workpaper_input.get("max_findings", DEFAULT_MAX_FINDINGS)
    if not isinstance(max_findings, int) or not 1 <= max_findings <= MAX_FINDINGS_LIMIT:
        raise InputRejected("max_findings is outside the fixed range")

    try:
        finding_set = json.loads(content.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InputRejected("finding-set must be UTF-8 JSON") from exc
    if not isinstance(finding_set, dict):
        raise InputRejected("finding-set must be a single JSON object")
    if finding_set.get("contract_id") != "finding-set" or finding_set.get("contract_version") != "1.0.0":
        raise InputRejected("finding-set contract version is unsupported")
    engagement_id = finding_set.get("engagement_id")
    if not isinstance(engagement_id, str) or not engagement_id:
        raise InputRejected("finding-set engagement_id is missing")
    engagement_name = finding_set.get("engagement_name")
    if not isinstance(engagement_name, str) or not engagement_name.strip():
        raise InputRejected("finding-set engagement_name is missing")
    raw_findings = finding_set.get("findings")
    if not isinstance(raw_findings, list) or not raw_findings:
        raise InputRejected("finding-set findings are missing")

    findings = [_validated_finding(raw) for raw in raw_findings]
    truncated = len(findings) > max_findings
    included = findings[:max_findings]

    finding_ids = [finding["finding_id"] for finding in included]
    export_seed = "|".join([actual_hash, *sorted(finding_ids), str(max_findings)])
    export_id = hashlib.sha256(export_seed.encode("utf-8")).hexdigest()[:16]

    sections: list[dict[str, Any]] = []
    for finding in included:
        section_id = hashlib.sha256(f"{finding['finding_id']}|{finding['claim_id']}".encode("utf-8")).hexdigest()[:16]
        sections.append(
            {
                "section_id": section_id,
                "finding_id": finding["finding_id"],
                "title": finding["title"],
                "severity": finding["severity"],
                "claim_id": finding["claim_id"],
                "claim": finding["claim"],
                "reviewer_label": finding["reviewer_label"],
                "evidence_refs": finding["evidence_refs"],
                "confirmed_at": finding["confirmed_at"],
            }
        )

    severity_counts = {"high": 0, "medium": 0, "low": 0}
    for finding in included:
        severity_counts[finding["severity"]] += 1
    evidence_refs_total = sum(len(finding["evidence_refs"]) for finding in included)

    return {
        "contract_id": "workpaper-export",
        "contract_version": "1.0.0",
        "export_id": export_id,
        "export_kind": "workpaper",
        "status": "draft",
        "engagement_id": engagement_id,
        "engagement_name": engagement_name,
        "sections": sections,
        "summary": {
            "findings": len(included),
            "high": severity_counts["high"],
            "medium": severity_counts["medium"],
            "low": severity_counts["low"],
            "evidence_refs": evidence_refs_total,
            "truncated": truncated,
        },
        "constraints": {
            "no_overwrite": True,
            "requires_human_approval": True,
            "immutable_source": True,
        },
        "provenance": {
            "source_sha256": actual_hash,
            "plugin": "audit.workpaper-export@0.1.0",
            "export_time": _timestamp_utc(),
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
