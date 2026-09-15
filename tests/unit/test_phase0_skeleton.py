from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_phase0_required_files_exist() -> None:
    required = [
        "AGENTS.md",
        "README.md",
        "pyproject.toml",
        "package.json",
        "tsconfig.json",
        "docker-compose.yml",
        ".env.example",
        "docs/status.md",
    ]
    missing = [item for item in required if not (ROOT / item).is_file()]
    assert not missing, f"missing Phase 0 files: {missing}"


def test_package_json_is_valid() -> None:
    package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    assert package["name"] == "audit-network-web"
    assert "typecheck" in package["scripts"]


def test_compose_declares_pgvector_pg16() -> None:
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "pgvector/pgvector:pg16" in compose
    assert "54329:5432" in compose


def test_phase0_has_a_disposable_empty_database_bootstrap_check() -> None:
    script = (ROOT / "scripts" / "verify-empty-bootstrap.ps1").read_text(encoding="utf-8")
    assert "audit_network_verify_" in script
    assert "alembic" in script
    assert "DROP DATABASE IF EXISTS" in script
