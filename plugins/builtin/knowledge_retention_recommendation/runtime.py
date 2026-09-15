"""Isolated implementation for the verified read-only knowledge retention-recommendation plugin.

R3 受审批输出：只读已摄入不可变 document-content 工件与保留元数据，确定性地
提出 retain/archive/delete_candidate 建议。建议绝不删除证据：实际归档或删除
必须经策略裁决、人工审批与独立治理身份执行；插件本身无网络、不写基础设施。
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

MAX_INPUT_BYTES = 20 * 1024 * 1024
SENSITIVE_CLASSIFICATIONS = {"audit_confidential", "restricted", "legal"}


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
        raise InputRejected("document URI is missing")
    parsed = urlparse(uri)
    if parsed.scheme != "file" or parsed.netloc not in {"", "localhost"}:
        raise InputRejected("document URI must be local")
    raw_path = unquote(parsed.path)
    if raw_path.startswith("/") and len(raw_path) >= 3 and raw_path[2] == ":":
        raw_path = raw_path[1:]
    try:
        resolved = Path(raw_path).resolve(strict=True)
    except OSError as exc:
        raise InputRejected("document file is unavailable") from exc
    if not resolved.is_file():
        raise InputRejected("document is not a regular file")
    try:
        next(root for root in roots if resolved.is_relative_to(root))
    except StopIteration as exc:
        raise InputRejected("document is outside declared read roots") from exc
    return resolved


def _timestamp_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _validated_int(raw: object, field: str, *, minimum: int = 0) -> int:
    if not isinstance(raw, int) or isinstance(raw, bool) or raw < minimum:
        raise InputRejected(f"{field} is outside the fixed budget")
    return raw


def _decide(last_access_days: int, ref_count: int, classification: str, archive_after_days: int, purge_candidate_after_days: int, min_refs_to_retain: int) -> tuple[str, list[str], str]:
    """Deterministic retention decision over immutable inputs only."""
    if ref_count >= min_refs_to_retain:
        return "retain", ["ref_count_above_threshold"], "文档被活动对象引用，建议保留。"
    if classification in SENSITIVE_CLASSIFICATIONS:
        return "retain", ["sensitive_classification"], "文档属敏感分级，建议保留。"
    if last_access_days <= 30:
        return "retain", ["recent_access"], "文档近期仍被访问，建议保留。"
    if last_access_days > purge_candidate_after_days and ref_count == 0:
        return (
            "delete_candidate",
            ["idle_beyond_purge_threshold", "zero_references"],
            "文档长期未被访问且无引用，仅建议列入回收候选（不删除证据）。",
        )
    if last_access_days > archive_after_days and ref_count == 0:
        return (
            "archive",
            ["idle_beyond_archive_threshold", "zero_references"],
            "文档超过归档阈值且无引用，建议归档（证据仍不可变保留）。",
        )
    return "retain", ["within_retention_window"], "文档仍在保留窗口内，建议保留。"


def handle(envelope: dict[str, Any]) -> dict[str, Any]:
    if envelope.get("protocol") != "audit-network-plugin-child-v1":
        raise InputRejected("unsupported child protocol")
    if envelope.get("plugin_id") != "knowledge.retention-recommendation":
        raise InputRejected("unexpected plugin identity")
    if envelope.get("capability") != "knowledge.retention.recommend":
        raise InputRejected("unexpected capability")
    retention_input = envelope.get("retention")
    if not isinstance(retention_input, dict):
        raise InputRejected("retention input is missing")
    artifact = retention_input.get("document")
    if not isinstance(artifact, dict):
        raise InputRejected("document artifact reference is missing")
    path = _resolve_file(artifact.get("uri"), _allowed_roots())
    content = path.read_bytes()
    if len(content) > MAX_INPUT_BYTES:
        raise InputRejected("document exceeds local read budget")
    expected_size = artifact.get("size_bytes")
    if not isinstance(expected_size, int) or expected_size != len(content):
        raise InputRejected("document size does not match reference")
    expected_hash = artifact.get("sha256")
    actual_hash = hashlib.sha256(content).hexdigest()
    if not isinstance(expected_hash, str) or actual_hash.lower() != expected_hash.lower():
        raise InputRejected("document sha256 does not match reference")

    last_access_days = _validated_int(retention_input.get("last_access_days"), "last_access_days")
    ref_count = _validated_int(retention_input.get("ref_count"), "ref_count")
    archive_after_days = _validated_int(retention_input.get("archive_after_days", 365), "archive_after_days", minimum=30)
    purge_candidate_after_days = _validated_int(
        retention_input.get("purge_candidate_after_days", 730), "purge_candidate_after_days", minimum=30
    )
    min_refs_to_retain = _validated_int(retention_input.get("min_refs_to_retain", 1), "min_refs_to_retain")
    classification = retention_input.get("classification")
    if not isinstance(classification, str) or not classification.strip():
        raise InputRejected("classification is missing")
    media_type = artifact.get("media_type")
    if not isinstance(media_type, str) or not media_type.strip():
        raise InputRejected("document media_type is missing")
    artifact_id = artifact.get("artifact_id")
    if not isinstance(artifact_id, str) or not artifact_id:
        raise InputRejected("document artifact_id is missing")
    uri = artifact.get("uri")
    if not isinstance(uri, str) or not uri:
        raise InputRejected("document uri is missing")

    recommendation, matched_rules, detail = _decide(
        last_access_days,
        ref_count,
        classification,
        archive_after_days,
        purge_candidate_after_days,
        min_refs_to_retain,
    )

    seed = "|".join([actual_hash, recommendation, str(last_access_days), str(ref_count), classification])
    recommendation_id = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]

    summary = {"retain": 0, "archive": 0, "delete_candidate": 0}
    summary[recommendation if recommendation in summary else "delete_candidate"] = 1

    return {
        "contract_id": "retention-recommendation",
        "contract_version": "1.0.0",
        "recommendation_id": recommendation_id,
        "status": "proposed",
        "document_ref": {
            "artifact_id": artifact_id,
            "uri": uri,
            "sha256": actual_hash,
            "media_type": media_type,
        },
        "recommendation": recommendation,
        "rationale": {
            "matched_rules": matched_rules,
            "last_access_days": last_access_days,
            "ref_count": ref_count,
            "classification": classification,
            "detail": detail,
        },
        "summary": summary,
        "signature": {
            "recommended_by": "knowledge.retention-recommendation@0.1.0",
            "reviewed": False,
        },
        "constraints": {
            "no_delete": True,
            "evidence_immutable": True,
            "requires_human_approval": True,
        },
        "provenance": {
            "source_sha256": actual_hash,
            "plugin": "knowledge.retention-recommendation@0.1.0",
            "recommend_time": _timestamp_utc(),
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