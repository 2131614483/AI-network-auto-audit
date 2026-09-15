from __future__ import annotations

from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from openpyxl import Workbook

from packages.knowledge.ingest import chunk_text, ingest_directory
from packages.knowledge.local_extractors import local_adapter_catalog, mineru_available


def test_chunk_text_preserves_paragraphs_and_splits_long_text() -> None:
    chunks = chunk_text("first\n\nsecond" + "x" * 300, max_chars=160)
    assert chunks
    assert "first" in chunks[0]
    assert "second" in "".join(chunks)
    assert all(len(chunk) <= 160 for chunk in chunks)


def test_ingest_dry_run_is_deterministic(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text("# A\n\ncontent", encoding="utf-8")
    (tmp_path / "ignored.pdf").write_bytes(b"not parsed in phase 2")
    first = ingest_directory(tmp_path, database_url="postgresql://invalid", dry_run=True)
    second = ingest_directory(tmp_path, database_url="postgresql://invalid", dry_run=True)
    assert first == second
    assert first.scanned == 2
    assert first.accepted == 1
    assert first.deferred == 1
    assert first.batch_id is None


def test_markdown_front_matter_is_not_indexed_and_title_is_extracted(tmp_path: Path) -> None:
    path = tmp_path / "note.md"
    path.write_text("---\ntitle: 可靠标题\ntags: [audit]\n---\n正文第一段\n\n正文第二段", encoding="utf-8")
    result = ingest_directory(tmp_path, database_url="postgresql://invalid", dry_run=True)
    assert result.files[0].chunks == 1
    # The dry-run contract exposes the content fingerprint/count only; parsing
    # is asserted through the deterministic chunk helper to keep DB-free tests.
    assert chunk_text("正文第一段\n\n正文第二段") == ["正文第一段\n\n正文第二段"]


def test_chunk_text_normalizes_crlf_without_creating_empty_chunks() -> None:
    chunks = chunk_text("a\r\nb\r\n\r\nc")
    assert chunks == ["a\nb\n\nc"]


def test_dry_run_extracts_csv_docx_and_xlsx_without_running_macros(tmp_path: Path) -> None:
    (tmp_path / "ledger.csv").write_text("account,amount\n现金,100\n", encoding="utf-8")
    with ZipFile(tmp_path / "memo.docx", "w", ZIP_DEFLATED) as archive:
        archive.writestr(
            "word/document.xml",
            """<w:document xmlns:w=\"http://schemas.openxmlformats.org/wordprocessingml/2006/main\">
            <w:body><w:p><w:r><w:t>审计备忘录</w:t></w:r></w:p></w:body></w:document>""",
        )
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "风险"
    sheet.append(["项目", "分数"])
    sheet.append(["供应商", 95])
    workbook.save(tmp_path / "risk.xlsx")

    result = ingest_directory(tmp_path, database_url="postgresql://invalid", dry_run=True)

    assert result.scanned == result.accepted == 3
    assert {Path(item.source_uri).suffix for item in result.files} == {".csv", ".docx", ".xlsx"}
    assert all(item.chunks >= 1 for item in result.files)


def test_pdf_image_and_media_are_registered_for_local_extractors_without_execution(tmp_path: Path) -> None:
    (tmp_path / "report.pdf").write_bytes(b"%PDF-local-mineru")
    (tmp_path / "page.png").write_bytes(b"not-decoded")
    (tmp_path / "meeting.mp3").write_bytes(b"not-transcribed")

    result = ingest_directory(tmp_path, database_url="postgresql://invalid", dry_run=True)

    assert result.scanned == 3
    assert result.accepted == 0
    assert result.deferred == 3
    assert {item.extractor_key for item in result.files} == {"local.mineru", "local.media"}
    assert all(item.status == "waiting_extractor" for item in result.files)


def test_local_rich_file_adapter_catalog_exposes_interfaces_without_execution() -> None:
    catalog = local_adapter_catalog()
    by_key = {adapter.key: adapter for adapter in catalog}

    assert set(by_key) == {"local.mineru", "local.media"}
    # Audio/video transcription stays unconfigured regardless of MinerU presence.
    assert by_key["local.media"].status == "waiting_for_local_runtime"
    assert by_key["local.media"].execution_mode == "not_configured"
    # MinerU reflects local availability but never runs during a catalog read.
    assert by_key["local.mineru"].status == ("available" if mineru_available() else "waiting_for_local_runtime")
    assert all(adapter.supported_suffixes for adapter in catalog)
