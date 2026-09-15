from __future__ import annotations

import os
from pathlib import Path

import psycopg2
import pytest

from packages.knowledge.source import register_read_only_source

DB = os.getenv("AUDIT_NETWORK_TEST_DATABASE_URL", "postgresql://audit_app:admin@localhost:5432/audit_network")


@pytest.fixture()
def database() -> None:
    try:
        with psycopg2.connect(DB):
            return
    except psycopg2.Error as exc:  # pragma: no cover - environment-dependent
        pytest.skip(f"native PostgreSQL unavailable: {exc}")


def test_source_registration_is_read_only_non_recursive_and_idempotent(tmp_path: Path, database: None) -> None:
    (tmp_path / "README.md").write_text("root manifest", encoding="utf-8")
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "README.md").write_text("must not inspect", encoding="utf-8")
    first = register_read_only_source(str(tmp_path), database_url=DB, name="External test")
    second = register_read_only_source(str(tmp_path), database_url=DB, name="External test updated")
    assert first.source_id == second.source_id
    assert first.readme_files == ("README.md",)
    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        cur.execute("SELECT id FROM iam.tenants WHERE slug='local-dev'")
        tenant_id = cur.fetchone()[0]
        cur.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),))
        cur.execute("SELECT name,read_only,file_rules,manifest FROM knowledge.ingest_sources WHERE id=%s", (first.source_id,))
        row = cur.fetchone()
        assert row[0] == "External test updated"
        assert row[1] is True
        assert row[2]["recursive_scan"] is False
        assert row[2]["explicit_readmes_only"] is True
        assert [item["path"] for item in row[3]["readmes"]] == ["README.md"]
