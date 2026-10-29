"""Runtime path resolution: read-only resources versus writable data.

In a packaged install the application code lives under a read-only resource
directory (for example ``Program Files``), while logs, spool files, evidence
bundles and generated artifacts must live in a location the current user is
allowed to write.  The desktop launcher exports ``AUDIT_NETWORK_DATA_DIR``
before it starts the API and the worker.

When the variable is unset - development, CI, the existing PowerShell
scripts - the historical ``<project>/.data`` layout is preserved exactly, so
this indirection changes no existing behaviour.
"""

from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

DATA_DIR_ENV = "AUDIT_NETWORK_DATA_DIR"


def project_root() -> Path:
    """Return the directory that holds the application packages."""
    return PROJECT_ROOT


def data_root() -> Path:
    """Return the writable data directory for this installation."""
    override = os.getenv(DATA_DIR_ENV, "").strip()
    if override:
        return Path(override)
    return PROJECT_ROOT / ".data"
