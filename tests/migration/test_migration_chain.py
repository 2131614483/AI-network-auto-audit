"""R4: migration-chain integrity (tests/migration was an empty directory).

Pins the migration numbering (non-decreasing 4-digit prefixes, with the known
0018 triple-branch historical exception) and the live head, so a broken or
missing migration is caught by the suite instead of by a 24x7 run.
"""

from __future__ import annotations

import re
from pathlib import Path

import psycopg2
from alembic.config import Config
from alembic.script import ScriptDirectory

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations" / "versions"
TEST_DB = "postgresql://audit_app:admin@localhost:5432/audit_network_test"

_PREFIX_RE = re.compile(r"^(\d{4})_[a-z0-9_]+\.py$")


def _alembic_head() -> str:
    """从 Alembic 脚本目录推导当前 head。

    期望头曾以常量形式写死（0051 → 0054 → 0062 …），每次新增迁移都会让本文件和
    ``/api/v1/health/ready``、运维健康检查一起误报。这里改用与 ``alembic upgrade
    head`` 同源的权威结果，豁免值的维护成本为零。
    """

    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    return str(ScriptDirectory.from_config(config).get_current_head())


EXPECTED_HEAD = _alembic_head()


def test_migration_files_numbered_in_order() -> None:
    prefixes: list[int] = []
    for path in sorted(MIGRATIONS_DIR.glob("*.py")):
        match = _PREFIX_RE.match(path.name)
        assert match, f"migration file does not match NNNN_slug.py: {path.name}"
        prefixes.append(int(match.group(1)))
    assert len(prefixes) >= 50
    assert prefixes == sorted(prefixes), "migration prefixes must be non-decreasing"
    # Historical Q6 note: 0018 was split into three branches that were merged
    # by 0019; duplicate prefixes are therefore allowed but only for 0018.
    duplicates = [p for p in set(prefixes) if prefixes.count(p) > 1]
    assert all(p == 18 for p in duplicates), f"unexpected duplicate migration prefixes: {duplicates}"


def test_test_database_migrated_to_expected_head() -> None:
    with psycopg2.connect(TEST_DB) as connection, connection.cursor() as cur:
        cur.execute("SELECT version_num FROM public.alembic_version")
        row = cur.fetchone()
        assert row is not None
        assert str(row[0]) == EXPECTED_HEAD


def test_migration_files_declare_revision_chain() -> None:
    """Every migration declares revision + down_revision and the chain lands on head."""
    revisions: dict[str, str | None] = {}
    for path in MIGRATIONS_DIR.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        rev = re.search(r'revision\s*=\s*["\']([^"\']+)["\']', text)
        down = re.search(r'down_revision\s*=\s*["\']([^"\']+)["\']', text)
        assert rev, f"{path.name}: missing revision"
        revisions[rev.group(1)] = down.group(1) if down else None
    assert EXPECTED_HEAD in revisions, f"expected head {EXPECTED_HEAD} not declared"
    # Walk the chain backwards from head to the first migration.
    current: str | None = EXPECTED_HEAD
    hops = 0
    while current is not None:
        assert current in revisions, f"broken chain at {current}"
        current = revisions[current]
        hops += 1
        assert hops < 200, "chain does not terminate"
