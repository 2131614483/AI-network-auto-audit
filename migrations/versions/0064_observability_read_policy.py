"""Grant the local desktop read access to the observability/evolution views.

Four read-only views ship with this migration's siblings (run index, failure
diagnostics, bundle re-verify, evolution rings).  Each is a GET with no write
path, but a read is still an access: without an explicit grant the policy gateway
fails closed and the page would be indistinguishable from a broken one.  Seeded
like the other ``local-*`` read sets, and reversible by deactivating the set.
"""

from alembic import op

revision = "0064_observability_read_policy"
down_revision = "0063_plugin_lifecycle_policy"
branch_labels = None
depends_on = None

_RULES = (
    ("local-runs-read", "6f2b9e40-8c15-4d3a-9a27-1e84b0c5d721", "observability.runs.read"),
    ("local-failures-read", "93a05c17-4e6b-4f82-b1d9-27c3a8e6f104", "observability.failures.read"),
    ("local-bundle-verify-read", "b81d7e29-5a34-4c60-8f12-9d40e7b3c856", "observability.evidence.verify"),
    ("local-evolution-read", "c45e9f81-2b76-4d08-a93e-617f0d28b439", "experience.evolution.read"),
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
