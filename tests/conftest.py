"""Test-process defaults that prevent integration fixtures polluting the work DB."""

from __future__ import annotations

import os
from typing import Iterator

import pytest

TEST_DATABASE_URL = "postgresql://audit_app:admin@localhost:5432/audit_network_test"
TEST_MIGRATOR_DATABASE_URL = "postgresql://audit_migrator:admin@localhost:5432/audit_network_test"

# Individual CI jobs can still provide their own isolated database, but an
# unconfigured local ``pytest`` invocation must never silently target the
# interactive ``audit_network`` database.
os.environ.setdefault("AUDIT_NETWORK_TEST_DATABASE_URL", TEST_DATABASE_URL)
os.environ.setdefault("AUDIT_NETWORK_MIGRATOR_DATABASE_URL", TEST_MIGRATOR_DATABASE_URL)

# Configuration resolution reads the project .env as a fallback so every entry
# point shares one channel.  A test must never observe the developer's own
# settings, so the suite opts out entirely — including the API's explicit load.
os.environ["AUDIT_NETWORK_SKIP_DOTENV"] = "1"

_LOCAL_DEV_SLUG = "local-dev"

#: Policy sets the project itself provisions — by migration or by its own seed
#: step (``packages.demo.seed._ensure_demo_policy``).  These are the only grants
#: a test may rely on being present without creating them itself; every other
#: ``active`` row is a fixture some test left behind.
SEEDED_POLICY_SETS = frozenset(
    {
        "demo-execution-allow",
        "local-graph-routing-read",
        "local-graph-visualization",
        "local-knowledge-workbench",
        "local-operations-dashboard",
        "local-plugin-topology-graph-plan",
        "local-plugin-topology-isolated",
        "local-plugin-topology-read",
        "local-plugin-topology-remediate",
        "local-plugin-topology-verify",
    }
)


def _active_policy_sets() -> set[tuple[str, str]] | None:
    """Currently ``active`` policy sets as ``(name, version)`` pairs.

    Returns ``None`` when the test database is unreachable or the schema is not
    ready, so a purely offline unit test is never blocked by this guard.
    """
    url = os.environ.get("AUDIT_NETWORK_TEST_DATABASE_URL") or TEST_DATABASE_URL
    try:
        import psycopg2

        with psycopg2.connect(url) as connection, connection.cursor() as cur:
            cur.execute(
                "SELECT set_config('app.tenant_id', "
                "(SELECT id::text FROM iam.tenants WHERE slug=%s), false)",
                (_LOCAL_DEV_SLUG,),
            )
            cur.execute("SELECT name, version FROM policy.policy_sets WHERE status='active'")
            return {(str(name), str(version)) for name, version in cur.fetchall()}
    except Exception:  # pragma: no cover - depends on local database availability
        return None


def _set_status(keys: set[tuple[str, str]], status: str) -> None:
    """Set the named ``(name, version)`` policy sets to ``status``."""
    if not keys:
        return
    url = os.environ.get("AUDIT_NETWORK_TEST_DATABASE_URL") or TEST_DATABASE_URL
    import psycopg2

    # ``(name, version) = ANY(%s)`` does not adapt a list of Python tuples, and
    # swallowing that error made this guard a silent no-op; an explicit OR of
    # parameterised pairs is unambiguous.  Only called when the database was
    # reachable a moment ago, so a failure here should surface, not vanish.
    clauses = " OR ".join(["(name=%s AND version=%s)"] * len(keys))
    params = [item for pair in sorted(keys) for item in pair]
    with psycopg2.connect(url) as connection, connection.cursor() as cur:
        cur.execute(
            "SELECT set_config('app.tenant_id', "
            "(SELECT id::text FROM iam.tenants WHERE slug=%s), false)",
            (_LOCAL_DEV_SLUG,),
        )
        cur.execute(
            f"UPDATE policy.policy_sets SET status=%s "
            f"WHERE status<>%s AND ({clauses})",
            [status, status, *params],
        )


def _deactivate(keys: set[tuple[str, str]]) -> None:
    _set_status(keys, "inactive")


def _all_policy_set_keys() -> set[tuple[str, str]] | None:
    """Every policy set row for local-dev, regardless of status."""
    url = os.environ.get("AUDIT_NETWORK_TEST_DATABASE_URL") or TEST_DATABASE_URL
    try:
        import psycopg2

        with psycopg2.connect(url) as connection, connection.cursor() as cur:
            cur.execute(
                "SELECT set_config('app.tenant_id', "
                "(SELECT id::text FROM iam.tenants WHERE slug=%s), false)",
                (_LOCAL_DEV_SLUG,),
            )
            cur.execute("SELECT name, version FROM policy.policy_sets")
            return {(str(name), str(version)) for name, version in cur.fetchall()}
    except Exception:  # pragma: no cover - depends on local database availability
        return None


