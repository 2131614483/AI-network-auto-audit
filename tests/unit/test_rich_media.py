from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from packages.knowledge.rich_media import (
    MineruAdapter,
    RichMediaParseError,
    _parse_content_list,
    _resolve_input,
    parse_mineru_output,
    validate_parse_result,
)


def _write_fixture(root: Path) -> Path:
    markdown = root / "demo.md"
    markdown.write_text("# Title\n\nBody text\n\n![](images/x.jpg)\n", encoding="utf-8")
    images = root / "images"
    images.mkdir()
    (images / "x.jpg").write_bytes(b"fake-image")
    content_list = [
        {"type": "text", "text": "Title", "text_level": 1, "bbox": [10, 20, 30, 40], "page_idx": 0},
        {"type": "text", "text": "Body text", "bbox": [10, 50, 30, 60], "page_idx": 0},
        {"type": "image", "img_path": "images/x.jpg", "bbox": [10, 70, 30, 80], "page_idx": 1},
    ]
    (root / "demo_content_list.json").write_text(json.dumps(content_list), encoding="utf-8")
    return root


def test_parse_mineru_output_builds_a_schema_valid_document(tmp_path: Path) -> None:
    root = _write_fixture(tmp_path)
    document = parse_mineru_output(
        root, input_sha256="a" * 64, method="txt", adapter_version="2.0.0", duration_ms=1234
    )
    assert document.adapter_key == "local.mineru"
    assert document.method == "txt"
    assert document.images == ("x.jpg",)
    assert len(document.chunks) == 3
    validate_parse_result(document.as_payload())  # must not raise
    text_chunks = [chunk for chunk in document.chunks if chunk.type == "text"]
    assert any(chunk.text == "Body text" and chunk.page_idx == 0 for chunk in text_chunks)
    title = next(chunk for chunk in document.chunks if chunk.text == "Title")
    assert title.text_level == 1
    assert title.bbox == (10.0, 20.0, 30.0, 40.0)


def test_parse_content_list_skips_blocks_without_page_index(tmp_path: Path) -> None:
    path = tmp_path / "content_list.json"
    path.write_text(
        json.dumps(
            [
                {"type": "text", "text": "kept", "page_idx": 2},
                {"type": "text", "text": "no-page"},
                {"type": "text", "text": "negative", "page_idx": -1},
                "not-an-object",
            ]
        ),
        encoding="utf-8",
    )
    chunks = _parse_content_list(path)
    assert [chunk.text for chunk in chunks] == ["kept"]


def test_parse_mineru_output_missing_markdown_raises(tmp_path: Path) -> None:
    (tmp_path / "not-markdown.txt").write_text("no md here", encoding="utf-8")
    with pytest.raises(RichMediaParseError):
        parse_mineru_output(
            tmp_path, input_sha256="a" * 64, method="txt", adapter_version="2.0.0", duration_ms=1
        )


def test_validate_parse_result_rejects_bad_sha256() -> None:
    payload = {
        "adapter_key": "local.mineru",
        "adapter_version": "2.0.0",
        "input_sha256": "not-a-sha",
        "method": "txt",
        "markdown": "",
        "chunks": [],
        "images": [],
        "duration_ms": 0,
    }
    with pytest.raises(RichMediaParseError):
        validate_parse_result(payload)


def test_resolve_input_accepts_a_valid_pdf(tmp_path: Path) -> None:
    source = tmp_path / "a.pdf"
    content = b"%PDF-1.4 test"
    source.write_bytes(content)
    resolved = _resolve_input(source.as_uri(), hashlib.sha256(content).hexdigest(), len(content), (tmp_path.resolve(),))
    assert resolved == source.resolve()


def test_resolve_input_rejects_sha256_mismatch(tmp_path: Path) -> None:
    source = tmp_path / "a.pdf"
    content = b"%PDF-1.4 test"
    source.write_bytes(content)
    with pytest.raises(RichMediaParseError):
        _resolve_input(source.as_uri(), "b" * 64, len(content), (tmp_path.resolve(),))


def test_resolve_input_rejects_outside_declared_roots(tmp_path: Path) -> None:
    source = tmp_path / "a.pdf"
    content = b"%PDF-1.4 test"
    source.write_bytes(content)
    nested = tmp_path / "nested"
    nested.mkdir()
    with pytest.raises(RichMediaParseError):
        _resolve_input(source.as_uri(), hashlib.sha256(content).hexdigest(), len(content), (nested,))


def test_mineru_nonzero_exit_is_a_controlled_parse_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "failed.pdf"
    content = b"%PDF-1.4 controlled failure"
    source.write_bytes(content)
    monkeypatch.setattr(
        "packages.knowledge.rich_media.subprocess.run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=2, stdout="bad", stderr="bad"),
    )
    adapter = MineruAdapter(
        executable="controlled-mineru.exe",
        output_root=tmp_path / "output",
        allowed_roots=(tmp_path,),
        method="txt",
    )
    with pytest.raises(RichMediaParseError, match="exited unsuccessfully"):
        adapter.extract(
            uri=source.as_uri(), sha256=hashlib.sha256(content).hexdigest(), size_bytes=len(content)
        )
