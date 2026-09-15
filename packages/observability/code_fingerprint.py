"""Deterministic service code fingerprint (code_sha256).

The fingerprint covers every ``.py`` file under the given roots in sorted
relative-path order (excluding ``__pycache__`` and virtualenvs), so a build is
reproducible: same sources -> same fingerprint.  The value is attached to log
lines (via ``code_sha_var``) and to the code-map payload, giving the "which
version of the code produced this data/log" answer (L2).
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Iterable


def service_code_sha256(roots: Iterable[str | Path]) -> str:
    digest = hashlib.sha256()
    for root in roots:
        root_path = Path(root)
        if not root_path.is_dir():
            continue
        for py_file in sorted(root_path.rglob("*.py"), key=lambda p: p.relative_to(root_path).as_posix()):
            if "__pycache__" in py_file.parts or ".venv" in py_file.parts:
                continue
            rel = py_file.relative_to(root_path).as_posix()
            digest.update(rel.encode("utf-8"))
            digest.update(b"\0")
            digest.update(py_file.read_bytes())
            digest.update(b"\0")
    return digest.hexdigest()
