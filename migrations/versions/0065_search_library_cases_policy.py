"""Grant the local desktop read access to the unified search, library and cases views.

Three more read-only views ship with this batch (cross-kind search, the document
asset index and the case x stage matrix).  Each is a GET with no write path, but
a read is still an access: without an explicit grant the policy gateway fails
closed and the page would be indistinguishable from a broken one.  Seeded like
the other ``local-*`` read sets, and reversible by deactivating the set.
"""

from alembic import op

revision = "0065_search_library_cases_policy"
down_revision = "0064_observability_read_policy"
branch_labels = None
depends_on = None

_RULES = (
    ("local-search-read", "d1e2f3a4-5b6c-7d8e-9f0a-1b2c3d4e5f60", "search.read"),
    ("local-library-read", "e5f60718-293a-4b5c-6d7e-8f90a1b2c3d4", "library.index.read"),
    ("local-cases-read", "f7a8b9c0-1d2e-3f4a-5b6c-7d8e9f0a1b2c", "cases.read"),
)


def upgrade() -> None:
    for name, rule_id, capability in _RULES:
        op.execute(
            f"""
            SELECT set_config('app.tenant_id', id::text, true)
              FROM iam.tenants WHERE slug='local-dev';
            INSERT INTO policy.policy_sets(tenant_id,name,version,status,rules)
            SELECT id,'{name}',1,'active',jsonb_build_array(
              jsonb_build_object(
                'rule_id','{rule_id}',
                'effect','allow',
                'match',jsonb_build_object(
                  'capabilities',jsonb_build_array('{capability}')
                )
              )
            )
            FROM iam.tenants WHERE slug='local-dev'
            ON CONFLICT (tenant_id,name,version) DO UPDATE
              SET status=EXCLUDED.status,rules=EXCLUDED.rules;
            """
        )


def downgrade() -> None:
    names = ", ".join(f"'{name}'" for name, _, _ in _RULES)
    op.execute(
        f"""UPDATE policy.policy_sets SET status='inactive'
        WHERE name IN ({names}) AND version=1"""
    )
