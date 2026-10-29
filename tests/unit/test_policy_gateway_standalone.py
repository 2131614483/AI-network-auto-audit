"""Installer and CLI policy persistence must not depend on API import effects."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_gateway_uuid_parameters_work_in_an_isolated_python_process():
    root = Path(__file__).resolve().parents[2]
    code = (
        f"import sys; sys.path.insert(0, {str(root)!r}); "
        "import packages.policy.gateway; from uuid import uuid4; "
        "from psycopg2.extensions import adapt; assert adapt(uuid4()).getquoted()"
    )
    result = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
