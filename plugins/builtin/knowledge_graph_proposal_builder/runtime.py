"""Isolated implementation for the verified read-only graph-proposal-builder plugin.

The plugin turns an already-extracted ``graph-candidate-set`` artifact into a
deterministic ChangeSet **draft**: distinct entity/relation operations carry a
source location, an idempotent key, a target space and a duplicate/conflict
marker computed against an optional live-graph snapshot.

It never writes to the active graph and never touches domain tables: the draft
is only materialized as output, and the graph governance service stages it into
a shadow ChangeSet after a policy verdict and manual/rule approval.
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
DEFAULT_MAX_PROPOSALS = 1_000
KNOWLEDGE_NODE_TYPES = ("organization", "document", "person", "generic")
SPACE_LEVELS = ("L0", "L1", "L2", "L3", "L4")
RELATION_TYPES = ("related_party", "controls", "invests_in", "transacts_with", "cites", "part_of", "generic")


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


def _snapshot_index(graph: dict[str, Any]) -> dict[str, set[str]]:
    """Map snapshot node text -> the set of knowledge-typed node types it carries."""
    index: dict[str, set[str]] = {}
    nodes_by_type = graph.get("nodes_by_type")
    if not isinstance(nodes_by_type, dict):
        return index
    for raw_type, raw_nodes in nodes_by_type.items():
        if not isinstance(raw_nodes, list):
            continue
        node_type = str(raw_type).lower()
        if node_type not in KNOWLEDGE_NODE_TYPES:
            # Non-knowledge spaces (e.g. AIOps incident/alert nodes) do not
            # share the proposal target space, so they cannot conflict.
            continue
        for raw in raw_nodes:
            if not isinstance(raw, dict):
                continue
            text = raw.get("text")
            if isinstance(text, str) and text:
                index.setdefault(text.strip(), set()).add(node_type)
    return index


def handle(envelope: dict[str, Any]) -> dict[str, Any]:
    if envelope.get("protocol") != "audit-network-plugin-child-v1":
        raise InputRejected("unsupported child protocol")
    if envelope.get("plugin_id") != "knowledge.graph-proposal-builder":
        raise InputRejected("unexpected plugin identity")
    if envelope.get("capability") != "graph.proposal.build":
        raise InputRejected("unexpected capability")
    proposal_input = envelope.get("proposal")
    if not isinstance(proposal_input, dict):
        raise InputRejected("proposal input is missing")
    roots = _allowed_roots()
    candidates_path, candidates_sha256 = _read_verified(proposal_input.get("candidate_set"), roots, "candidate set")
    snapshot_path: Path | None = None
    snapshot_sha256: str | None = None
    if proposal_input.get("graph_snapshot") is not None:
        snapshot_path, snapshot_sha256 = _read_verified(proposal_input.get("graph_snapshot"), roots, "graph snapshot")
    space_level = proposal_input.get("space_level", "L1")
    if space_level not in SPACE_LEVELS:
        raise InputRejected("space_level is outside the fixed budget")
    max_proposals = proposal_input.get("max_proposals", DEFAULT_MAX_PROPOSALS)
    if not isinstance(max_proposals, int) or not (1 <= max_proposals <= MAX_CANDIDATES):
        raise InputRejected("max_proposals is outside the fixed budget")

    candidate_set = _parse_json(candidates_path, "candidate set")
    if not isinstance(candidate_set, dict):
        raise InputRejected("candidate set must be a JSON object")
    raw_entities = candidate_set.get("entity_candidates")
    raw_relations = candidate_set.get("relation_candidates")
    if not isinstance(raw_entities, list) or not isinstance(raw_relations, list):
        raise InputRejected("candidate set must carry entity and relation candidate lists")
    if len(candidate_set.get("entity_candidates", [])) + len(raw_relations) > MAX_CANDIDATES:
        raise InputRejected("candidate set exceeds the fixed event budget")
    snapshot_index: dict[str, set[str]] = {}
    if snapshot_path is not None:
        graph = _parse_json(snapshot_path, "graph snapshot")
        if not isinstance(graph, dict):
            raise InputRejected("graph snapshot must be a JSON object")
        snapshot_index = _snapshot_index(graph)

    # --- Entity proposals (deterministic, ordered by candidate_id) ----------
    entity_by_id: dict[str, dict[str, Any]] = {}
    by_name: dict[str, set[str]] = {}
    for raw in raw_entities:
        if not isinstance(raw, dict):
            raise InputRejected("each entity candidate must be an object")
        candidate_id = raw.get("candidate_id")
        name = raw.get("name")
        entity_type = raw.get("entity_type")
        if not isinstance(candidate_id, str) or not candidate_id:
            raise InputRejected("each entity candidate must carry a candidate_id")
        if not isinstance(name, str) or not name:
            raise InputRejected("each entity candidate must carry a name")
        if entity_type not in KNOWLEDGE_NODE_TYPES:
            raise InputRejected("entity_type is outside the fixed contract")
        if candidate_id in entity_by_id:
            continue
        by_name.setdefault(name.strip(), set()).add(entity_type)
        entity_by_id[candidate_id] = {
            "candidate_id": candidate_id,
            "name": name.strip(),
            "entity_type": entity_type,
            "source_ref": raw.get("source_ref") if isinstance(raw.get("source_ref"), str) else None,
            "confidence": raw.get("confidence") if isinstance(raw.get("confidence"), (int, float)) else 0.5,
        }

    conflicts: list[dict[str, Any]] = []
    conflicts_by_name: set[str] = set()
    proposed_entities: list[dict[str, Any]] = []
    used = max_proposals
    truncated = False
    for entity in sorted(entity_by_id.values(), key=lambda item: item["candidate_id"]):
        name = entity["name"]
        entity_type = entity["entity_type"]
        status: str = "proposed"
        reason: str | None = None
        if name in by_name and len(by_name[name]) > 1:
            status = "conflict_name"
            reason = "same name carries distinct entity types in the candidate set"
            if name not in conflicts_by_name:
                conflicts_by_name.add(name)
                conflicts.append(
                    {"name": name, "entity_types": sorted(by_name[name]), "reason": "same name carries distinct entity types in the candidate set"}
                )
        elif name in snapshot_index:
            snapshot_types = snapshot_index[name]
            if entity_type in snapshot_types:
                status = "duplicate"
                reason = "entity already present in the target space"
            elif summary_snapshot_conflict := [item for item in sorted(snapshot_types) if item in KNOWLEDGE_NODE_TYPES]:
                status = "conflict_name"
                reason = f"snapshot holds a different type for the same name: {','.join(summary_snapshot_conflict)}"
                if name not in conflicts_by_name:
                    conflicts_by_name.add(name)
                    conflicts.append(
                        {"name": name, "entity_types": sorted({entity_type, *summary_snapshot_conflict}), "reason": "snapshot holds a different type for the same name"}
                    )
            else:
                status = "duplicate"
                reason = "name already present in the target space"
        proposal_entity: dict[str, Any] = {
            "op": "add_entity",
            "candidate_id": entity["candidate_id"],
            "name": name,
            "entity_type": entity_type,
            "confidence": round(float(entity["confidence"]), 6),
            "target_space": space_level,
            "status": status,
        }
        if entity["source_ref"]:
            proposal_entity["source_ref"] = entity["source_ref"]
        if reason:
            proposal_entity["reason"] = reason
        if status == "proposed":
            used -= 1
            if used < 0:
                truncated = True
            else:
                proposed_entities.append(proposal_entity)
        else:
            proposed_entities.append(proposal_entity)

    # --- Relation proposals --------------------------------------------------
    relation_by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    blocked = 0
    for raw in raw_relations:
        if not isinstance(raw, dict):
            raise InputRejected("each relation candidate must be an object")
        candidate_id = raw.get("candidate_id")
        source_id = raw.get("source_id")
        target_id = raw.get("target_id")
        relation_type = raw.get("relation_type")
        span = raw.get("span")
        if not isinstance(candidate_id, str) or not candidate_id:
            raise InputRejected("each relation candidate must carry a candidate_id")
        if not isinstance(source_id, str) or not isinstance(target_id, str):
            raise InputRejected("relation candidate endpoints are invalid")
        if relation_type not in RELATION_TYPES:
            raise InputRejected("relation_type is outside the fixed contract")
        source_entity = entity_by_id.get(source_id)
        target_entity = entity_by_id.get(target_id)
        if source_entity is None or target_entity is None:
            blocked += 1
            continue
        source_status = next((item["status"] for item in proposed_entities if item["candidate_id"] == source_id), "skipped_unknown")
        target_status = next((item["status"] for item in proposed_entities if item["candidate_id"] == target_id), "skipped_unknown")
        if source_status != "proposed" or target_status != "proposed":
            blocked += 1
            continue
        idempotent_key = hashlib.sha256(f"{source_id}|{relation_type}|{target_id}".encode("utf-8")).hexdigest()[:48]
        raw_confidence = raw.get("confidence")
        relation_by_key.setdefault(
            (source_id, target_id, relation_type),
            {
                "op": "add_relation",
                "candidate_id": candidate_id,
                "source_id": source_id,
                "target_id": target_id,
                "relation_type": relation_type,
                "idempotent_key": idempotent_key,
                "span": span if isinstance(span, str) else "",
                "confidence": round(float(raw_confidence) if isinstance(raw_confidence, (int, float)) else 0.5, 6),
                "target_space": space_level,
                "status": "proposed",
            },
        )

    proposed_relations: list[dict[str, Any]] = []
    relation_duplicates = 0
    for key in sorted(relation_by_key):
        if used < 0:
            truncated = True
            break
        relation = relation_by_key[key]
        if relation["span"]:
            relation["span"] = relation["span"][:512]
        proposed_relations.append(relation)
        used -= 1
    relation_duplicates = len(raw_relations) - len(relation_by_key) - blocked

    duplicates = sum(1 for item in proposed_entities if item["status"] == "duplicate") + relation_duplicates
    return {
        "contract_id": "graph-proposal-draft",
        "contract_version": "1.0.0",
        "candidate_set_sha256": candidates_sha256,
        "space_level": space_level,
        "draft_id": hashlib.sha256(f"{candidates_sha256}|{space_level}".encode("utf-8")).hexdigest()[:16],
        **({"graph_snapshot_sha256": snapshot_sha256} if snapshot_sha256 else {}),
        "entity_proposals": proposed_entities,
        "relation_proposals": proposed_relations,
        "conflicts": conflicts,
        "summary": {
            "candidate_entities": len(raw_entities),
            "candidate_relations": len(raw_relations),
            "entities_proposed": sum(1 for item in proposed_entities if item["status"] == "proposed"),
            "relations_proposed": len(proposed_relations),
            "duplicates": duplicates,
            "conflicts": len(conflicts),
            "blocked_relations": blocked,
            "truncated": truncated,
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