@pytest.fixture(scope="session")
def policy_baseline() -> set[tuple[str, str]]:
    """The policy grants a test may rely on: migration-seeded, nothing else.

    Runs once per session, before any test.  Pre-existing leaked grants are
    deactivated so a suite cannot pass on ambient ALLOW left by an earlier run,
    and every migration-seeded set is restored to ``active`` — the status the
    migrations intend, and the status one of the suites had accidentally left
    ``inactive`` for whatever ran next.  The surviving set is the baseline the
    per-test guard resets to.
    """
    known = _all_policy_set_keys()
    if known is None:
        return set()
    seeded = {key for key in known if key[0] in SEEDED_POLICY_SETS}
    _deactivate(known - seeded)
    _set_status(seeded, "active")
    return seeded


@pytest.fixture(scope="session")
def _ambient_state(policy_baseline: set[tuple[str, str]]) -> None:
    """Provision the ambient database state the suite silently assumes.

    Several files depend on rows that *other* files happen to create, and the
    suite only passed because a developer's database had accumulated them over
    many runs.  A fresh database — CI, a new machine, a rebuilt test database —
    exposes the dependency:

    * ``test_experience_api`` needs an **active principal** for local-dev.  The
      only code that ever inserts one is ``test_policy_api``, which sorts
      *after* it, so on a clean database ``_active_principal`` gets no row and
      the test dies with ``TypeError: 'NoneType' object is not subscriptable``.
    * ``test_ai_canvas_chat_api``, ``test_cw4_canvas_projection`` and
      ``test_cw5_ai_planning`` look up a **second tenant** whose slug matches
      ``%-other%``.  That row was only ever created by
      ``test_plugin_topology_chain_integration``, which also sorts later, giving
      ``assert None is not None``.

    Creating both here — idempotently, once per session — removes the ordering
    dependency instead of leaving the suite green only on a warmed database.
    Nothing is deleted; both inserts are ``ON CONFLICT DO NOTHING``.
    """
    del policy_baseline  # ordering only: this must run after the baseline sweep
    url = os.environ.get("AUDIT_NETWORK_TEST_DATABASE_URL") or TEST_DATABASE_URL
    try:
        import psycopg2
    except ImportError:  # pragma: no cover
        return
    try:
        with psycopg2.connect(url) as connection, connection.cursor() as cur:
            cur.execute("SELECT id FROM iam.tenants WHERE slug=%s", (_LOCAL_DEV_SLUG,))
            row = cur.fetchone()
            if row is None:
                return
            tenant_id = str(row[0])
            cur.execute("SELECT set_config('app.tenant_id', %s, false)", (tenant_id,))
            cur.execute(
                "INSERT INTO iam.principals(tenant_id,kind,subject,display_name) "
                "VALUES(%s,'human','conftest-ambient-reviewer','环境预置评审人') "
                "ON CONFLICT DO NOTHING",
                (tenant_id,),
            )
        with psycopg2.connect(url) as connection, connection.cursor() as cur:
            cur.execute(
                "INSERT INTO iam.tenants(slug,name) VALUES(%s,%s) ON CONFLICT(slug) DO NOTHING",
                ("m3-other-shared", "环境预置第二租户"),
            )
    except Exception:  # pragma: no cover - database unavailable ⇒ nothing to do
        return


@pytest.fixture(autouse=True)
def _policy_grants_do_not_leak(
    request, _ambient_state: None, policy_baseline: set[tuple[str, str]]
):
    """Reset policy grants around every test: no leaks, no accidental losses.

    Roughly 34 integration modules insert their own allow rules into
    ``policy.policy_sets`` and most never clean up.  In the shared test database
    1621 sets had accumulated as ``active`` — from ~34 different modules, not
    just one.  Leaked grants are not merely untidy: a fail-closed test that
    deactivates a single policy set still received ALLOW from a leftover set, so
    it passed (or failed) for a reason unrelated to what it asserts, and the
    suite became order-dependent.

    The reset is symmetric, because both directions bite:

    * a set activated *during* the test is switched back off (a leaked grant);
    * a baseline set the test switched off is switched back on.  Several suites
      disable ``local-plugin-topology-read`` or every set mentioning a
      capability, and one of them was leaving it inactive — the next suite then
      failed for reasons that had nothing to do with it.

    Nothing is deleted — the app role holds no DELETE on this table — so this is
    purely a status reset and stays reversible.
    """
    # Pure unit/contract tests never open the database; skip the round trips.
    path = str(getattr(request.node, "fspath", "")).replace("\\", "/")
    if "/unit/" in path or "/contract/" in path:
        yield
        return
    yield
    if not policy_baseline:
        return
    after = _active_policy_sets()
    if after is None:
        return
    _deactivate(after - policy_baseline)
    _set_status(policy_baseline - after, "active")


