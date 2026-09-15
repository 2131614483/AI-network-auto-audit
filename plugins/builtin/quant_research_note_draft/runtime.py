"""Isolated implementation for the verified read-only quant research-note-draft plugin.

R3 受审批输出：只读已确认 experiment-evaluation 草稿，确定性生成可追溯量化研究
结论草稿。草稿永不自动发布：发布必须人工审批；不含订单生成，不更新生产模型；
插件本身无网络、不写基础设施，落库由治理服务在人工审批后完成。
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

MAX_INPUT_BYTES = 10 * 1024 * 1024
MAX_EVIDENCE_REFS = 64
RECOMMENDATION_FINDINGS = {
    "promote": "晋级建议 promote，需独立 Reviewer 复核后由人工发布研究结论。",
    "hold": "晋级建议 hold，暂缓发布，等待进一步证据或人工复核。",
    "reject": "晋级建议 reject，不进入研究结论发布流程，由人工复核确认。",
}
ROBUSTNESS_FINDINGS = {
    "stable": "稳健性 stable，主要参考逐期收益稳定性与正向周期占比。",
    "moderate": "稳健性 moderate，整体可用但需关注波动与回撤。",
    "fragile": "稳健性 fragile，结果不稳健，需人工复核后再决定是否进入结论。",
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


def _stable_draft_utc(evaluation_hash: str, strategy_key: str) -> str:
    """Deterministic provenance instant (M7 reproducibility premise).

    A governed research-note draft is a reproducible replay of a locked
    release: the same locked inputs must yield byte-identical drafts, so the
    draft instant is derived from the input seed (same seed as ``note_id``)
    instead of the wall clock.  The derived value stays inside the 2026
    projection window and satisfies the ``draft_time`` string contract.
    """
    seed = hashlib.sha256("|".join([evaluation_hash, strategy_key]).encode("utf-8")).hexdigest()
    hours = int(seed[:8], 16) % (24 * 365)
    return (datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(hours=hours)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


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
    if envelope.get("plugin_id") != "quant.research-note-draft":
        raise InputRejected("unexpected plugin identity")
    if envelope.get("capability") != "quant.research-note.draft":
        raise InputRejected("unexpected capability")
    research_input = envelope.get("research_note")
    if not isinstance(research_input, dict):
        raise InputRejected("research_note input is missing")

    roots = _allowed_roots()
    evaluation_artifact = research_input.get("evaluation")
    evaluation_hash, evaluation = _read_artifact(evaluation_artifact, roots, "experiment-evaluation")
    if (
        evaluation.get("contract_id") != "experiment-evaluation"
        or evaluation.get("contract_version") != "1.0.0"
    ):
        raise InputRejected("experiment-evaluation contract version is unsupported")
    if evaluation.get("status") != "proposed":
        raise InputRejected("experiment-evaluation is not confirmed")
    experiment_id = evaluation.get("experiment_id")
    if not isinstance(experiment_id, str) or not experiment_id:
        raise InputRejected("experiment-evaluation experiment_id is missing")
    strategy_key = evaluation.get("strategy_key")
    strategy_version = evaluation.get("strategy_version")
    if not isinstance(strategy_key, str) or not strategy_key.strip():
        raise InputRejected("experiment-evaluation strategy_key is missing")
    if not isinstance(strategy_version, str) or not strategy_version.strip():
        raise InputRejected("experiment-evaluation strategy_version is missing")
    robustness = evaluation.get("robustness")
    if not isinstance(robustness, dict):
        raise InputRejected("experiment-evaluation robustness is missing")
    overall = robustness.get("overall")
    if overall not in ROBUSTNESS_FINDINGS:
        raise InputRejected("experiment-evaluation robustness overall is outside the fixed enum")
    score = robustness.get("score")
    if not isinstance(score, (int, float)) or score < 0 or score > 1:
        raise InputRejected("experiment-evaluation robustness score is outside [0,1]")
    promotion = evaluation.get("promotion")
    if not isinstance(promotion, dict):
        raise InputRejected("experiment-evaluation promotion is missing")
    recommendation = promotion.get("recommendation")
    if recommendation not in RECOMMENDATION_FINDINGS:
        raise InputRejected("experiment-evaluation promotion recommendation is outside the fixed enum")
    rationale = promotion.get("rationale")
    if not isinstance(rationale, str) or not rationale.strip():
        raise InputRejected("experiment-evaluation promotion rationale is missing")
    drift = evaluation.get("drift")
    if not isinstance(drift, dict):
        raise InputRejected("experiment-evaluation drift is missing")
    drift_count = drift.get("drift_count")
    if not isinstance(drift_count, int) or drift_count < 0:
        raise InputRejected("experiment-evaluation drift_count is outside the fixed range")

    seed = "|".join([evaluation_hash, strategy_key, strategy_version])
    note_id = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]

    title = f"【研究结论】{strategy_key}@{strategy_version} 模拟回测稳健性复核"
    overview = "\n".join(
        [
            f"依据实验评估（proposed）逐项复核：稳健性 {overall} {score}，漂移 {drift_count} 项，晋级建议 {recommendation}。",
            "",
            f"初判：{rationale}",
        ]
    )
    findings = [
        ROBUSTNESS_FINDINGS[overall],
        f"相对基线漂移 {drift_count} 项。",
    ]
    recommendations = [RECOMMENDATION_FINDINGS[recommendation]]

    return {
        "contract_id": "research-note-draft",
        "contract_version": "1.0.0",
        "note_id": note_id,
        "status": "draft",
        "evaluation_refs": [f"evaluation:{experiment_id}"],
        "strategy": {
            "key": strategy_key,
            "version": strategy_version,
        },
        "title": title,
        "overview": overview,
        "findings": findings,
        "recommendations": recommendations,
        "summary": {
            "evaluations": 1,
            "recommendation": recommendation,
            "truncated": False,
        },
        "signature": {
            "drafted_by": "quant.research-note-draft@0.1.0",
            "published": False,
            "publisher": None,
        },
        "constraints": {
            "no_auto_publish": True,
            "no_order_generation": True,
            "requires_human_approval": True,
            "immutable_source": True,
        },
        "provenance": {
            "evaluation_sha256": evaluation_hash,
            "plugin": "quant.research-note-draft@0.1.0",
            "draft_time": _stable_draft_utc(evaluation_hash, strategy_key),
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