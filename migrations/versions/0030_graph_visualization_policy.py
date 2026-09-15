"""Allow the local desktop to read bounded graph visualizations."""

from alembic import op

revision = "0030_graph_visualization_policy"
down_revision = "0029_local_dashboard_policy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        SELECT set_config('app.tenant_id', id::text, true)
          FROM iam.tenants WHERE slug='local-dev';
        INSERT INTO policy.policy_sets(tenant_id,name,version,status,rules)
        SELECT id,'local-graph-visualization',1,'active',jsonb_build_array(
          jsonb_build_object(
            'rule_id','f29138dc-7a1a-46b5-9f52-03b3396c21fe',
            'effect','allow',
            'match',jsonb_build_object('capabilities',jsonb_build_array('graph.visualize.read'))
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
        WHERE name='local-graph-visualization' AND version=1"""
    )
