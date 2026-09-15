"""Isolated implementation for the verified read-only entity-relation-candidate plugin."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

MAX_INPUT_BYTES = 10 * 1024 * 1024
DEFAULT_MAX_ENTITIES = 200
DEFAULT_MAX_RELATIONS = 200
ORG_SUFFIXES = (
    "公司", "集团", "银行", "证券", "保险", "基金", "大学", "研究院",
    "事务所", "中心", "委员会", "管理局", "审计局", "财政局", "部",
)
DOC_PATTERN = re.compile(r"[《『「]([^》』」]{2,80})[》』」]")
ORG_PATTERN = re.compile(r"([^，。；、\s与和及或的,\.|]{1,20}(?:" + "|".join(ORG_SUFFIXES) + r"))")
EN_TOKEN_PATTERN = re.compile(r"(?<![A-Za-z0-9])([A-Z][A-Za-z0-9]{2,24}(?:[ _-][A-Z0-9][A-Za-z0-9]{1,24}){0,3})")
RELATION_TEMPLATES = [
    (r"关联交易", "related_party"),
    (r"控股|全资控股|控制", "controls"),
    (r"参股|投资|持股", "invests_in"),
    (r"采购|销售|交易|往来", "transacts_with"),
    (r"引用|基于|参考|依据", "cites"),
    (r"子公司|分公司|组成部分|属于", "part_of"),
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
        raise InputRejected("document URI is missing")
    parsed = urlparse(uri)
    if parsed.scheme != "file" or parsed.netloc not in {"", "localhost"}:
        raise InputRejected("document URI must be local")
    raw_path = unquote(parsed.path)
    if raw_path.startswith("/") and len(raw_path) >= 3 and raw_path[2] == ":":
        raw_path = raw_path[1:]
    try:
        resolved = Path(raw_path).resolve(strict=True)
    except OSError as exc:
        raise InputRejected("document file is unavailable") from exc
    if not resolved.is_file():
        raise InputRejected("document is not a regular file")
    try:
        next(root for root in roots if resolved.is_relative_to(root))
    except StopIteration as exc:
        raise InputRejected("document is outside declared read roots") from exc
    return resolved


def _break_text(text: str) -> str:
    """Replace relation verbs and colons with a separator so entity names stop at clause boundaries."""
    broken = text
    for pattern, _relation_type in RELATION_TEMPLATES:
        broken = re.sub(pattern, "|", broken)
    broken = re.sub(r"[：:]", "|", broken)
    return broken


def _extract_entities(text: str) -> list[dict[str, Any]]:
    entities: dict[str, dict[str, Any]] = {}
    broken = _break_text(text)
    for match in DOC_PATTERN.finditer(broken):
        name = match.group(1).strip()
        if not name:
            continue
        key = f"document::{name}"
        entities.setdefault(
            key,
            {
                "candidate_id": key,
                "entity_type": "document",
                "name": name,
                "span": match.group(0),
                "confidence": 0.9,
                "_start": match.start(),
            },
        )
    for match in ORG_PATTERN.finditer(broken):
        name = match.group(1).strip()
        if not name or len(name) < 2:
            continue
        key = f"organization::{name}"
        entities.setdefault(
            key,
            {
                "candidate_id": key,
                "entity_type": "organization",
                "name": name,
                "span": match.group(0),
                "confidence": 0.8,
                "_start": match.start(),
            },
        )
    for match in EN_TOKEN_PATTERN.finditer(broken):
        name = match.group(1).strip()
        key = f"organization::{name}"
        entities.setdefault(
            key,
            {
                "candidate_id": key,
                "entity_type": "organization",
                "name": name,
                "span": match.group(0),
                "confidence": 0.6,
                "_start": match.start(),
            },
        )
    return sorted(entities.values(), key=lambda item: item["_start"])  # type: ignore[return-value]


def _extract_relations(text: str, entities: list[dict[str, Any]], document_ref: str) -> list[dict[str, Any]]:
    relations: dict[tuple[str, str, str], dict[str, Any]] = {}
    for line in text.splitlines():
        line_entities = [
            entity
            for entity in entities
            if entity["_start"] is not None and line.find(entity["span"]) >= 0  # type: ignore[arg-type]
        ]
        if len(line_entities) < 2:
            continue
        for index, left in enumerate(line_entities):
            for right in line_entities[index + 1 :]:
                window_start = line.find(left["span"])
                window_end = line.find(right["span"], window_start + len(str(left["span"])))
                if window_start < 0 or window_end < 0:
                    continue
                window = line[window_start:window_end]
                for pattern, relation_type in RELATION_TEMPLATES:
                    if re.search(pattern, window):
                        key = (left["candidate_id"], right["candidate_id"], relation_type)
                        relations.setdefault(
                            key,
                            {
                                "candidate_id": f"{relation_type}:{left['candidate_id']}->{right['candidate_id']}",
                                "source_id": left["candidate_id"],
                                "target_id": right["candidate_id"],
                                "relation_type": relation_type,
                                "span": window.strip(),
                                "source_ref": f"{document_ref}:line:{len(line)}",
                                "confidence": 0.7,
                                "evidence": {"window": window.strip()},
                            },
                        )
                        break
    return sorted(relations.values(), key=lambda item: item["candidate_id"])  # type: ignore[return-value]


def handle(envelope: dict[str, Any]) -> dict[str, Any]:
    if envelope.get("protocol") != "audit-network-plugin-child-v1":
        raise InputRejected("unsupported child protocol")
    if envelope.get("plugin_id") != "knowledge.entity-relation-candidate":
        raise InputRejected("unexpected plugin identity")
    if envelope.get("capability") != "knowledge.extract.relations":
        raise InputRejected("unexpected capability")
    document = envelope.get("document")
    if not isinstance(document, dict):
        raise InputRejected("document reference is missing")
    artifact = document.get("artifact")
    if not isinstance(artifact, dict):
        raise InputRejected("document artifact reference is missing")
    path = _resolve_file(artifact.get("uri"), _allowed_roots())
    content = path.read_bytes()
    if len(content) > MAX_INPUT_BYTES:
        raise InputRejected("document exceeds local read budget")
    expected_size = artifact.get("size_bytes")
    if not isinstance(expected_size, int) or expected_size != len(content):
        raise InputRejected("document size does not match reference")
    expected_hash = artifact.get("sha256")
    actual_hash = hashlib.sha256(content).hexdigest()
    if not isinstance(expected_hash, str) or actual_hash.lower() != expected_hash.lower():
        raise InputRejected("document sha256 does not match reference")
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise InputRejected("document must be UTF-8 text") from exc
    language = document.get("language", "auto")
    if language not in {"zh", "en", "auto"}:
        raise InputRejected("language is outside the fixed budget")
    max_entities = int(document.get("max_entities", DEFAULT_MAX_ENTITIES))
    max_relations = int(document.get("max_relations", DEFAULT_MAX_RELATIONS))
    if not (1 <= max_entities <= 1000 and 1 <= max_relations <= 1000):
        raise InputRejected("entity or relation budget is outside the fixed limits")

    source_uri = artifact.get("uri", "")
    document_ref = f"document:{actual_hash}"
    entities = _extract_entities(text)
    truncated_entities = False
    if len(entities) > max_entities:
        entities = entities[:max_entities]
        truncated_entities = True
    relations = _extract_relations(text, entities, document_ref)
    truncated_relations = False
    if len(relations) > max_relations:
        relations = relations[:max_relations]
        truncated_relations = True
    for entity in entities:
        entity.pop("_start", None)
        entity.setdefault("source_ref", f"{document_ref}:span:{entity['span']}")

    return {
        "contract_id": "graph-candidate-set",
        "contract_version": "1.0.0",
        "document_sha256": actual_hash,
        "source_uri": str(source_uri),
        "language": language,
        "summary": {
            "entities": len(entities),
            "relations": len(relations),
            "truncated": truncated_entities or truncated_relations,
        },
        "entity_candidates": entities,
        "relation_candidates": relations,
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
