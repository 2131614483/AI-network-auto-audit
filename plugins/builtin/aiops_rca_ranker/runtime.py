"""Isolated implementation for the verified read-only rca-ranker plugin."""

from __future__ import annotations

import hashlib
import json
import os
import sys
from collections import defaultdict, deque
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

MAX_INPUT_BYTES = 100 * 1024 * 1024
MAX_INCIDENTS = 100_000
MAX_NODES = 200_000
MAX_EDGES = 500_000
TEXT_FIELDS = ("fault_description", "affected_system", "equipment_type", "root_cause", "solution_applied")
SEVERITY_RANK = {"high": 3, "medium": 2, "low": 1}
SEVERITY_ALIASES = {"高": "high", "中": "medium", "低": "low", "严重": "high", "紧急": "high", "warning": "medium", "info": "low"}


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


def _severity(raw: object) -> str:
    if not isinstance(raw, str):
        return "medium"
    text = raw.strip().lower()
    if text in SEVERITY_ALIASES:
        return SEVERITY_ALIASES[text]
    if text in SEVERITY_RANK:
        return text
    return "medium"


def _incident_id(incident: dict[str, Any]) -> str:
    for key in ("ticket_id", "incident_id", "alert_id", "id", "event_id"):
        value = incident.get(key)
        if isinstance(value, str) and value:
            return value
    return json.dumps(incident, ensure_ascii=False, sort_keys=True)


def _build_index(graph: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], dict[str, list[tuple[str, float, float]]]]:
    nodes_by_type = graph.get("nodes_by_type")
    if not isinstance(nodes_by_type, dict):
        raise InputRejected("topology graph must carry nodes_by_type")
    nodes: dict[str, dict[str, Any]] = {}
    node_count = 0
    for _node_type, raw_nodes in nodes_by_type.items():
        if not isinstance(raw_nodes, list):
            raise InputRejected("topology nodes_by_type must be lists")
        for raw in raw_nodes:
            if not isinstance(raw, dict):
                raise InputRejected("each topology node must be an object")
            node_id = raw.get("id")
            text = raw.get("text")
            if not isinstance(node_id, str) or not isinstance(text, str) or not text:
                continue
            node_count += 1
            if node_count > MAX_NODES:
                raise InputRejected("topology graph exceeds the fixed node budget")
            nodes[node_id] = {
                "id": node_id,
                "text": text,
                "node_type": str(raw.get("node_type") or str(_node_type)),
                "confidence": raw.get("confidence") if isinstance(raw.get("confidence"), (int, float)) else 0.5,
            }
    raw_edges = graph.get("edges")
    if not isinstance(raw_edges, dict):
        raise InputRejected("topology graph must carry edges")
    reverse: dict[str, list[tuple[str, float, float]]] = defaultdict(list)
    edge_count = 0
    for _key, edge_group in raw_edges.items():
        if not isinstance(edge_group, list):
            continue
        for edge in edge_group:
            if not isinstance(edge, dict):
                continue
            source = edge.get("source")
            target = edge.get("target")
            if not isinstance(source, str) or not isinstance(target, str):
                continue
            edge_count += 1
            if edge_count > MAX_EDGES:
                raise InputRejected("topology graph exceeds the fixed edge budget")
            confidence = edge.get("confidence") if isinstance(edge.get("confidence"), (int, float)) else 0.5
            strength = edge.get("strength") if isinstance(edge.get("strength"), (int, float)) else 0.5
            reverse[target].append((source, float(confidence), float(strength)))
    return nodes, reverse


def _text_matches(node_text: str, incident_text: str) -> bool:
    if not node_text or not incident_text:
        return False
    return node_text in incident_text


