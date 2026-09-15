"""audit.foundation.nlp-process: deterministic text processing.

Modes: summary (sentence truncation + keyword scan), match (keyword
coverage), compose (template fill).  Stdlib-only; read-only projection of
the input artifact.  No external NLP model is invoked.
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

PLUGIN_ID = "audit.foundation.nlp-process"
CAPABILITY = "audit.foundation.nlp-process"
SENTENCE_SPLIT = re.compile(r"[。！？!?；;\n]+")
DEFAULT_SUMMARY_SENTENCES = 2


def _split_sentences(text: str) -> list[str]:
    return [part.strip() for part in SENTENCE_SPLIT.split(text) if part.strip()]


def _scan_keywords(text: str, keywords: list[str]) -> dict[str, int]:
    lower = text.lower()
    counts: dict[str, int] = {}
    for keyword in keywords:
        counts[keyword] = lower.count(keyword.lower())
    return counts


def handle(envelope: dict[str, Any]) -> dict[str, Any]:
    check_identity(envelope, PLUGIN_ID, CAPABILITY)
    roots = allowed_roots()
    port = envelope.get("nlp-input")
    if not isinstance(port, dict) or not isinstance(port.get("artifact"), dict):
        raise InputRejected("nlp-input reference is missing")
    artifact = port["artifact"]
    read_verified_artifact(artifact, roots)
    payload = read_artifact_json(artifact, roots)
    if not isinstance(payload, dict):
        raise InputRejected("nlp-input payload must be an object")

    mode = str(payload.get("mode") or "summary")
    text = payload.get("text")
    if not isinstance(text, str) or not text.strip():
        raise InputRejected("nlp-input requires a non-empty text")
    keywords = payload.get("keywords")
    if not isinstance(keywords, list):
        keywords = []
    keywords = [str(k) for k in keywords]

    sentences = _split_sentences(text)
    scan = _scan_keywords(text, keywords)
    matched = [k for k, count in scan.items() if count > 0]

    if mode == "summary":
        limit = max(1, int(payload.get("summary_sentences") or DEFAULT_SUMMARY_SENTENCES))
        summary = "。".join(sentences[:limit]) + ("。" if sentences[:limit] else "")
        output_text = summary
    elif mode == "match":
        output_text = "；".join(f"{k}×{scan[k]}" for k in keywords if scan[k] > 0) or "无关键词命中"
    elif mode == "compose":
        template = payload.get("template")
        if not isinstance(template, str):
            raise InputRejected("compose mode requires a template")
        summary = "。".join(sentences[:DEFAULT_SUMMARY_SENTENCES]) + ("。" if sentences[:DEFAULT_SUMMARY_SENTENCES] else "")
        output_text = (
            template.replace("{summary}", summary)
            .replace("{keywords}", "、".join(matched) or "无")
            .replace("{sentence_count}", str(len(sentences)))
        )
    else:
        raise InputRejected(f"不支持的 NLP 模式: {mode}")

    return {
        "contract_id": "nlp-output",
        "contract_version": "1.0.0",
        "artifact": {"name": str(artifact.get("uri", "")), "uri": str(artifact.get("uri", ""))},
        "language": "zh",
        "mode": mode,
        "text": output_text,
        "summary": summary if mode in ("summary", "compose") else output_text,
        "keywords": matched,
        "sentence_count": len(sentences),
        "keyword_counts": scan if matched else {},
    }


if __name__ == "__main__":
    from plugins.builtin._child_common import child_main

    raise SystemExit(child_main(handle))
