# -*- coding: utf-8 -*-
"""audit.foundation.fulltext-search: simulated fulltext search over documents

Read-only/simulated support-layer plugin. No network, no writes outside the
staging artifact. Fails closed on invalid input.
"""
from __future__ import annotations

from typing import Any

from plugins.builtin._child_common import (
    InputRejected,
    allowed_roots,
    check_identity,
    child_main,
    read_artifact_json,
    read_verified_artifact,
)

PLUGIN_ID = "audit.foundation.fulltext-search"
CAPABILITY = "audit.foundation.fulltext-search"

def handle(envelope: dict[str, Any]) -> dict[str, Any]:
    check_identity(envelope, PLUGIN_ID, CAPABILITY)
    port = envelope.get("search-query")
    if not isinstance(port, dict) or not isinstance(port.get("artifact"), dict):
        raise InputRejected("search-query reference is missing")
    artifact = port["artifact"]
    roots = allowed_roots()
    read_verified_artifact(artifact, roots)
    payload = read_artifact_json(artifact, roots)
    query = str(payload.get("query") or "")
    docs = payload.get("documents") if isinstance(payload, dict) else None
    if not query or not isinstance(docs, list):
        raise InputRejected("search-query requires query and documents")
    hits = [
        {"doc_id": str(d.get("doc_id")), "score": float(d.get("score") or 1.0)}
        for d in docs[:20]
        if isinstance(d, dict) and query.lower() in str(d.get("title", "")).lower()
    ]
    if not hits:
        raise InputRejected("no search hits")
    return {
        "contract_id": "document-content", "contract_version": "1.0.0",
        "artifact": {"title": f"全文检索: {query}", "summary": f"命中 {len(hits)} 篇", "hits": hits},
    }


if __name__ == "__main__":
    raise SystemExit(child_main(handle))
