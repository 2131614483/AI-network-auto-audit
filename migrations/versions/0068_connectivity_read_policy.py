"""Grant the local desktop read access to the connectivity report.

The connectivity / supply-closure view used to be script-only: the page told the
operator to run ``scripts/report-connectivity.py`` by hand, because there was no
read endpoint.  ``GET /api/v1/connectivity/report`` now serves the same figures
from the same implementation, but a read is still an access — without an explicit
grant the policy gateway fails closed and the page would look broken rather than
forbidden.  Seeded like the other ``local-*`` read sets and reversible by
deactivating the set.
"""

from alembic import op

revision = "0068_connectivity_read_policy"
down_revision = "0067_graph_knn_gist_indexes"
branch_labels = None
depends_on = None


_RULES = (
    (
        "local-connectivity-read",
        "2c3d4e5f-6a7b-8c9d-0e1f-2a3b4c5d6e7f",
        "connectivity.report.read",
    ),
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
