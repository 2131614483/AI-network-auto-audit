"""Machine-local absolute paths must not re-enter shipped scripts or code.

The repository already paid for this once: the launcher and maintenance
scripts named ``C:\\Users\\<author>\\...``, ``C:\\ProgramData\\Anaconda3`` and a
version-pinned ``C:\\Program Files\\PostgreSQL\\16\\bin``, so on any other
machine they failed in ways that looked like the project was broken.  All of
that now resolves through ``scripts/env-common.ps1`` (:func:`Resolve-ProjectPython`
/ :func:`Resolve-PgTool`); this test is the gate that keeps it resolved.

Scope, deliberately:

* ``scripts/``, ``.github/``, ``packages/``, ``apps/``, ``plugins/``,
  ``desktop/src/`` — things that ship and run;
* ``docs/`` is **excluded**: it holds run archives whose ``D:/pythonpro/...``
  paths are evidence of where a run happened, not configuration;
* ``*.test.ts`` / ``*.test.tsx`` are **excluded**: a fixture may legitimately
  embed a path literal as test data;
* ``plugins/builtin/*/contract/`` is **excluded**: those are the plugin
  contract fixtures, sample payloads whose ``file:///G:/...`` URIs are input
  data, not configuration — the same category as the ``*.test.ts`` fixtures.

A *generic* install root such as ``C:\\PostgreSQL`` is a legitimate search probe
(``env-common.ps1`` globs it) and is deliberately not matched — only
machine-specific markers are.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

SCAN_ROOTS = ("scripts", ".github", "packages", "apps", "plugins", "desktop/src")
SUFFIXES = frozenset(
    {".py", ".ps1", ".psm1", ".sql", ".yml", ".yaml", ".ts", ".tsx", ".js",
     ".json", ".bat", ".cmd", ".toml", ".ini"}
)
SKIP_NAMES = frozenset({"package-lock.json", "uv.lock"})

#: Machine-specific markers.  Kept narrow on purpose: matching any drive-letter
#: path would reject legitimate probes and teach people to disable the gate.
PATTERNS = (
    ("a user profile path", re.compile(r"[A-Za-z]:\\Users\\")),
    ("a ProgramData path", re.compile(r"[A-Za-z]:\\ProgramData\\")),
    ("an Anaconda interpreter", re.compile(r"Anaconda", re.IGNORECASE)),
    ("this repository's checkout path", re.compile(r"[A-Za-z]:[\\/]pythonpro[\\/]", re.IGNORECASE)),
    ("a specific data drive", re.compile(r"[A-Za-z]:[\\/]数据")),
)

_COMMENT_PREFIXES = ("#", "//", "--", "/*", "<!--")


def _code_lines(path: Path) -> list[tuple[int, str]]:
    """Non-comment lines, so a historical note ("this used to be pinned to …")
    does not trip the gate.  PowerShell block comments are stripped first —
    they are the reason a naive line-prefix check is not enough here."""
    text = path.read_text(encoding="utf-8", errors="replace")
    if path.suffix in (".ps1", ".psm1"):
        text = re.sub(r"<#.*?#>", "", text, flags=re.S)
    lines: list[tuple[int, str]] = []
    for number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith(_COMMENT_PREFIXES):
            continue
        lines.append((number, line))
    return lines


def _is_fixture(path: Path) -> bool:
    """Plugin contract fixtures are sample payloads, not configuration."""
    parts = path.parts
    return "contract" in parts and "builtin" in parts


def _is_demo_data_builder(path: Path) -> bool:
    """``desktop/src/public/showcase`` holds one-off builders for static demo payloads.

    ``build_data.py`` / ``_survey*.py`` / ``_sample.py`` are executed by hand to
    regenerate ``*.data.js`` from a local dataset drive; the browser only ever
    loads their *output*.  Those ``E:\\数据\\...`` literals are evidence of where
    a demo dataset came from, not configuration the app resolves at runtime —
    the same category as the run archives already excluded under ``docs/``.
    (``pyproject.toml`` ships the same exemption for ruff.)
    """

    return "showcase" in path.parts and "public" in path.parts


def _scanned_files() -> list[Path]:
    files: list[Path] = []
    for root in SCAN_ROOTS:
        base = ROOT / root
        if not base.exists():
            continue
        for path in sorted(base.rglob("*")):
            if not path.is_file() or path.suffix not in SUFFIXES or path.name in SKIP_NAMES:
                continue
            if path.name.endswith((".test.ts", ".test.tsx")) or _is_fixture(path) or _is_demo_data_builder(path):
                continue
            files.append(path)
    return files


def test_the_scan_finds_files_to_check() -> None:
    assert len(_scanned_files()) > 50, "the scanner is looking in the wrong place"


def test_no_machine_local_paths_in_shipped_code() -> None:
    offenders: list[str] = []
    for path in _scanned_files():
        for number, line in _code_lines(path):
            for description, pattern in PATTERNS:
                if pattern.search(line):
                    offenders.append(
                        f"{path.relative_to(ROOT)}:{number} names {description}: {line.strip()[:120]}"
                    )
    assert not offenders, (
        "machine-local absolute path(s) in shipped code — resolve them instead "
        "(scripts/env-common.ps1 has Resolve-ProjectPython / Resolve-PgTool):\n  "
        + "\n  ".join(offenders)
    )


@pytest.mark.parametrize(
    "line",
    [
        '   ".\\scripts\\verify-plugin-protocol-data.ps1 -DataRoot D:\\samples")',   # relative sample arg
        '  $roots += "C:\\PostgreSQL"',                                              # generic probe root
    ],
)
def test_the_gate_does_not_fire_on_legitimate_probes(line: str) -> None:
    """Narrowness is a feature: a gate that fires on install-root probes gets
    disabled, and a disabled gate protects nothing."""
    assert not any(pattern.search(line) for _, pattern in PATTERNS)
