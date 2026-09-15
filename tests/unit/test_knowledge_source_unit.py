from __future__ import annotations

import sys
from pathlib import Path

import pytest

from packages.knowledge.source import _path_from_uri, _readme_manifest


def test_readme_manifest_reads_only_explicit_root_files(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("root", encoding="utf-8")
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "README.md").write_text("nested", encoding="utf-8")
    manifest = _readme_manifest(tmp_path, ("README.md",))
    assert [entry["path"] for entry in manifest["readmes"]] == ["README.md"]
    assert manifest["recursive_scan"] is False
    assert manifest["copied"] is False
    assert manifest["executed"] is False


def test_readme_manifest_rejects_path_escape(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="escapes"):
        _readme_manifest(tmp_path, ("..",))


@pytest.mark.skipif(
    sys.platform != "win32",
    reason="drive-letter semantics are Windows-only: on POSIX 'G:/数据' is a relative path",
)
def test_file_uri_round_trip_for_windows_drive() -> None:
    assert str(_path_from_uri("file:///G:/数据")) == "G:\\数据"
