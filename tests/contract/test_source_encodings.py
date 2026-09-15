"""Source-file encodings: a BOM where it helps, never where it only hides.

Two opposite rules, both learned the hard way on this repository:

* a ``.py`` must **not** carry a UTF-8 BOM.  CPython happens to tolerate one, so
  nothing fails — which is exactly the problem: seven shipped modules had an
  invisible first byte, and the only way to find them was a byte-level scan.
* a ``.ps1`` that contains **any non-ASCII character must** carry a UTF-8 BOM.
  PowerShell 5.1 decodes a BOM-less script as the ANSI code page (GBK here), and
  a multi-byte sequence can then swallow the quote that ends a string:
  ``verify-plugin-protocol-data.ps1`` failed to parse *at all* until the BOM was
  added, and the error pointed at a line that looked perfectly fine.

The second rule is the dangerous one, because it breaks only on machines whose
code page differs from the author's — i.e. never on the machine that wrote it.
"""
from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

SCAN_ROOTS = ("scripts", "packages", "apps", "plugins", "tests", "migrations", ".github")
SKIP_DIRS = frozenset({"node_modules", "__pycache__", ".venv", "dist", "build"})

BOM = b"\xef\xbb\xbf"


def _files(*suffixes: str) -> list[Path]:
    found: list[Path] = []
    for root in SCAN_ROOTS:
        base = ROOT / root
        if not base.exists():
            continue
        for path in sorted(base.rglob("*")):
            if not path.is_file() or path.suffix not in suffixes:
                continue
            if SKIP_DIRS & set(path.parts):
                continue
            found.append(path)
    return found


def _has_bom(path: Path) -> bool:
    return path.read_bytes().startswith(BOM)


def _is_pure_ascii(path: Path) -> bool:
    """Decoded with ``utf-8-sig`` so the BOM itself is not counted as content."""
    return all(ord(char) < 128 for char in path.read_text(encoding="utf-8-sig"))


def test_the_scan_finds_files_to_check() -> None:
    assert len(_files(".py")) > 100, "the scanner is looking in the wrong place"
    assert _files(".ps1"), "the scanner found no PowerShell scripts"


def test_no_python_module_carries_a_bom() -> None:
    offenders = [str(p.relative_to(ROOT)) for p in _files(".py") if _has_bom(p)]
    assert not offenders, (
        "UTF-8 BOM on Python source — invisible, tolerated, and therefore never "
        "noticed; strip the leading 3 bytes:\n  " + "\n  ".join(offenders)
    )


def test_every_non_ascii_powershell_script_has_a_bom() -> None:
    """Without it PowerShell 5.1 reads the file as GBK and the quotes break."""
    offenders: list[str] = []
    for path in _files(".ps1", ".psm1"):
        if not _is_pure_ascii(path) and not _has_bom(path):
            offenders.append(str(path.relative_to(ROOT)))
    assert not offenders, (
        "non-ASCII PowerShell script without a UTF-8 BOM — it parses as ANSI/GBK "
        "and can fail to parse at all:\n  " + "\n  ".join(offenders)
    )


@pytest.mark.parametrize("suffix", (".py", ".ps1", ".psm1", ".sql"))
def test_source_files_are_readable_as_utf8(suffix: str) -> None:
    broken: list[str] = []
    for path in _files(suffix):
        try:
            path.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError as exc:  # pragma: no cover - failure path
            broken.append(f"{path.relative_to(ROOT)}: {exc}")
    assert not broken, "not valid UTF-8:\n  " + "\n  ".join(broken)