def handle(envelope: dict[str, Any]) -> dict[str, Any]:
    if envelope.get("protocol") != "audit-network-plugin-child-v1":
        raise InputRejected("unsupported child protocol")
    if envelope.get("plugin_id") != "aiops.rca-ranker":
        raise InputRejected("unexpected plugin identity")
    if envelope.get("capability") != "aiops.rca.rank":
        raise InputRejected("unexpected capability")
    rca_input = envelope.get("rca")
    if not isinstance(rca_input, dict):
        raise InputRejected("rca input is missing")
    roots = _allowed_roots()
    incidents_path, incidents_sha256 = _read_verified(rca_input.get("incident_set"), roots, "incident set")
    topology_path, topology_sha256 = _read_verified(rca_input.get("topology_graph"), roots, "topology graph")
    max_candidates = rca_input.get("max_candidates")
    if not isinstance(max_candidates, int) or not (1 <= max_candidates <= 100):
        raise InputRejected("max_candidates is outside the fixed budget")
    max_hops = rca_input.get("max_hops")
    if not isinstance(max_hops, int) or not (1 <= max_hops <= 3):
        raise InputRejected("max_hops is outside the fixed budget")

    parsed_incidents = _parse_json(incidents_path, "incident set")
    if not isinstance(parsed_incidents, list):
        raise InputRejected("incident set must be a JSON array")
    if len(parsed_incidents) > MAX_INCIDENTS:
        raise InputRejected("incident set exceeds the fixed event budget")

    graph = _parse_json(topology_path, "topology graph")
    if not isinstance(graph, dict):
        raise InputRejected("topology graph must be a JSON object")
    nodes, reverse = _build_index(graph)

    # For each incident, collect matched node ids and per-incident evidence.
    incident_matches: dict[str, set[str]] = {}
    incident_direct: dict[str, set[str]] = {}
    for raw in parsed_incidents:
        if not isinstance(raw, dict):
            raise InputRejected("each incident must be an object")
        incident_id = _incident_id(raw)
        text_parts: list[str] = []
        for field in TEXT_FIELDS:
            value = raw.get(field)
            if isinstance(value, str) and value:
                text_parts.append(value)
        incident_text = " ".join(text_parts)
        matched: set[str] = set()
        for node_id, node in nodes.items():
            if _text_matches(node["text"], incident_text):
                matched.add(node_id)
        incident_matches[incident_id] = matched
        direct: set[str] = set()
        root_cause = raw.get("root_cause")
        if isinstance(root_cause, str) and root_cause:
            for node_id, node in nodes.items():
                if _text_matches(node["text"], root_cause):
                    direct.add(node_id)
        incident_direct[incident_id] = direct

    # Bounded upstream propagation: mark candidate node ids reachable from matched
    # nodes within max_hops, per incident, with accumulated edge strength.
    candidate_support: dict[str, set[str]] = defaultdict(set)
    candidate_strength: dict[str, list[float]] = defaultdict(list)
    total_incidents = len(parsed_incidents)
    for incident_id, matched in incident_matches.items():
        for start in matched:
            queue: deque[tuple[str, int]] = deque([(start, 0)])
            seen: set[str] = {start}
            while queue:
                node_id, depth = queue.popleft()
                if depth > 0:
                    candidate_support[node_id].add(incident_id)
                if depth >= max_hops:
                    continue
                for source, _conf, strength in reverse.get(node_id, []):
                    if source in seen:
                        continue
                    seen.add(source)
                    candidate_strength[source].append(float(strength))
                    queue.append((source, depth + 1))
    # Direct root-cause names are strong candidates even without graph reachability.
    for incident_id, direct in incident_direct.items():
        for node_id in direct:
            candidate_support[node_id].add(incident_id)

    # Build candidates from every reachable node, scoring by incident support.
    raw_candidates: list[dict[str, Any]] = []
    for node_id, supporting in candidate_support.items():
        node = nodes.get(node_id)
        if node is None:
            continue
        support_count = len(supporting)
        direct_count = sum(1 for incident_id in supporting if node_id in incident_direct.get(incident_id, set()))
        strength_values = candidate_strength.get(node_id) or [0.5]
        avg_strength = sum(strength_values) / len(strength_values)
        support_fraction = support_count / max(1, total_incidents)
        score = min(1.0, round(0.5 * support_fraction + 0.5 * min(1.0, direct_count / max(1, support_count)) + 0.1 * avg_strength, 6))
        confidence = min(1.0, round(0.4 + 0.3 * float(node["confidence"]) + 0.1 * avg_strength, 6))
        raw_candidates.append(
            {
                "candidate_id": node_id,
                "root_cause": node["text"],
                "node_ref": node_id,
                "score": min(1.0, score),
                "confidence": confidence,
                "support_count": support_count,
                "supporting_incidents": sorted(supporting),
                "direct_count": direct_count,
                "evidence": {
                    "node_type": node["node_type"],
                    "avg_strength": round(avg_strength, 6),
                    "matched_fields": ["root_cause"] if direct_count else ["affected_system"],
                    "source_sha256": incidents_sha256,
                },
            }
        )

    raw_candidates.sort(key=lambda item: (-item["score"], -item["support_count"], item["node_ref"]))
    truncated = len(raw_candidates) > max_candidates
    candidates = []
    for rank, item in enumerate(raw_candidates[:max_candidates], start=1):
        candidate = dict(item)
        candidate.pop("direct_count", None)
        candidate["rank"] = rank
        candidates.append(candidate)

    return {
        "contract_id": "rca-candidates",
        "contract_version": "1.0.0",
        "incident_set_sha256": incidents_sha256,
        "topology_sha256": topology_sha256,
        "window_minutes": rca_input.get("window_minutes"),
        "max_hops": max_hops,
        "summary": {
            "incidents_processed": total_incidents,
            "nodes_considered": len(nodes),
            "candidate_count": len(candidates),
            "truncated": truncated,
        },
        "candidates": candidates,
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