# ---------------------------------------------------------------------------
# Experience rows created by the suite must not accumulate.
#
# ``service.start_plan_run`` projects every finished DAG run into the experience
# layer (``experience.node_observations`` / ``edge_observations``), which is what
# makes "which projects has this plugin been proven in?" answerable.  Those
# tables are append-only for the app role and keyed by a random run id, so a
# suite that exercises DAG chains (eight modules do) would leave hundreds of
# rows behind on *every* invocation — and the rollups would drift upward
# forever, which is exactly the shared-test-database decay the repository has
# been burned by before.
#
# Session scoped on purpose: snapshot once, purge once, converge once.  A
# per-test purge would recompute every rollup ~370 times for no extra fidelity.
# ---------------------------------------------------------------------------


def _experience_run_ids() -> set[str] | None:
    """Run ids with an experience observation, or ``None`` if the DB is absent."""
    url = os.environ.get("AUDIT_NETWORK_TEST_DATABASE_URL") or TEST_DATABASE_URL
    try:
        import psycopg2

        with psycopg2.connect(url) as connection, connection.cursor() as cur:
            cur.execute(
                "SELECT set_config('app.tenant_id', "
                "(SELECT id::text FROM iam.tenants WHERE slug=%s), false)",
                (_LOCAL_DEV_SLUG,),
            )
            cur.execute("SELECT DISTINCT run_id FROM experience.node_observations")
            return {str(row[0]) for row in cur.fetchall()}
    except Exception:  # pragma: no cover - depends on local database availability
        return None


def _purge_experience_runs(run_ids: set[str]) -> None:
    """Drop the given runs' observations, then recompute the rollups.

    The migrator connection is required: the app role holds no DELETE on the
    observation tables by design (evidence is never rewritten).  ``rebuild``
    recomputes the rollups from the observations that remain, so the database
    converges rather than merely forgetting.
    """
    if not run_ids:
        return
    migrator = os.environ.get("AUDIT_NETWORK_MIGRATOR_DATABASE_URL") or TEST_MIGRATOR_DATABASE_URL
    app = os.environ.get("AUDIT_NETWORK_TEST_DATABASE_URL") or TEST_DATABASE_URL
    try:
        import psycopg2

        with psycopg2.connect(migrator) as connection, connection.cursor() as cur:
            cur.execute(
                "SELECT id::text FROM iam.tenants WHERE slug=%s",
                (_LOCAL_DEV_SLUG,),
            )
            row = cur.fetchone()
            if row is None:
                return
            tenant = str(row[0])
            cur.execute("SELECT set_config('app.tenant_id', %s, false)", (tenant,))
            cur.fetchone()
            for run_id in sorted(run_ids):
                cur.execute("DELETE FROM experience.node_observations WHERE run_id=%s", (run_id,))
                cur.execute("DELETE FROM experience.edge_observations WHERE run_id=%s", (run_id,))
            # Observations gone, the proposals that cited them keep advertising
            # success counts with nothing behind them — and ``_upsert_suggestion``
            # refreshes them on the next run, so the phantom grows.  Prune them
            # on this same migrator cursor: the app role has no DELETE here.
            from packages.experience import ExperienceProjector

            ExperienceProjector.prune_dangling_suggestions(cur)
            connection.commit()

        from uuid import UUID

        ExperienceProjector(app).rebuild(tenant_id=UUID(tenant))
    except Exception:  # pragma: no cover - cleanup must never fail the suite
        return


@pytest.fixture(scope="session", autouse=True)
def _experience_rows_do_not_accumulate() -> Iterator[None]:
    baseline = _experience_run_ids()
    yield
    if baseline is None:
        return
    after = _experience_run_ids()
    if after is None:
        return
    _purge_experience_runs(after - baseline)
