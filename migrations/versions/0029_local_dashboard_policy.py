"""Allow the local desktop to read cross-domain operational projections."""

from alembic import op

revision = "0029_local_dashboard_policy"
down_revision = "0028_local_knowledge_policy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        SELECT set_config('app.tenant_id', id::text, true)
          FROM iam.tenants WHERE slug='local-dev';
        INSERT INTO policy.policy_sets(tenant_id,name,version,status,rules)
        SELECT id,'local-operations-dashboard',1,'active',jsonb_build_array(
          jsonb_build_object(
            'rule_id','d18b649b-b8e7-44b5-98f5-46eeb0829864',
            'effect','allow',
            'match',jsonb_build_object('capabilities',jsonb_build_array('platform.dashboard.read'))
          )
        )
        FROM iam.tenants WHERE slug='local-dev'
        ON CONFLICT (tenant_id,name,version) DO UPDATE
          SET status=EXCLUDED.status,rules=EXCLUDED.rules;
        """
    )


def downgrade() -> None:
    op.execute(
        """UPDATE policy.policy_sets SET status='inactive'
        WHERE name='local-operations-dashboard' AND version=1"""
    )
