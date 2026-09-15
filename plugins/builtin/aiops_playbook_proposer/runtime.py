"""Isolated implementation for the verified read-only playbook-proposer plugin.

The plugin turns an already-extracted ``rca-candidates`` artifact into a
deterministic AIOps remediation **proposal**: for each ranked root-cause
candidate it selects a fixed Playbook by keyword matching, and emits
preconditions, structured actions (never free Shell), a canary suggestion and a
rollback strategy.

It never generates a free Shell command, never creates a ChangeRequest and
never executes any change: the proposal is only materialized as output, and the
AIOps domain service stages it into the shadow remediation-proposal store after
a policy verdict.  Execution remains behind Policy Gateway, ChangeRequest and
human approval.
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

MAX_INPUT_BYTES = 100 * 1024 * 1024
MAX_CANDIDATES = 100
MAX_PLAYBOOKS = 20
MAX_PROPOSALS = 20
MAX_INCIDENT_REFS = 64
CANARY_SCOPES = ("single_node", "subset_10pct", "low_traffic")

# Deterministic Playbook library.  Matching is done on root-cause text; the
# proposals are shadow drafts only and contain no free-form command payloads.
_PLAYBOOKS: list[dict[str, Any]] = [
    {
        "playbook_id": "restart-service",
        "title": "服务/进程重启恢复",
        "keywords": ["restart", "service", "process", "crash", "崩溃", "重启", "挂起", "无响应", "卡死", "oom", "僵死"],
        "preconditions": [
            "变更前已确认受影响服务存在副本或可降级路径",
            "已核对最近一次发布/配置变更记录",
            "已确认该服务不承载未持久化的关键状态",
        ],
        "actions": [
            {"step": 1, "action_type": "drain", "target": "service", "params": {"graceful_shutdown_seconds": 30}, "verify": "在途请求数归零"},
            {"step": 2, "action_type": "restart", "target": "service", "params": {"restart_count": 1}, "verify": "进程存活且健康检查通过"},
            {"step": 3, "action_type": "probe", "target": "service", "params": {"probe_type": "http_health"}, "verify": "连续 3 次探测返回 200"},
        ],
        "canary": {
            "suggested": True,
            "scope": "single_node",
            "ratio": 0.1,
            "watch_minutes": 15,
            "traffic_criteria": ["错误率低于基线 1.5 倍", "P99 延迟低于基线 1.5 倍"],
            "abort_conditions": ["错误率上升超过 20%", "健康检查连续失败 3 次"],
        },
        "rollback": {"available": True, "strategy": "restart_previous", "criteria": ["canary 窗口内错误率未收敛"], "auto_rollback": False},
        "verification": {"expected_signals": ["错误率回落至基线", "请求成功率回到 99.9% 以上"], "probes": ["http_health", "metric_error_rate"], "watch_minutes": 30},
    },
    {
        "playbook_id": "scale-resource",
        "title": "资源扩容/容量恢复",
        "keywords": ["内存", "磁盘", "cpu", "capacity", "容量", "saturation", "耗尽", "泄漏", "leak", "满载", "爆满", "空间不足"],
        "preconditions": [
            "已确认扩容目标存在配额与预算",
            "已确认负载曲线在扩容阈值内且无异常流量来源",
        ],
        "actions": [
            {"step": 1, "action_type": "probe", "target": "resource", "params": {"probe_type": "usage_metric"}, "verify": "确认当前使用率与趋势"},
            {"step": 2, "action_type": "scale", "target": "resource", "params": {"increment_pct": 30}, "verify": "使用率回落到安全水位"},
            {"step": 3, "action_type": "probe", "target": "resource", "params": {"probe_type": "saturation_metric"}, "verify": "连续 3 次采样未触及上限"},
        ],
        "canary": {
            "suggested": True,
            "scope": "subset_10pct",
            "ratio": 0.1,
            "watch_minutes": 20,
            "traffic_criteria": ["扩容后无新的饱和度告警"],
            "abort_conditions": ["扩容后饱和度不降反升"],
        },
        "rollback": {"available": True, "strategy": "drain_and_failover", "criteria": ["扩容未改善且触发新的风险"], "auto_rollback": False},
        "verification": {"expected_signals": ["饱和度降至目标区间"], "probes": ["usage_metric", "saturation_metric"], "watch_minutes": 60},
    },
    {
        "playbook_id": "config-rollback",
        "title": "配置/版本回滚",
        "keywords": ["config", "配置", "变更", "change", "版本", "version", "发布", "deploy", "回滚", "rollback", "上线", "灰度"],
        "preconditions": [
            "已确认最近变更窗口内存在配置或版本变更",
            "已确认回滚目标版本存在可恢复备份",
        ],
        "actions": [
            {"step": 1, "action_type": "probe", "target": "config", "params": {"probe_type": "config_diff"}, "verify": "确认当前生效配置与最近变更"},
            {"step": 2, "action_type": "revert", "target": "config", "params": {"revert_to": "previous_version"}, "verify": "配置哈希恢复到上一版本"},
            {"step": 3, "action_type": "probe", "target": "config", "params": {"probe_type": "consistency"}, "verify": "所有副本配置一致"},
        ],
        "canary": {
            "suggested": True,
            "scope": "single_node",
            "ratio": 0.1,
            "watch_minutes": 15,
            "traffic_criteria": ["回滚后告警率下降"],
            "abort_conditions": ["回滚后出现新的不一致告警"],
        },
        "rollback": {"available": True, "strategy": "revert_config", "criteria": ["回滚未收敛且配置状态异常"], "auto_rollback": False},
        "verification": {"expected_signals": ["配置一致且告警收敛"], "probes": ["config_diff", "consistency"], "watch_minutes": 30},
    },
    {
        "playbook_id": "network-failover",
        "title": "网络切换/故障转移",
        "keywords": ["网络", "network", "连接", "connect", "timeout", "超时", "断连", "路由", "route", "failover", "丢包", "抖动"],
        "preconditions": [
            "已确认存在备路或备节点且容量足够",
            "已确认故障域不包含备路承载链路",
        ],
        "actions": [
            {"step": 1, "action_type": "drain", "target": "connection", "params": {"drain_seconds": 30}, "verify": "在途连接迁移完成"},
            {"step": 2, "action_type": "failover", "target": "link", "params": {"fallback": "standby"}, "verify": "流量切换到备路"},
            {"step": 3, "action_type": "probe", "target": "link", "params": {"probe_type": "connectivity"}, "verify": "连续 3 次探测成功"},
        ],
        "canary": {
            "suggested": True,
            "scope": "low_traffic",
            "ratio": 0.1,
            "watch_minutes": 10,
            "traffic_criteria": ["切换后丢包率低于 0.1%"],
            "abort_conditions": ["切换后连通性中断"],
        },
        "rollback": {"available": True, "strategy": "drain_and_failover", "criteria": ["备路质量不达标"], "auto_rollback": False},
        "verification": {"expected_signals": ["连通性恢复且丢包率为零"], "probes": ["connectivity", "metric_packet_loss"], "watch_minutes": 20},
    },
    {
        "playbook_id": "db-connection-recovery",
        "title": "数据库连接池/锁恢复",
        "keywords": ["数据库", "db", "database", "连接池", "pool", "锁", "lock", "死锁", "deadlock", "事务", "慢查询", "sql"],
        "preconditions": [
            "已确认存在可回收的长期事务或死锁会话",
            "已确认业务对连接池大小的容忍上限",
        ],
        "actions": [
            {"step": 1, "action_type": "probe", "target": "database", "params": {"probe_type": "connection_audit"}, "verify": "定位长事务与锁等待会话"},
            {"step": 2, "action_type": "drain", "target": "database", "params": {"terminate_stale_sessions": True}, "verify": "锁等待会话数归零"},
            {"step": 3, "action_type": "probe", "target": "database", "params": {"probe_type": "latency"}, "verify": "查询延迟回到基线"},
        ],
        "canary": {
            "suggested": True,
            "scope": "low_traffic",
            "ratio": 0.1,
            "watch_minutes": 20,
            "traffic_criteria": ["连接池空闲率恢复", "无新的锁等待告警"],
            "abort_conditions": ["连接池再次打满"],
        },
        "rollback": {"available": True, "strategy": "restart_previous", "criteria": ["恢复动作导致新会话失败"], "auto_rollback": False},
        "verification": {"expected_signals": ["锁等待归零且延迟回落"], "probes": ["connection_audit", "latency"], "watch_minutes": 40},
    },
    {
        "playbook_id": "cache-refresh",
        "title": "缓存重建/刷新",
        "keywords": ["缓存", "cache", "缓存不一致", "过期", "stale", "刷新", "reload", "穿透"],
        "preconditions": [
            "已确认缓存来源数据可用且可重建",
            "已确认重建窗口内可接受短暂回源",
        ],
        "actions": [
            {"step": 1, "action_type": "probe", "target": "cache", "params": {"probe_type": "consistency"}, "verify": "确认不一致键范围"},
            {"step": 2, "action_type": "clear_cache", "target": "cache", "params": {"keyspace": "stale_keys"}, "verify": "陈旧键清空"},
            {"step": 3, "action_type": "probe", "target": "cache", "params": {"probe_type": "hit_rate"}, "verify": "命中率恢复"},
        ],
        "canary": {
            "suggested": False,
            "scope": "single_node",
            "ratio": 0.0,
            "watch_minutes": 10,
            "traffic_criteria": ["回源流量未超过阈值"],
            "abort_conditions": ["回源导致源端压力告警"],
        },
        "rollback": {"available": True, "strategy": "revert_config", "criteria": ["命中率未恢复且回源压力升高"], "auto_rollback": False},
        "verification": {"expected_signals": ["缓存一致且命中率恢复"], "probes": ["consistency", "hit_rate"], "watch_minutes": 20},
    },
]


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


def _match_playbooks(candidate: dict[str, Any]) -> list[tuple[dict[str, Any], list[str]]]:
    """Deterministic keyword matching over the candidate root-cause text."""
    root_cause = candidate.get("root_cause")
    node_ref = candidate.get("node_ref")
    evidence = candidate.get("evidence")
    parts: list[str] = []
    if isinstance(root_cause, str):
        parts.append(root_cause)
    if isinstance(node_ref, str):
        parts.append(node_ref)
    if isinstance(evidence, dict):
        node_type = evidence.get("node_type")
        if isinstance(node_type, str):
            parts.append(node_type)
    text = " ".join(parts).lower()
    matched: list[tuple[dict[str, Any], list[str]]] = []
    for playbook in _PLAYBOOKS:
        hits: list[str] = []
        for keyword in playbook["keywords"]:
            if keyword.lower() in text:
                hits.append(keyword)
        if hits:
            matched.append((playbook, hits))
    matched.sort(key=lambda item: (len(item[1]), item[0]["playbook_id"]))
    return matched


def _select_proposals(
    candidates: list[dict[str, Any]],
    playbook_matches: dict[str, list[tuple[dict[str, Any], list[str]]]],
    max_playbooks: int,
    canary_scope: str,
) -> list[dict[str, Any]]:
    """Deterministically rank (candidate, playbook) fits and cap the proposal set."""
    ranked: list[tuple[float, int, str, dict[str, Any], list[str]]] = []
    for rank, candidate in enumerate(candidates, start=1):
        candidate_id = str(candidate.get("candidate_id", ""))
        score = float(candidate.get("score") or 0.0)
        for playbook, hits in playbook_matches.get(candidate_id, []):
            fit = min(1.0, score * (0.6 + 0.4 * min(1.0, len(hits) / 2.0)))
            ranked.append((fit, rank, playbook["playbook_id"], playbook, hits))
    ranked.sort(key=lambda item: (-item[0], item[1], item[2]))
    seen: set[str] = set()
    proposals: list[dict[str, Any]] = []
    for fit, rank, playbook_id, playbook, hits in ranked:
        if playbook_id in seen:
            continue
        seen.add(playbook_id)
        proposal = _build_proposal(playbook, fit, rank, hits, canary_scope)
        proposals.append(proposal)
        if len(proposals) >= min(max_playbooks, MAX_PROPOSALS):
            break
    return proposals


def _build_proposal(
    playbook: dict[str, Any],
    fit: float,
    rank: int,
    hits: list[str],
    canary_scope: str,
) -> dict[str, Any]:
    canary = dict(playbook["canary"])
    canary["scope"] = canary_scope
    if canary_scope == "single_node":
        canary["ratio"] = 0.1
    elif canary_scope == "subset_10pct":
        canary["ratio"] = 0.1
    else:
        canary["ratio"] = 0.0 if not canary["suggested"] else 0.05
    confidence = round(min(0.97, max(0.5, fit * 0.8 + 0.5 * rank_fallback(rank))), 2)
    return {
        "playbook_id": playbook["playbook_id"],
        "title": playbook["title"],
        "root_cause_ref": "RC-{0:04d}".format(rank),
        "candidate_rank": rank,
        "score": round(fit, 4),
        "confidence": confidence,
        "match_terms": hits[:8],
        "preconditions": list(playbook["preconditions"]),
        "actions": list(playbook["actions"]),
        "canary": canary,
        "rollback": dict(playbook["rollback"]),
        "verification": dict(playbook["verification"]),
    }


def rank_fallback(rank: int) -> float:
    """Deterministic small confidence boost for top-ranked candidates."""
    if rank == 1:
        return 0.1
    if rank == 2:
        return 0.05
    return 0.0


def handle(envelope: dict[str, Any]) -> dict[str, Any]:
    if envelope.get("protocol") != "audit-network-plugin-child-v1":
        raise InputRejected("unsupported child protocol")
    if envelope.get("plugin_id") != "aiops.playbook-proposer":
        raise InputRejected("unexpected plugin identity")
    if envelope.get("capability") != "aiops.remediation.propose":
        raise InputRejected("unexpected capability")
    remediation_input = envelope.get("remediation")
    if not isinstance(remediation_input, dict):
        raise InputRejected("remediation input is missing")
    roots = _allowed_roots()
    rca_path, rca_sha256 = _read_verified(remediation_input.get("rca_candidates"), roots, "rca candidates")
    max_playbooks = remediation_input.get("max_playbooks")
    if not isinstance(max_playbooks, int) or not (1 <= max_playbooks <= MAX_PLAYBOOKS):
        raise InputRejected("max_playbooks is outside the fixed budget")
    canary_scope = remediation_input.get("canary_scope")
    if not isinstance(canary_scope, str) or canary_scope not in CANARY_SCOPES:
        raise InputRejected("canary_scope is outside the fixed scope set")

    rca_set = _parse_json(rca_path, "rca candidates")
    if not isinstance(rca_set, dict):
        raise InputRejected("rca candidates must be a JSON object")
    raw_candidates = rca_set.get("candidates")
    if not isinstance(raw_candidates, list):
        raise InputRejected("rca candidates must carry a candidate list")
    if len(raw_candidates) > MAX_CANDIDATES:
        raise InputRejected("rca candidates exceed the fixed event budget")
    candidates: list[dict[str, Any]] = []
    for raw in raw_candidates:
        if not isinstance(raw, dict):
            raise InputRejected("each rca candidate must be an object")
        candidate_id = raw.get("candidate_id")
        if not isinstance(candidate_id, str) or not candidate_id:
            raise InputRejected("each rca candidate must carry candidate_id")
        root_cause = raw.get("root_cause")
        if not isinstance(root_cause, str) or not root_cause:
            raise InputRejected("each rca candidate must carry root_cause")
        score = raw.get("score")
        candidates.append(
            {
                "candidate_id": candidate_id,
                "rank": raw.get("rank") if isinstance(raw.get("rank"), int) else len(candidates) + 1,
                "root_cause": root_cause,
                "node_ref": raw.get("node_ref") if isinstance(raw.get("node_ref"), str) else candidate_id,
                "score": round(float(score) if isinstance(score, (int, float)) else 0.0, 6),
                "confidence": raw.get("confidence") if isinstance(raw.get("confidence"), (int, float)) else 0.5,
                "supporting_incidents": [str(value) for value in raw.get("supporting_incidents", []) if isinstance(value, str)],
                "evidence": raw.get("evidence") if isinstance(raw.get("evidence"), dict) else {},
            }
        )
    candidates.sort(key=lambda item: (item["rank"], item["candidate_id"]))

    playbook_matches: dict[str, list[tuple[dict[str, Any], list[str]]]] = {
        candidate["candidate_id"]: _match_playbooks(candidate) for candidate in candidates
    }
    proposals = _select_proposals(candidates, playbook_matches, max_playbooks, canary_scope)

    incident_refs: list[str] = []
    for candidate in candidates:
        for incident in candidate["supporting_incidents"]:
            if incident not in incident_refs:
                incident_refs.append(incident)
            if len(incident_refs) >= MAX_INCIDENT_REFS:
                break
        if len(incident_refs) >= MAX_INCIDENT_REFS:
            break

    evidence_refs: list[str] = []
    for candidate in candidates:
        evidence = candidate["evidence"]
        for value in (evidence.get("source_sha256"),):
            if isinstance(value, str) and value and value not in evidence_refs:
                evidence_refs.append(value)
    evidence_refs = evidence_refs[:64]

    proposal_id = _stable_hash(f"{rca_sha256}|{max_playbooks}|{canary_scope}")
    truncated = len(playbook_matches) > len(proposals)
    return {
        "contract_id": "remediation-proposal",
        "contract_version": "1.0.0",
        "proposal_id": proposal_id,
        "rca_candidates_sha256": rca_sha256,
        "status": "proposed",
        "reference_time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "summary": {
            "candidates_processed": len(candidates),
            "playbook_count": len(proposals),
            "canary_suggested": any(proposal["canary"]["suggested"] for proposal in proposals),
            "rollback_available": all(proposal["rollback"]["available"] for proposal in proposals) if proposals else False,
            "truncated": truncated,
        },
        "incident_refs": incident_refs,
        "constraints": {
            "no_shell": True,
            "no_change_request": True,
            "no_auto_execution": True,
            "requires_human_approval": True,
        },
        "evidence_refs": evidence_refs,
        "proposals": proposals,
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
