"""Grant the local desktop read access to the rules registry and version lineage.

Two more read-only views ship with the P2 batch: the rule registry (contracts,
policy sets, report templates, skills) and the per-plugin version history.
Each is a GET with no write path, but a read is still an access: without an
explicit grant the policy gateway fails closed and the page would be
indistinguishable from a broken one.  Seeded like the other ``local-*`` read
sets, and reversible by deactivating the set.
"""

from alembic import op

revision = "0066_rules_lineage_read_policy"
down_revision = "0065_search_library_cases_policy"
branch_labels = None
depends_on = None


_RULES = (
    ("local-rules-registry-read", "0a1b2c3d-4e5f-6a7b-8c9d-0e1f2a3b4c5d", "rules.registry.read"),
    ("local-plugin-versions-read", "1b2c3d4e-5f6a-7b8c-9d0e-1f2a3b4c5d6e", "plugin.versions.read"),
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
