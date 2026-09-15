"""Grant the local desktop read access to the plugin lifecycle view.

The lifecycle view is read-only, but a read is still an access: without an
explicit grant the policy gateway fails closed and the page would be
indistinguishable from a broken one.  Seeded like the other ``local-*`` read
sets, and reversible by deactivating the set.
"""

from alembic import op

revision = "0063_plugin_lifecycle_policy"
down_revision = "0062_archive_link"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        SELECT set_config('app.tenant_id', id::text, true)
          FROM iam.tenants WHERE slug='local-dev';
        INSERT INTO policy.policy_sets(tenant_id,name,version,status,rules)
        SELECT id,'local-plugin-lifecycle-read',1,'active',jsonb_build_array(
          jsonb_build_object(
            'rule_id','7d4a1c62-2f83-4b57-9ae1-6c05d8f2b310',
            'effect','allow',
            'match',jsonb_build_object(
              'capabilities',jsonb_build_array('plugin.lifecycle.read')
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
        WHERE name='local-plugin-lifecycle-read' AND version=1"""
    )
