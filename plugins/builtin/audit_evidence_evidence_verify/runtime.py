"""audit.evidence.evidence-verify: cross-check evidence lineage integrity.

Consumes an evidence-lineage graph (nodes + edges) and verifies: digest
well-formedness, node uniqueness, referential closure of edges and
duplicate digests.  Read-only; the verdict never mutates evidence.
"""
from __future__ import annotations

import re
from typing import Any

from plugins.builtin._child_common import (
    InputRejected,
    allowed_roots,
    check_identity,
    read_artifact_json,
    read_verified_artifact,
)

PLUGIN_ID = "audit.evidence.evidence-verify"
CAPABILITY = "audit.evidence.evidence-verify"
_HEX64 = re.compile(r"^[A-Fa-f0-9]{64}$")


def handle(envelope: dict[str, Any]) -> dict[str, Any]:
    check_identity(envelope, PLUGIN_ID, CAPABILITY)
    roots = allowed_roots()
    port = envelope.get("evidence-index")
    if not isinstance(port, dict) or not isinstance(port.get("artifact"), dict):
        raise InputRejected("evidence-index reference is missing")
    artifact = port["artifact"]
    read_verified_artifact(artifact, roots)
    lineage = read_artifact_json(artifact, roots)
    if not isinstance(lineage, dict):
        raise InputRejected("evidence-index payload must be an object")

    nodes = lineage.get("nodes")
    edges = lineage.get("edges")
    if not isinstance(nodes, list) or not all(isinstance(node, dict) for node in nodes):
        raise InputRejected("evidence-index nodes must be an array of objects")
    if not isinstance(edges, list) or not all(isinstance(edge, dict) for edge in edges):
        raise InputRejected("evidence-index edges must be an array of objects")

    checks: list[dict[str, str]] = []
    violations: list[dict[str, str]] = []

    # evidence_hash: every node carries a well-formed digest
    missing_hashes: list[str] = []
    for node in nodes:
        digest = node.get("sha256")
        if not isinstance(digest, str) or not _HEX64.match(digest):
            missing_hashes.append(str(node.get("node_id") or node.get("id") or "?"))
    if missing_hashes:
        checks.append({"check_id": "evidence_hash", "status": "fail", "message": f"{len(missing_hashes)} 个证据节点缺少有效摘要: {missing_hashes[:10]}"})
        violations.append({"check_id": "evidence_hash", "message": f"缺少有效摘要: {missing_hashes[:10]}"})
    else:
        checks.append({"check_id": "evidence_hash", "status": "pass", "message": f"{len(nodes)} 个证据节点摘要有效"})

    # uniqueness: node ids unique
    ids = [str(node.get("node_id") or node.get("id") or "?") for node in nodes]
    dup_ids = sorted({i for i in ids if ids.count(i) > 1})
    if dup_ids:
        checks.append({"check_id": "uniqueness", "status": "fail", "message": f"节点 id 重复: {dup_ids[:10]}"})
        violations.append({"check_id": "uniqueness", "message": f"节点 id 重复: {dup_ids[:10]}"})
    else:
        checks.append({"check_id": "uniqueness", "status": "pass", "message": f"{len(ids)} 个节点 id 唯一"})

    # referential closure: every edge endpoint exists
    id_set = set(ids)
    dangling: list[str] = []
    for edge in edges:
        for endpoint in (edge.get("source"), edge.get("target")):
            if endpoint is not None and str(endpoint) not in id_set:
                dangling.append(str(endpoint))
    closed = len(edges) - len(dangling)
    if dangling:
        checks.append({"check_id": "referential_closure", "status": "fail", "message": f"{len(dangling)} 条边端点悬空: {sorted(set(dangling))[:10]}"})
        violations.append({"check_id": "referential_closure", "message": f"悬空端点: {sorted(set(dangling))[:10]}"})
    else:
        checks.append({"check_id": "referential_closure", "status": "pass", "message": f"{len(edges)} 条边引用闭合"})

    # duplicate_digest: same digest on different nodes
    by_digest: dict[str, list[str]] = {}
    for node in nodes:
        digest = str(node.get("sha256") or "")
        if digest:
            by_digest.setdefault(digest, []).append(str(node.get("node_id") or node.get("id") or "?"))
    dup_digests = {digest: names for digest, names in by_digest.items() if len(names) > 1}
    if dup_digests:
        first = next(iter(dup_digests.items()))
        checks.append({"check_id": "duplicate_digest", "status": "fail", "message": f"{len(dup_digests)} 组证据摘要重复，如 {first[1][:3]}"})
        violations.append({"check_id": "duplicate_digest", "message": f"证据摘要重复: {first[1][:3]}"})
    else:
        checks.append({"check_id": "duplicate_digest", "status": "pass", "message": "证据摘要全部唯一"})

    valid = all(check["status"] == "pass" for check in checks)
    return {
        "contract_id": "evidence-verdict",
        "contract_version": "1.0.0",
        "lineage_id": lineage.get("lineage_id"),
        "summary": {
            "valid": valid,
            "checked_evidence": len(nodes),
            "checked_edges": len(edges),
            "closed_edges": closed,
            "duplicate_digests": len(dup_digests),
            "missing_hashes": len(missing_hashes),
        },
        "checks": checks,
        "violations": violations,
    }


if __name__ == "__main__":
    from plugins.builtin._child_common import child_main

    raise SystemExit(child_main(handle))
