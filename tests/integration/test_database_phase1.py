from __future__ import annotations

import os
from pathlib import Path

import psycopg2
import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

DATABASE_URL = os.getenv(
    "AUDIT_NETWORK_TEST_DATABASE_URL",
    "postgresql://audit_app:admin@localhost:5432/audit_network",
)
MIGRATOR_DATABASE_URL = os.getenv(
    "AUDIT_NETWORK_MIGRATOR_DATABASE_URL",
    "postgresql://audit_migrator:admin@localhost:5432/audit_network",
)


@pytest.fixture()
def connection():
    try:
        conn = psycopg2.connect(DATABASE_URL)
    except psycopg2.Error as exc:  # pragma: no cover - environment-dependent
        pytest.skip(f"native PostgreSQL unavailable: {exc}")
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture()
def migrator_connection():
    try:
        conn = psycopg2.connect(MIGRATOR_DATABASE_URL)
    except psycopg2.Error as exc:  # pragma: no cover - environment-dependent
        pytest.skip(f"native PostgreSQL migrator unavailable: {exc}")
    try:
        yield conn
    finally:
        conn.close()


def test_migration_head_and_table_inventory(connection, migrator_connection) -> None:
    with migrator_connection.cursor() as cur:
        cur.execute("SELECT version_num FROM public.alembic_version")
        version = cur.fetchone()[0]
        config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
        assert version in set(ScriptDirectory.from_config(config).get_heads())
        cur.execute(
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_schema IN ('iam','policy','catalog','control','event','artifact','semantic','graph','knowledge','belief','audit','quant','aiops','risk','ops')"
        )
        assert cur.fetchone()[0] >= 30


def test_phase1_rls_is_forced_on_tenant_tables(connection) -> None:
    with connection.cursor() as cur:
        cur.execute(
            "SELECT c.relrowsecurity, c.relforcerowsecurity FROM pg_class c "
            "JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname='graph' AND c.relname='nodes'"
        )
        assert cur.fetchone() == (True, True)


def test_knowledge_upload_idempotency_records_are_rls_forced(connection) -> None:
    with connection.cursor() as cur:
        cur.execute(
            "SELECT c.relrowsecurity, c.relforcerowsecurity FROM pg_class c "
            "JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname='knowledge' AND c.relname='upload_idempotency'"
        )
        assert cur.fetchone() == (True, True)


def test_rich_media_job_queue_is_rls_forced(connection) -> None:
    with connection.cursor() as cur:
        cur.execute(
            "SELECT c.relrowsecurity, c.relforcerowsecurity FROM pg_class c "
            "JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname='knowledge' AND c.relname='rich_media_jobs'"
        )
        assert cur.fetchone() == (True, True)


def test_phase1_outbox_function_is_callable(connection) -> None:
    with connection.cursor() as cur:
        cur.execute("SELECT event.enqueue_outbox(NULL, 'test', 'phase1.test', NULL, '{\"ok\":true}')")
        outbox_id = cur.fetchone()[0]
        connection.rollback()
        assert isinstance(outbox_id, int)
