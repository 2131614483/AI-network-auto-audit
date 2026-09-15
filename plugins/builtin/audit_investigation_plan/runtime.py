"""Isolated implementation for the verified read-only investigation-plan plugin.

The plugin turns an already-extracted ``anomaly-candidates`` artifact into a
deterministic audit investigation-plan **draft**: ordered investigation steps,
the evidence still needed to close each candidate group, and a counter-hypothesis
checklist that must be falsified before any suspicion is treated as confirmed.

It never confirms Findings and never mutates evidence: the draft is only
materialized as output, and the audit domain service stages it into an audit
plan draft after a policy verdict.
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
DEFAULT_MAX_STEPS = 8
MAX_STEPS = 64
ANOMALY_RULE_KEYS = ("invalid_date", "outlier_amount", "negative_amount", "round_amount", "duplicate_row", "missing_amount")
SEVERITIES = ("low", "medium", "high")

# Deterministic per-rule templates.  Each rule maps to the investigation
# action, the evidence families needed to close it, and the counter-hypothesis
# that must be actively falsified before escalation.
_RULE_STEP: dict[str, dict[str, Any]] = {
    "invalid_date": {
        "action": "核对异常行的记账日期与原始业务日期，确认日期换算/录入错误或跨期调整；按日重新抽取同账户分布。",
        "required_evidence": ["原始凭单", "银行/入库日期流水", "日切说明"],
        "counter_hypothesis": "异常日期由系统换算或跨期调整正常产生，非人为改期。",
        "verify_with": ["原始业务单据", "系统日切日志", "跨期调整审批"],
    },
    "outlier_amount": {
        "action": "对高分异常金额逐笔追索至原始凭证，复核入账科目与业务实质；对同账户历史分布做稳健 z-score 复核。",
        "required_evidence": ["原始凭证", "合同/订单", "历史同期分布"],
        "counter_hypothesis": "金额异常由大宗一次性交易、对冲或错入对方科目所致，非虚构收入。",
        "verify_with": ["合同与收货/交付单据", "对方账户流水", "增值税/发票记录"],
    },
    "negative_amount": {
        "action": "核对负金额分录的冲销/红字业务依据，确认冲销范围与剩余金额；复核是否重复冲销。",
        "required_evidence": ["冲销审批单", "原入账凭证", "科目余额"],
        "counter_hypothesis": "负金额为正常红字冲销或退款，非虚减收入。",
        "verify_with": ["冲销审批", "原记账凭证", "退款流水"],
    },
    "round_amount": {
        "action": "核对整数/近似整数金额的分录来源，确认是否存在人为凑整或分割搭配；复核同一会计期间的近似金额组。",
        "required_evidence": ["费用分摊表", "发票汇总", "内部结转说明"],
        "counter_hypothesis": "整数金额来自固定分摊或代扣代缴，非人为拆分。",
        "verify_with": ["分摊计算表", "代扣凭证", "科目明细"],
    },
    "duplicate_row": {
        "action": "复核重复行的差异字段（金额、摘要、对方科目），确认是否存在重复入账或仅摘要差异。",
        "required_evidence": ["原始业务单据去重比对", "入账日志", "科目余额"],
        "counter_hypothesis": "重复行为系统同步或模板复制产生，未重复入账。",
        "verify_with": ["过账日志", "单据编号唯一性", "对方科目余额"],
    },
    "missing_amount": {
        "action": "复核空/缺失金额行的字段完整性与单据状态，确认是否为未入账草稿、零金额或提取失败。",
        "required_evidence": ["原始单据", "提取日志/OCR 结果", "分录状态标记"],
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


def handle(envelope: dict[str, Any]) -> dict[str, Any]:
    if envelope.get("protocol") != "audit-network-plugin-child-v1":
        raise InputRejected("unsupported child protocol")
    if envelope.get("plugin_id") != "audit.investigation-plan":
        raise InputRejected("unexpected plugin identity")
    if envelope.get("capability") != "audit.investigation.plan":
        raise InputRejected("unexpected capability")
    plan_input = envelope.get("investigation")
    if not isinstance(plan_input, dict):
        raise InputRejected("investigation input is missing")
    roots = _allowed_roots()
    anomaly_path, anomaly_sha256 = _read_verified(plan_input.get("anomaly_candidates"), roots, "anomaly candidates")
    evidence_path: Path | None = None
    evidence_sha256: str | None = None
    if plan_input.get("evidence_index") is not None:
        evidence_path, evidence_sha256 = _read_verified(plan_input.get("evidence_index"), roots, "evidence index")
    max_steps = plan_input.get("max_steps", DEFAULT_MAX_STEPS)
    if not isinstance(max_steps, int) or not (1 <= max_steps <= MAX_STEPS):
        raise InputRejected("max_steps is outside the fixed budget")

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

    # --- Index available evidence (optional) ----------------------------------
    collected_types: set[str] = set()
    collected_refs: set[str] = set()
    if evidence_path is not None:
        evidence_index = _parse_json(evidence_path, "evidence index")
        if not isinstance(evidence_index, dict):
            raise InputRejected("evidence index must be a JSON object")
        raw_evidence = evidence_index.get("evidence")
        if not isinstance(raw_evidence, list):
            raise InputRejected("evidence index must carry an evidence list")
        for raw in raw_evidence:
            if not isinstance(raw, dict):
                continue
            evidence_type = raw.get("evidence_type")
            source_ref = raw.get("source_ref")
            if isinstance(evidence_type, str) and evidence_type:
                collected_types.add(evidence_type.strip())
            if isinstance(source_ref, str) and source_ref:
                collected_refs.add(source_ref.strip())

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
        if severity is None or not isinstance(severity, str):
            severity = "medium"
        if severity not in SEVERITIES:
            severity = "medium"
        score = raw.get("score")
        entry: dict[str, Any] = {
            "row_ref": row_ref,
            "source_ref": raw.get("source_ref") if isinstance(raw.get("source_ref"), str) else row_ref,
            "severity": severity,
            "score": round(float(score) if isinstance(score, (int, float)) else 0.0, 6),
        }
        grouped.setdefault(rule_key, []).append(entry)
    for rule_key in grouped:
        grouped[rule_key].sort(key=lambda item: (item["row_ref"], item["source_ref"]))

    # --- Investigation steps (one deterministic step per covered rule) ---------
    remaining = max_steps
    truncated = False
    steps: list[dict[str, Any]] = []
    for rule_key in sorted(grouped):
        if remaining <= 0:
            truncated = True
            break
        entries = grouped[rule_key]
        template = _RULE_STEP[rule_key]
        highest_severity = sorted((item["severity"] for item in entries), key=SEVERITIES.index)[-1]
        steps.append(
            {
                "step_id": _stable_hash(f"plan-step:{rule_key}:{anomaly_sha256}"),
                "order": len(steps) + 1,
                "rule_key": rule_key,
                "action": (
                    f"{template['action']}"
                    f" 命中 {len(entries)} 条、最高严重度 {highest_severity}，"
                    f"目标行 {','.join(item['row_ref'] for item in entries[:10])}"
                    + ("…" if len(entries) > 10 else "")
                    + "。"
                ),
                "target_refs": [item["source_ref"] for item in entries],
                "required_evidence": [item for item in template["required_evidence"] if item not in collected_types],
                "rationale": f"规则命中 {len(entries)} 条候选，最高严重度 {highest_severity}，需主动证伪反证后才能继续。",
            }
        )
        remaining -= 1

    # --- Counter-hypothesis checklist ------------------------------------------
    counter_hypotheses: list[dict[str, Any]] = []
    for rule_key in sorted(grouped):
        template = _RULE_STEP[rule_key]
        entries = grouped[rule_key]
        highest_severity = sorted((item["severity"] for item in entries), key=SEVERITIES.index)[-1]
        counter_hypotheses.append(
            {
                "hypothesis_id": _stable_hash(f"counter:{rule_key}:{anomaly_sha256}"),
                "hypothesis": template["counter_hypothesis"],
                "rule_key": rule_key,
                "verify_with": template["verify_with"],
                "severity": highest_severity,
            }
        )

    # --- Evidence gaps (material the investigation still needs) ----------------
    evidence_gaps: list[dict[str, Any]] = []
    for item in steps:
        for evidence_type in item["required_evidence"]:
            if any(gap["evidence_type"] == evidence_type for gap in evidence_gaps):
                gap = next(gap for gap in evidence_gaps if gap["evidence_type"] == evidence_type)
                if item["rule_key"] not in gap["rule_keys"]:
                    gap["rule_keys"] = sorted([*gap["rule_keys"], item["rule_key"]])
                continue
            evidence_gaps.append(
                {
                    "gap_id": _stable_hash(f"gap:{evidence_type}:{anomaly_sha256}"),
                    "evidence_type": evidence_type,
                    "purpose": f"验证 {item['rule_key']} 反证与业务实质所需材料。",
                    "rule_keys": [item["rule_key"]],
                }
            )

    plan_id = _stable_hash(f"{anomaly_sha256}|{max_steps}")
    return {
        "contract_id": "investigation-plan-draft",
        "contract_version": "1.0.0",
        "anomaly_set_sha256": anomaly_sha256,
        **({"evidence_bundle_sha256": evidence_sha256} if evidence_sha256 else {}),
        "plan_id": plan_id,
        "period": period,
        "rule_pack_sha256": rule_pack_sha256,
        "steps": steps,
        "evidence_gaps": evidence_gaps,
        "counter_hypotheses": counter_hypotheses,
        "summary": {
            "covered_candidates": sum(len(entries) for entries in grouped.values()),
            "covered_rules": len(grouped),
            "steps": len(steps),
            "evidence_gaps": len(evidence_gaps),
            "counter_hypotheses": len(counter_hypotheses),
            "truncated": truncated,
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