from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from packages.knowledge.rich_media import MineruAdapter, validate_parse_result

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MINERU_EXE = os.getenv("MINERU_EXECUTABLE", "")


def _minimal_pdf() -> bytes:
    """Build a tiny valid single-page PDF with one extractable text line."""
    content = b"BT /F1 18 Tf 72 720 Td (AuditNetworkPhase3) Tj ET"
    objects = [
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n",
        b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n",
        b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>\nendobj\n",
        b"4 0 obj\n<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream\nendobj\n",
        b"5 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n",
    ]
    body = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for obj in objects:
        offsets.append(len(body))
        body += obj
    xref_offset = len(body)
    xref = b"xref\n0 6\n0000000000 65535 f \n"
    for offset in offsets:
        xref += f"{offset:010d} 00000 n \n".encode("ascii")
    trailer = (
        b"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n"
        + str(xref_offset).encode("ascii")
        + b"\n%%EOF\n"
    )
    return bytes(body) + xref + trailer


def _require_mineru() -> None:
    if not os.getenv("AUDIT_NETWORK_RUN_MINERU_TESTS"):
        pytest.skip("set AUDIT_NETWORK_RUN_MINERU_TESTS=1 to run real MinerU integration")
    # is_file(), not exists(): these paths may be empty strings, and Path("")
    # is the current directory, which exists on every machine.
    if not MINERU_EXE or not Path(MINERU_EXE).is_file():
        pytest.skip(f"MinerU executable not found: {MINERU_EXE or '<MINERU_EXECUTABLE unset>'}")


def test_mineru_extracts_a_real_pdf(tmp_path: Path) -> None:
    _require_mineru()
    output_root = PROJECT_ROOT / ".data" / "mineru-test"
    output_root.mkdir(parents=True, exist_ok=True)
    adapter = MineruAdapter(output_root=output_root, allowed_roots=(tmp_path.resolve(),), method="txt")
    source = tmp_path / "probe.pdf"
    content = _minimal_pdf()
    source.write_bytes(content)

    document = adapter.extract(
        uri=source.as_uri(),
        sha256=hashlib.sha256(content).hexdigest(),
        size_bytes=len(content),
    )

    validate_parse_result(document.as_payload())
    assert document.markdown.strip()
    assert any("AuditNetworkPhase3" in chunk.text for chunk in document.chunks)
    assert all(chunk.page_idx == 0 for chunk in document.chunks)
