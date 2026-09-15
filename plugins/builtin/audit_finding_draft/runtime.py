"""Isolated implementation for the verified read-only finding-draft plugin.

The plugin turns an already-extracted ``anomaly-candidates`` artifact (optionally
combined with an ``investigation-plan-draft`` artifact) into a deterministic
audit finding **draft**: one proposed Finding per covered rule, each carrying
structured Claims with a Claim/EvidenceRef basis (``anomaly``/``gap``/``counter``)
and a counter-hypothesis that must be falsified before confirmation.

It never confirms Findings and never mutates evidence: the draft is only
materialized as output, and the audit domain service stages it into the audit
candidate inbox after a policy verdict.  Drafts do not replace independent QA
or human confirmation.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

MAX_INPUT_BYTES = 100 * 1024 * 1024
MAX_CANDIDATES = 500_000
MAX_FINDINGS = 500
MAX_ROW_REFS = 200
MAX_EVIDENCE_REFS = 64
ANOMALY_RULE_KEYS = ("invalid_date", "outlier_amount", "negative_amount", "round_amount", "duplicate_row", "missing_amount")
SEVERITIES = ("low", "medium", "high")
ACTIONS = ("investigate", "confirm", "escalate")

# Deterministic per-rule templates.  Each rule maps to the finding title, the
# primary anomaly claim, the counter-hypothesis and the falsification probe.
_RULE_FINDING: dict[str, dict[str, Any]] = {
    "invalid_date": {
        "title": "记账日期疑似异常（跨期/改期嫌疑）",
        "claim": "候选行记账日期偏离业务日历，存在日期换算或人为改期嫌疑。",
        "gap_claim": "原始凭单与日切说明尚未收集，无法排除系统换算或跨期调整的正常解释。",
        "counter_hypothesis": "异常日期由系统换算或跨期调整正常产生，非人为改期。",
        "verify_with": ["原始业务单据", "系统日切日志", "跨期调整审批"],
    },
    "outlier_amount": {
        "title": "金额偏离历史分布（虚构收入/错入科目嫌疑）",
        "claim": "候选行金额显著偏离同账户历史分布，存在虚构收入或错入科目嫌疑。",
        "gap_claim": "原始凭证、合同与历史同期分布尚未取得，无法排除大宗一次性交易的正常解释。",
        "counter_hypothesis": "金额异常由大宗一次性交易、对冲或错入对方科目所致，非虚构收入。",
        "verify_with": ["合同与收货/交付单据", "对方账户流水", "增值税/发票记录"],
    },
    "negative_amount": {
        "title": "负金额分录（虚减收入/重复冲销嫌疑）",
        "claim": "候选行为负金额分录，存在虚减收入或重复冲销嫌疑。",
        "gap_claim": "冲销审批单与原入账凭证尚未核对，无法确认冲销范围与剩余金额。",
        "counter_hypothesis": "负金额为正常红字冲销或退款，非虚减收入。",
        "verify_with": ["冲销审批", "原记账凭证", "退款流水"],
    },
    "round_amount": {
        "title": "整数/近似整数金额（人为凑整或分割搭配嫌疑）",
        "claim": "候选行为整数或近似整数金额，存在人为凑整或分割搭配嫌疑。",
        "gap_claim": "费用分摊表与发票汇总尚未取得，无法排除固定分摊或代扣代缴的正常解释。",
        "counter_hypothesis": "整数金额来自固定分摊或代扣代缴，非人为拆分。",
        "verify_with": ["分摊计算表", "代扣凭证", "科目明细"],
    },
    "duplicate_row": {
        "title": "疑似重复入账行",
        "claim": "候选行与其他行在关键字段上重复，存在重复入账嫌疑。",
        "gap_claim": "原始业务单据去重比对与过账日志尚未复核，无法确认是否仅摘要差异。",
        "counter_hypothesis": "重复行为系统同步或模板复制产生，未重复入账。",
        "verify_with": ["过账日志", "单据编号唯一性", "对方科目余额"],
    },
    "missing_amount": {
        "title": "金额缺失/为空行（丢失收入或草稿嫌疑）",
        "claim": "候选行金额缺失或为空，存在丢失收入或未入账草稿嫌疑。",
        "gap_claim": "原始单据与提取日志尚未核对，无法确认是否为零金额业务或提取失败。",
        "counter_hypothesis": "缺失金额来自草稿行或零金额业务，非丢失收入。",
        "verify_with": ["单据状态", "OCR 原始输出", "零金额业务规则"],
    },
}


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


def _read_verified(artifact: Any, roots: tuple[Path, ...], label: str) -> tuple[Path, str]:
    if not isinstance(artifact, dict):
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
    return path, actual_hash


def _parse_json(path: Path, label: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InputRejected(f"{label} must be UTF-8 JSON") from exc


def _stable_hash(text: str, size: int = 16) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:size]


def _bounded(items: list[str], limit: int) -> list[str]:
    """Deterministically cap a reference list; longer input is never silently kept whole."""
    return items[:limit]


def handle(envelope: dict[str, Any]) -> dict[str, Any]:
    if envelope.get("protocol") != "audit-network-plugin-child-v1":
        raise InputRejected("unsupported child protocol")
    if envelope.get("plugin_id") != "audit.finding-draft":
        raise InputRejected("unexpected plugin identity")
    if envelope.get("capability") != "audit.finding.draft":
        raise InputRejected("unexpected capability")
    finding_input = envelope.get("finding")
    if not isinstance(finding_input, dict):
        raise InputRejected("finding input is missing")
    roots = _allowed_roots()
    anomaly_path, anomaly_sha256 = _read_verified(finding_input.get("anomaly_candidates"), roots, "anomaly candidates")
    plan_path: Path | None = None
    plan_sha256: str | None = None
    if finding_input.get("investigation_plan") is not None:
        plan_path, plan_sha256 = _read_verified(finding_input.get("investigation_plan"), roots, "investigation plan")

    anomaly_set = _parse_json(anomaly_path, "anomaly candidates")
    if not isinstance(anomaly_set, dict):
        raise InputRejected("anomaly candidates must be a JSON object")
    period = anomaly_set.get("period")
    rule_pack_sha256 = anomaly_set.get("rule_pack_sha256")
    if not isinstance(period, str) or not isinstance(rule_pack_sha256, str):
        raise InputRejected("anomaly candidates must carry period and rule_pack_sha256")
    raw_candidates = anomaly_set.get("candidates")
    if not isinstance(raw_candidates, list):
        raise InputRejected("anomaly candidates must carry a candidate list")
    if len(raw_candidates) > MAX_CANDIDATES:
        raise InputRejected("anomaly candidates exceed the fixed event budget")

    # --- Optional investigation plan: reuse its counter-hypotheses ------------
    plan_hypotheses: dict[str, dict[str, Any]] = {}
    plan_gaps: dict[str, list[str]] = {}
    if plan_path is not None:
        plan_draft = _parse_json(plan_path, "investigation plan")
        if not isinstance(plan_draft, dict):
            raise InputRejected("investigation plan must be a JSON object")
        raw_hypotheses = plan_draft.get("counter_hypotheses")
        if isinstance(raw_hypotheses, list):
            for raw in raw_hypotheses:
                if not isinstance(raw, dict):
                    continue
                rule_key = raw.get("rule_key")
                if isinstance(rule_key, str) and rule_key in ANOMALY_RULE_KEYS:
                    plan_hypotheses[rule_key] = raw
        raw_gaps = plan_draft.get("evidence_gaps")
        if isinstance(raw_gaps, list):
            for raw in raw_gaps:
                if not isinstance(raw, dict):
                    continue
                rule_keys = raw.get("rule_keys")
                if isinstance(rule_keys, list):
                    for rule_key in rule_keys:
                        if isinstance(rule_key, str) and rule_key in ANOMALY_RULE_KEYS:
                            plan_gaps.setdefault(rule_key, []).append(str(raw.get("evidence_type", "")))

    # --- Group candidates deterministically by rule_key ------------------------
    grouped: dict[str, list[dict[str, Any]]] = {}
    unknown_rule_count = 0
    for raw in raw_candidates:
        if not isinstance(raw, dict):
            raise InputRejected("each anomaly candidate must be an object")
        rule_key = raw.get("rule_key")
        row_ref = raw.get("row_ref")
        if not isinstance(rule_key, str) or not isinstance(row_ref, str):
            raise InputRejected("each anomaly candidate must carry rule_key and row_ref")
        if rule_key not in ANOMALY_RULE_KEYS:
            unknown_rule_count += 1
            continue
        severity = raw.get("severity")
        if severity is None or not isinstance(severity, str) or severity not in SEVERITIES:
            severity = "medium"
        score = raw.get("score")
        evidence = raw.get("evidence")
        entry: dict[str, Any] = {
            "row_ref": row_ref,
            "source_ref": raw.get("source_ref") if isinstance(raw.get("source_ref"), str) else row_ref,
            "severity": severity,
            "score": round(float(score) if isinstance(score, (int, float)) else 0.0, 6),
            "evidence": evidence if isinstance(evidence, dict) else {},
        }
        grouped.setdefault(rule_key, []).append(entry)
    for rule_key in grouped:
        grouped[rule_key].sort(key=lambda item: (item["row_ref"], item["source_ref"]))

    # --- Build one proposed Finding per covered rule ---------------------------
    findings: list[dict[str, Any]] = []
    for rule_key in sorted(grouped):
        if len(findings) >= MAX_FINDINGS:
            break
        entries = grouped[rule_key]
        template = _RULE_FINDING[rule_key]
        highest_severity = sorted((item["severity"] for item in entries), key=SEVERITIES.index)[-1]
        max_score = max(item["score"] for item in entries)
        anomaly_confidence = round(min(max(0.5 + 0.4 * max_score, 0.5), 0.97), 2)

        row_refs = _bounded([item["row_ref"] for item in entries], MAX_ROW_REFS)
        evidence_refs: list[str] = []
        for item in entries:
            evidence_refs.append(item["source_ref"])
            evidence = item["evidence"]
            for value in (evidence.get("source_sha256"),):
                if isinstance(value, str) and value:
                    evidence_refs.append(value)
        evidence_refs = _bounded(sorted(set(evidence_refs)), MAX_EVIDENCE_REFS)

        claim_counter = _stable_hash(f"claim:counter:{rule_key}:{anomaly_sha256}")
        hypothesis = plan_hypotheses.get(rule_key, {})
        verify_with = (
            [str(value) for value in hypothesis.get("verify_with", []) if isinstance(value, str)]
            if hypothesis
            else template["verify_with"]
        )
        counter_statement = (
            str(hypothesis.get("hypothesis"))
            if hypothesis and isinstance(hypothesis.get("hypothesis"), str)
            else template["counter_hypothesis"]
        )
        claims: list[dict[str, Any]] = [
            {
                "claim_id": _stable_hash(f"claim:anomaly:{rule_key}:{anomaly_sha256}"),
                "statement": f"{template['claim']} 命中 {len(entries)} 行、最高严重度 {highest_severity}。",
                "basis": "anomaly",
                "rule_key": rule_key,
                "row_ref": entries[0]["row_ref"],
                "evidence_refs": _bounded(evidence_refs[:8], 32),
                "confidence": anomaly_confidence,
                "severity": highest_severity,
            }
        ]
        gap_types = plan_gaps.get(rule_key, [])
        if gap_types:
            claims.append(
                {
                    "claim_id": _stable_hash(f"claim:gap:{rule_key}:{anomaly_sha256}"),
                    "statement": f"{template['gap_claim']} 待补证据：{'、'.join(sorted(set(gap_types))[:6])}。",
                    "basis": "gap",
                    "rule_key": rule_key,
                    "row_ref": entries[0]["row_ref"],
                    "evidence_refs": [],
                    "confidence": 0.5,
                    "severity": highest_severity,
                }
            )
        claims.append(
            {
                "claim_id": claim_counter,
                "statement": f"必须主动证伪反证“{counter_statement}”后才能把嫌疑上升为确认。",
                "basis": "counter",
                "rule_key": rule_key,
                "row_ref": entries[0]["row_ref"],
                "evidence_refs": _bounded(verify_with[:8], 32),
                "confidence": round(1.0 - anomaly_confidence, 2),
                "severity": highest_severity,
            }
        )

        counter_hypotheses: list[dict[str, Any]] = []
        hypothesis_id = _stable_hash(f"finding:counter:{rule_key}:{anomaly_sha256}")
        counter_hypotheses.append(
            {
                "hypothesis_id": hypothesis_id,
                "hypothesis": counter_statement,
                "verify_with": _bounded(verify_with, 16),
            }
        )

        findings.append(
            {
                "finding_id": _stable_hash(f"finding:{rule_key}:{anomaly_sha256}"),
                "title": template["title"],
                "severity": highest_severity,
                "rule_key": rule_key,
                "status": "proposed",
                "row_refs": row_refs,
                "evidence_refs": evidence_refs,
                "claims": claims,
                "counter_hypotheses": counter_hypotheses,
                "proposed_action": "escalate" if highest_severity == "high" else "confirm" if highest_severity == "medium" else "investigate",
            }
        )

    draft_id = _stable_hash(f"{anomaly_sha256}|{plan_sha256 or ''}")
    return {
        "contract_id": "finding-draft",
        "contract_version": "1.0.0",
        "anomaly_set_sha256": anomaly_sha256,
        **({"plan_sha256": plan_sha256} if plan_sha256 else {}),
        "draft_id": draft_id,
        "period": period,
        "rule_pack_sha256": rule_pack_sha256,
        "findings": findings,
        "summary": {
            "covered_candidates": sum(len(entries) for entries in grouped.values()),
            "findings": len(findings),
            "claims": sum(len(finding["claims"]) for finding in findings),
            "evidence_refs": sum(len(finding["evidence_refs"]) for finding in findings),
            "truncated": len(grouped) > len(findings),
            "unknown_rule_count": unknown_rule_count,
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
