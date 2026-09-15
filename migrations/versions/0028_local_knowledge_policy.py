"""Install the explicit local knowledge-workbench capability whitelist."""

from alembic import op

revision = "0028_local_knowledge_policy"
down_revision = "0027_knowledge_retrieval"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        SELECT set_config('app.tenant_id', id::text, true)
          FROM iam.tenants WHERE slug='local-dev';
        INSERT INTO policy.policy_sets(tenant_id,name,version,status,rules)
        SELECT id,'local-knowledge-workbench',1,'active',jsonb_build_array(
          jsonb_build_object(
            'rule_id','f39efacc-05f0-470e-a313-4d2ee2cc389e',
            'effect','allow',
            'match',jsonb_build_object(
              'capabilities',jsonb_build_array(
                'knowledge.read','knowledge.search','knowledge.embedding.write'
              )
            )
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
        WHERE name='local-knowledge-workbench' AND version=1"""
    )
