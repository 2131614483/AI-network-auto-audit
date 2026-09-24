"""Isolated implementation for the verified read-only audit.network.skill-output plugin.

将组网流水线（L0–L5）已确认的上游结果工件（network-result-set）复用为输出素材。
调用哪个 Skill 由调度 AI 从 skills 目录【自主选用】：插件只读扫描技能目录的
SKILL.md（name / description），把目录清单与调用上下文一起交给 AI；AI 选定
skill_id 后再发起正式调用。本插件只读、不访问网络、永不覆盖历史输出；签发由
人完成，落库由治理服务在人工审批后以独立身份完成。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

MAX_INPUT_BYTES = 16 * 1024 * 1024
DEFAULT_TEMPLATE_VERSION = "1.0.0"
MAX_RESULT_SOURCES = 256
MAX_FINDINGS_LIMIT = 500
MAX_SKILL_ENTRIES = 200
SKILL_MD_NAME = "SKILL.md"


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


def _skill_roots() -> tuple[Path, ...]:
    try:
        raw_roots = json.loads(os.environ["AUDIT_SKILL_ROOTS"])
    except (KeyError, json.JSONDecodeError) as exc:
        raise InputRejected("skill search roots are unavailable") from exc
    if not isinstance(raw_roots, list) or not raw_roots:
        raise InputRejected("skill search roots are invalid")
    return tuple(Path(str(raw)).resolve() for raw in raw_roots)


def _parse_frontmatter(content: str) -> dict[str, str]:
    """Extract YAML-frontmatter name/description from a SKILL.md (best effort, read-only)."""
    match = re.match(r"\A---\s*\n(.*?)\n---", content, flags=re.DOTALL)
    if not match:
        return {}
    fields: dict[str, str] = {}
    for line in match.group(1).splitlines():
        m = re.match(r'^\s*([A-Za-z_][A-Za-z0-9_-]*)\s*:\s*(.*?)\s*$', line)
        if m and (m.group(1) in {"name", "description", "version"}):
            value = m.group(2).strip().strip('"').strip("'")
            if value:
                fields[m.group(1)] = value
    return fields


def _list_skills() -> list[dict[str, Any]]:
    """枚举 skills 目录下的可用 Skill（name / description 仅取自各 SKILL.md frontmatter）。"""
    entries: list[dict[str, Any]] = []
    for root in _skill_roots():
        for child in sorted(root.iterdir()) if root.is_dir() else []:
            if not child.is_dir():
                continue
            skill_md = child / SKILL_MD_NAME
            if not skill_md.is_file():
                continue
            try:
                if skill_md.stat().st_size > 64 * 1024:
                    continue
                content = skill_md.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            meta = _parse_frontmatter(content)
            if not meta.get("name"):
                continue
            entries.append(
                {
                    "id": meta.get("name"),
                    "name": meta.get("name"),
                    "description": meta.get("description", ""),
                    "version": meta.get("version", "0.0.0"),
                    "source": str(skill_md.resolve()),
                }
            )
            if len(entries) >= MAX_SKILL_ENTRIES:
                return entries
    return entries


def _catalog_sha256(entries: list[dict[str, Any]]) -> str:
    canonical = json.dumps(
        [{"id": e["id"], "version": e["version"], "description": e["description"]} for e in entries],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _resolve_file(uri: object, roots: tuple[Path, ...]) -> Path:
    if not isinstance(uri, str):
        raise InputRejected("network-result-set URI is missing")
    parsed = urlparse(uri)
    if parsed.scheme != "file" or parsed.netloc not in {"", "localhost"}:
        raise InputRejected("network-result-set URI must be local")
    raw_path = unquote(parsed.path)
    if raw_path.startswith("/") and len(raw_path) >= 3 and raw_path[2] == ":":
        raw_path = raw_path[1:]
    try:
        resolved = Path(raw_path).resolve(strict=True)
    except OSError as exc:
        raise InputRejected("network-result-set file is unavailable") from exc
    if not resolved.is_file():
        raise InputRejected("network-result-set is not a regular file")
    try:
        next(root for root in roots if resolved.is_relative_to(root))
    except StopIteration as exc:
        raise InputRejected("network-result-set is outside declared read roots") from exc
    return resolved


def _timestamp_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _require_str(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InputRejected(f"network-result-set {field} is missing")
    return value


def _require_int(value: object, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise InputRejected(f"network-result-set {field} is out of range")
    return value


def handle(envelope: dict[str, Any]) -> dict[str, Any]:
    if envelope.get("protocol") != "audit-network-plugin-child-v1":
        raise InputRejected("unsupported child protocol")
    if envelope.get("plugin_id") != "audit.network.skill-output":
        raise InputRejected("unexpected plugin identity")
    if envelope.get("capability") != "audit.network.skill-output":
        raise InputRejected("unexpected capability")

    request = envelope.get("request")
    if not isinstance(request, dict):
        raise InputRejected("request is missing")
    artifact = request.get("result_set")
    if not isinstance(artifact, dict):
        raise InputRejected("result-set artifact reference is missing")

    # 1) 只读装载不可变输入工件并核验引用（size + sha256）
    path = _resolve_file(artifact.get("uri"), _allowed_roots())
    content = path.read_bytes()
    if len(content) > MAX_INPUT_BYTES:
        raise InputRejected("network-result-set exceeds local read budget")
    expected_size = artifact.get("size_bytes")
    if not isinstance(expected_size, int) or expected_size != len(content):
        raise InputRejected("network-result-set size does not match reference")
    expected_hash = artifact.get("sha256")
    actual_hash = hashlib.sha256(content).hexdigest()
    if not isinstance(expected_hash, str) or actual_hash.lower() != expected_hash.lower():
        raise InputRejected("network-result-set sha256 does not match reference")

    try:
        result_set = json.loads(content.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InputRejected("network-result-set must be UTF-8 JSON") from exc
    if not isinstance(result_set, dict):
        raise InputRejected("network-result-set must be a single JSON object")
    if result_set.get("contract_id") != "network-result-set" or result_set.get("contract_version") != "1.0.0":
        raise InputRejected("network-result-set contract version is unsupported")

    engagement_id = _require_str(result_set.get("engagement_id"), "engagement_id")
    engagement_name = _require_str(result_set.get("engagement_name"), "engagement_name")

    # 2) Skill 选用：默认由调度 AI 从 skills 目录自主选用；
    #    仅在 request 显式给出 skill_id 时使用该固定 Skill（并核验其确实在目录中）
    skills = _list_skills()
    catalog_hash = _catalog_sha256(skills)
    requested_skill_id = request.get("skill_id")
    requested_skill_version = request.get("skill_version")
    template_version = _require_str(request.get("template_version") or DEFAULT_TEMPLATE_VERSION, "template_version")

    selected_skill: dict[str, Any] | None = None
    if isinstance(requested_skill_id, str) and requested_skill_id.strip():
        matched = [s for s in skills if s["id"] == requested_skill_id]
        if not matched:
            raise InputRejected("requested skill_id is not present in the skill catalog")
        selected_skill = matched[0]
        skill_id = selected_skill["id"]
        skill_version = (
            _require_str(requested_skill_version, "skill_version")
            if isinstance(requested_skill_version, str) and requested_skill_version.strip()
            else selected_skill["version"]
        )
    else:
        skill_id = ""
        skill_version = ""

    sources = result_set.get("sources")
    if not isinstance(sources, list) or not sources or len(sources) > MAX_RESULT_SOURCES:
        raise InputRejected("network-result-set sources are missing or exceed budget")
    source_count = len(sources)
    total_records = 0
    for source in sources:
        if not isinstance(source, dict):
            raise InputRejected("network-result-set sources must be objects")
        total_records += _require_int(source.get("records"), "source.records")
    engine_summary = result_set.get("engine")
    if not isinstance(engine_summary, dict):
        raise InputRejected("network-result-set engine is missing")
    layer_count = _require_int(engine_summary.get("layers"), "engine.layers")
    node_count = _require_int(engine_summary.get("nodes"), "engine.nodes")
    edge_count = _require_int(engine_summary.get("edges"), "engine.edges")

    findings = result_set.get("findings")
    if findings is None:
        findings = []
    if not isinstance(findings, list) or len(findings) > MAX_FINDINGS_LIMIT:
        raise InputRejected("network-result-set findings are outside the fixed budget")
    if any(not isinstance(f, dict) or not _require_str(f.get("finding_id"), "finding.finding_id") for f in findings):
        raise InputRejected("network-result-set findings are malformed")

    # 3) 幂等键：输入工件哈希 + Skill 标识/目录清单 + 模板版本（确定性、可追溯）
    idem_skill = skill_id or f"ai-select:{catalog_hash[:16]}"
    idem_version = skill_version or catalog_hash[:16]
    seed = "|".join([actual_hash, idem_skill, idem_version, template_version, engagement_id])
    output_id = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]

    # 4) 组装 Skill 调用上下文（作为输出工件附带的确定性输入快照）
    selection_mode = "explicit" if selected_skill else "ai_autonomous"
    skill_context = {
        "skill": {"id": skill_id, "version": skill_version} if selected_skill else {},
        "selection_mode": selection_mode,
        "template_version": template_version,
        "skill_catalog": [
            {"id": s["id"], "name": s["name"], "version": s["version"], "description": s["description"]}
            for s in skills
        ],
        "skill_catalog_sha256": catalog_hash,
        "engagement": {"engagement_id": engagement_id, "engagement_name": engagement_name},
        "engine": {
            "layers": layer_count,
            "nodes": node_count,
            "edges": edge_count,
            "sources": source_count,
            "records": total_records,
        },
        "findings": [
            {
                "finding_id": f["finding_id"],
                "severity": f.get("severity", "unclassified"),
                "title": f.get("title", ""),
                "evidence_refs": f.get("evidence_refs", []),
            }
            for f in findings
        ],
    }

    # 5) 降级只读产出：结构合成为可追溯输出工件，正文由 Skill 在审批后生成
    return {
        "contract_id": "skill-output",
        "contract_version": "1.0.0",
        "output_id": output_id,
        "output_kind": "skill_invocation",
        "status": "pending_ai_selection" if selected_skill is None else "pending_human_review",
        "engagement_id": engagement_id,
        "engagement_name": engagement_name,
        "skill": (
            {"id": skill_id, "version": skill_version, "template_version": template_version}
            if selected_skill
            else {}
        ),
        "selection": {
            "mode": selection_mode,
            "catalog_sha256": catalog_hash,
            "available": [s["id"] for s in skills],
        },
        "body": {
            "task": "基于组网结果生成审计输出（调用 skill：AI 从技能目录自主选用）",
            "skill_context": skill_context,
            "sections": [
                "审计问题、结论与保证限度（先行声明）",
                "审计范围与对象（含开源来源）",
                "数据质量观测与发现清单",
                "处理过程与原理",
                "可靠性、边界声明与结论",
            ],
        },
        "signature": {
            "generated_by": "audit.network.skill-output@0.1.0",
            "signed": False,
            "issuer": "audit_engagement_lead",
        },
        "summary": {
            "sources": source_count,
            "records": total_records,
            "findings": len(findings),
            "nodes": node_count,
            "edges": edge_count,
            "layers": layer_count,
        },
        "constraints": {
            "no_overwrite": True,
            "requires_human_approval": True,
            "immutable_source": True,
            "human_issuance_required": True,
            "skill_invocation_gated": True,
        },
        "provenance": {
            "source_sha256": actual_hash,
            "selection_mode": selection_mode,
            "skill_id": skill_id,
            "skill_version": skill_version,
            "skill_catalog_sha256": catalog_hash,
            "template_version": template_version,
            "plugin": "audit.network.skill-output@0.1.0",
            "generated_at": _timestamp_utc(),
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