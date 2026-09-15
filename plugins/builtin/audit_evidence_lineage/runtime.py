"""Isolated implementation for the verified read-only evidence-lineage plugin.

The plugin turns a ``released-graph-ref`` artifact into a deterministic
evidence lineage query result: Evidence / Claim / Finding / Artifact /
Document nodes and directed edges, bounded by the caller-declared node, edge
and hop budgets.  It never writes a Finding, never mutates the released graph
and never touches the host: the lineage is only materialized as output, and the
audit domain service persists it as read-only evidence after a policy verdict.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from collections import deque
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

MAX_INPUT_BYTES = 50 * 1024 * 1024
NODE_TYPES = frozenset({"evidence", "claim", "finding", "artifact", "document"})
EDGE_TYPES = frozenset({"supports", "refutes", "derived_from", "references", "part_of"})
SPACE_LEVELS = frozenset({"L0", "L1", "L2", "L3", "L4"})


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
        raise InputRejected("graph URI is missing")
    parsed = urlparse(uri)
    if parsed.scheme != "file" or parsed.netloc not in {"", "localhost"}:
        raise InputRejected("graph URI must be local")
    raw_path = unquote(parsed.path)
    if raw_path.startswith("/") and len(raw_path) >= 3 and raw_path[2] == ":":
        raw_path = raw_path[1:]
    try:
        resolved = Path(raw_path).resolve(strict=True)
    except OSError as exc:
        raise InputRejected("graph file is unavailable") from exc
    if not resolved.is_file():
        raise InputRejected("graph is not a regular file")
    try:
        next(root for root in roots if resolved.is_relative_to(root))
    except StopIteration as exc:
        raise InputRejected("graph is outside declared read roots") from exc
    return resolved


def _read_verified_graph(artifact: Any, roots: tuple[Path, ...]) -> tuple[dict[str, Any], str]:
    if not isinstance(artifact, dict):
        raise InputRejected("graph artifact reference is missing")
    path = _resolve_file(artifact.get("uri"), roots)
    content = path.read_bytes()
    if len(content) > MAX_INPUT_BYTES:
        raise InputRejected("released graph exceeds local read budget")
    expected_size = artifact.get("size_bytes")
    if not isinstance(expected_size, int) or expected_size != len(content):
        raise InputRejected("graph size does not match reference")
    expected_hash = artifact.get("sha256")
    actual_hash = hashlib.sha256(content).hexdigest()
    if not isinstance(expected_hash, str) or actual_hash.lower() != expected_hash.lower():
        raise InputRejected("graph sha256 does not match reference")
    try:
        graph = json.loads(content.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InputRejected("released graph must be UTF-8 JSON") from exc
    if not isinstance(graph, dict):
        raise InputRejected("released graph must be a single JSON object")
    return graph, actual_hash


def _validated_nodes(raw_nodes: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_nodes, list) or not raw_nodes:
        raise InputRejected("released graph nodes are missing")
    if len(raw_nodes) > 100_000:
        raise InputRejected("released graph exceeds the fixed node budget")
    seen: set[str] = set()
    validated: list[dict[str, Any]] = []
    for raw in raw_nodes:
        if not isinstance(raw, dict):
            raise InputRejected("released graph node must be an object")
        node_id = raw.get("node_id")
        if not isinstance(node_id, str) or not node_id or len(node_id) > 255:
            raise InputRejected("released graph node_id is invalid")
        if node_id in seen:
            raise InputRejected("released graph node_id is duplicated")
        node_type = raw.get("node_type")
        if node_type not in NODE_TYPES:
            raise InputRejected("released graph node_type is outside the fixed enum")
        label = raw.get("label")
        if not isinstance(label, str) or not label.strip() or len(label) > 512:
            raise InputRejected("released graph node label is invalid")
        seen.add(node_id)
        node: dict[str, Any] = {"node_id": node_id, "node_type": node_type, "label": label}
        source_ref = raw.get("source_ref")
        if isinstance(source_ref, str) and source_ref:
            if len(source_ref) > 2048:
                raise InputRejected("released graph node source_ref is invalid")
            node["source_ref"] = source_ref
        if node_type == "evidence":
            artifact = raw.get("artifact")
            if isinstance(artifact, dict):
                node["artifact"] = artifact
        validated.append(node)
    return validated


def _validated_edges(raw_edges: Any, node_ids: set[str]) -> list[dict[str, Any]]:
    if not isinstance(raw_edges, list):
        raise InputRejected("released graph edges are missing")
    if len(raw_edges) > 500_000:
        raise InputRejected("released graph exceeds the fixed edge budget")
    seen: set[tuple[str, str, str]] = set()
    validated: list[dict[str, Any]] = []
    for raw in raw_edges:
        if not isinstance(raw, dict):
            raise InputRejected("released graph edge must be an object")
        source_id = raw.get("source_id")
        target_id = raw.get("target_id")
        edge_type = raw.get("edge_type")
        if not isinstance(source_id, str) or not source_id or len(source_id) > 255:
            raise InputRejected("released graph edge source_id is invalid")
        if not isinstance(target_id, str) or not target_id or len(target_id) > 255:
            raise InputRejected("released graph edge target_id is invalid")
        if edge_type not in EDGE_TYPES:
            raise InputRejected("released graph edge_type is outside the fixed enum")
        if source_id not in node_ids or target_id not in node_ids:
            raise InputRejected("released graph edge references an unknown node")
        key = (source_id, target_id, edge_type)
        if key in seen:
            raise InputRejected("released graph edge is duplicated")
        seen.add(key)
        edge: dict[str, Any] = {"source_id": source_id, "target_id": target_id, "edge_type": edge_type}
        confidence = raw.get("confidence")
        if confidence is not None:
            if not isinstance(confidence, (int, float)) or isinstance(confidence, bool) or not 0 <= confidence <= 1:
                raise InputRejected("released graph edge confidence is outside [0, 1]")
            edge["confidence"] = round(float(confidence), 4)
        validated.append(edge)
    return validated


def _validated_budget(raw_budget: Any) -> dict[str, int]:
    if not isinstance(raw_budget, dict):
        raise InputRejected("lineage budget is missing")
    max_nodes = raw_budget.get("max_nodes")
    max_edges = raw_budget.get("max_edges")
    max_hops = raw_budget.get("max_hops")
    timeout_seconds = raw_budget.get("timeout_seconds")
    if not isinstance(max_nodes, int) or isinstance(max_nodes, bool) or not 1 <= max_nodes <= 100_000:
        raise InputRejected("lineage budget max_nodes is outside the fixed range")
    if not isinstance(max_edges, int) or isinstance(max_edges, bool) or not 1 <= max_edges <= 500_000:
        raise InputRejected("lineage budget max_edges is outside the fixed range")
    if not isinstance(max_hops, int) or isinstance(max_hops, bool) or not 1 <= max_hops <= 8:
        raise InputRejected("lineage budget max_hops is outside the fixed range")
    if not isinstance(timeout_seconds, int) or isinstance(timeout_seconds, bool) or not 1 <= timeout_seconds <= 300:
        raise InputRejected("lineage budget timeout_seconds is outside the fixed range")
    return {"max_nodes": max_nodes, "max_edges": max_edges, "max_hops": max_hops, "timeout_seconds": timeout_seconds}


def handle(envelope: dict[str, Any]) -> dict[str, Any]:
    if envelope.get("protocol") != "audit-network-plugin-child-v1":
        raise InputRejected("unsupported child protocol")
    if envelope.get("plugin_id") != "audit.evidence-lineage":
        raise InputRejected("unexpected plugin identity")
    if envelope.get("capability") != "audit.evidence.lineage":
        raise InputRejected("unexpected capability")
    lineage_input = envelope.get("lineage")
    if not isinstance(lineage_input, dict):
        raise InputRejected("evidence lineage input is missing")
    graph_ref = lineage_input.get("graph_ref")
    if not isinstance(graph_ref, dict):
        raise InputRejected("released graph reference is missing")
    artifact = graph_ref.get("artifact")
    graph, graph_sha256 = _read_verified_graph(artifact, _allowed_roots())

    if graph.get("contract_id") != "released-graph" or graph.get("contract_version") != "1.0.0":
        raise InputRejected("released graph contract version is unsupported")
    declared_release_hash = graph.get("release_sha256")
    ref_release_hash = graph_ref.get("release_sha256")
    if not isinstance(declared_release_hash, str) or not isinstance(ref_release_hash, str):
        raise InputRejected("released graph release_sha256 is missing")
    if declared_release_hash.lower() != ref_release_hash.lower():
        raise InputRejected("released graph release_sha256 does not match reference")
    release_sha256 = declared_release_hash.lower()

    space_level = graph.get("space_level")
    ref_space_level = graph_ref.get("space_level")
    if space_level not in SPACE_LEVELS or ref_space_level not in SPACE_LEVELS or space_level != ref_space_level:
        raise InputRejected("released graph space_level does not match reference")
    budget = _validated_budget(graph_ref.get("budget"))

    nodes = _validated_nodes(graph.get("nodes"))
    node_ids = {node["node_id"] for node in nodes}
    edges = _validated_edges(graph.get("edges"), node_ids)

    max_nodes = budget["max_nodes"]
    max_edges = budget["max_edges"]
    max_hops = budget["max_hops"]

    adjacency: dict[str, list[tuple[str, str, str, float | None]]] = {node_id: [] for node_id in node_ids}
    for edge in edges:
        source_id, target_id, edge_type = edge["source_id"], edge["target_id"], edge["edge_type"]
        confidence = edge.get("confidence")
        adjacency[source_id].append((target_id, edge_type, confidence))
        adjacency[target_id].append((source_id, edge_type, confidence))
    for neighbors in adjacency.values():
        neighbors.sort(key=lambda item: (item[0], item[1]))

    seeds = sorted(node["node_id"] for node in nodes if node["node_type"] == "evidence")
    visited: list[str] = []
    truncated = False
    if seeds:
        if len(seeds) > max_nodes:
            seeds = seeds[:max_nodes]
            truncated = True
        seen_nodes = set(seeds)
        visited = list(seeds)
        frontier = deque(seeds)
        for _hop in range(max_hops):
            if not frontier:
                break
            next_frontier: list[str] = []
            while frontier:
                node_id = frontier.popleft()
                for neighbor_id, _edge_type, _confidence in adjacency.get(node_id, ()):
                    if neighbor_id in seen_nodes:
                        continue
                    if len(seen_nodes) >= max_nodes:
                        truncated = True
                        break
                    seen_nodes.add(neighbor_id)
                    visited.append(neighbor_id)
                    next_frontier.append(neighbor_id)
                if truncated:
                    break
            frontier = deque(sorted(next_frontier))
            if truncated:
                break
        visited.sort()
    else:
        truncated = True

    included = set(visited)
    output_edges = [edge for edge in edges if edge["source_id"] in included and edge["target_id"] in included]
    output_edges.sort(key=lambda edge: (edge["source_id"], edge["target_id"], edge["edge_type"]))
    if len(output_edges) > max_edges:
        output_edges = output_edges[:max_edges]
        truncated = True

    output_nodes = [node for node in nodes if node["node_id"] in included]
    output_nodes.sort(key=lambda node: node["node_id"])
    evidence_count = sum(1 for node in output_nodes if node["node_type"] == "evidence")
    claim_count = sum(1 for node in output_nodes if node["node_type"] == "claim")
    finding_count = sum(1 for node in output_nodes if node["node_type"] == "finding")

    lineage_seed = "|".join(
        (
            release_sha256,
            space_level,
            str(max_nodes),
            str(max_edges),
            str(max_hops),
        )
    )
    lineage_id = hashlib.sha256(lineage_seed.encode("utf-8")).hexdigest()[:16]

    return {
        "contract_id": "evidence-lineage",
        "contract_version": "1.0.0",
        "lineage_id": lineage_id,
        "release_sha256": release_sha256,
        "truncated": truncated,
        "node_count": len(output_nodes),
        "edge_count": len(output_edges),
        "nodes": output_nodes,
        "edges": output_edges,
        "summary": {
            "evidence_count": evidence_count,
            "claim_count": claim_count,
            "finding_count": finding_count,
        },
        "provenance": {
            "graph_sha256": graph_sha256,
            "space_level": space_level,
            "release_sha256": release_sha256,
            "plugin": "audit.evidence-lineage@0.1.0",
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
