from __future__ import annotations

import json
from urllib.error import URLError

import pytest

from packages.knowledge.retrieval import OllamaEmbedder, reciprocal_rank_fusion


class _Response:
    def __init__(self, payload: object) -> None:
        self.payload = payload

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode()


def test_ollama_embedder_requires_exact_contract_dimension() -> None:
    embedder = OllamaEmbedder(opener=lambda *_args, **_kwargs: _Response({"embeddings": [[0.5] * 1024]}))
    vectors = embedder.embed(["审计证据"])
    assert len(vectors) == 1
    assert len(vectors[0]) == 1024

    invalid = OllamaEmbedder(opener=lambda *_args, **_kwargs: _Response({"embeddings": [[0.5] * 3]}))
    with pytest.raises(ValueError, match="1024"):
        invalid.embed(["错误维度"])


def test_ollama_failure_is_an_explicit_unavailable_state() -> None:
    def unavailable(*_args: object, **_kwargs: object) -> _Response:
        raise URLError("offline")

    with pytest.raises(RuntimeError, match="Ollama embedding unavailable"):
        OllamaEmbedder(opener=unavailable).embed(["离线"])


def test_hybrid_fusion_rewards_results_present_in_both_rankings() -> None:
    keyword = [{"chunk_id": "a"}, {"chunk_id": "b"}]
    vector = [{"chunk_id": "b"}, {"chunk_id": "c"}]
    fused = reciprocal_rank_fusion(keyword, vector, limit=3)
    assert [item["chunk_id"] for item in fused] == ["b", "a", "c"]
    assert fused[0]["retrieval_mode"] == "hybrid"
