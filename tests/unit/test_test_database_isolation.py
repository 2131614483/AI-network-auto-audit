from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_integration_tests_default_to_a_dedicated_native_database() -> None:
    conftest = (ROOT / "tests" / "conftest.py").read_text(encoding="utf-8")

    assert "audit_network_test" in conftest
    assert "AUDIT_NETWORK_TEST_DATABASE_URL" in conftest
    assert "AUDIT_NETWORK_MIGRATOR_DATABASE_URL" in conftest


def test_test_database_initialization_is_explicit_and_non_destructive() -> None:
    script = (ROOT / "scripts" / "init-test-postgres.ps1").read_text(encoding="utf-8")

    assert "audit_network_test" in script
    assert "CREATE DATABASE" in script
    assert "DROP DATABASE" not in script
    assert "existingOutput" in script
    assert "alembic" in script
