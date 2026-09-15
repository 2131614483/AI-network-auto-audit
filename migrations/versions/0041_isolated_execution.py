"""M5: execution_ledger gains mode 'isolated' plus real-run metadata columns.

The isolated branch reuses the verified builtin plugin allow-list and the
read-only child-process runtime.  The new ``topology.chain.execute.isolated``
policy rule is seeded *inactive* so nothing can start a child process unless a
tenant explicitly activates it (fail-closed by default).
"""

from __future__ import annotations

from alembic import op

revision = "0041_isolated_execution"
down_revision = "0040_execution_verification"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # -- widen mode CHECK to admit isolated (real read-only child runs) -------
    op.execute(
        """
        ALTER TABLE topology.execution_ledger DROP CONSTRAINT IF EXISTS execution_ledger_mode_check;
        ALTER TABLE topology.execution_ledger ADD CONSTRAINT execution_ledger_mode_check
          CHECK (mode IN ('simulated', 'isolated'));
        """
    )
    # -- real-run metadata columns (all DEFAULT so existing rows stay valid) --
    op.execute(
        """
        ALTER TABLE topology.execution_ledger ADD COLUMN IF NOT EXISTS plugin_id            text   NOT NULL DEFAULT '';
        ALTER TABLE topology.execution_ledger ADD COLUMN IF NOT EXISTS plugin_version       text   NOT NULL DEFAULT '';
        ALTER TABLE topology.execution_ledger ADD COLUMN IF NOT EXISTS runtime_code_sha256  text   NOT NULL DEFAULT '';
        ALTER TABLE topology.execution_ledger ADD COLUMN IF NOT EXISTS input_sha256         text   NOT NULL DEFAULT '';
        ALTER TABLE topology.execution_ledger ADD COLUMN IF NOT EXISTS output_artifact_refs jsonb  NOT NULL DEFAULT '[]';
        """
    )
    # -- isolated execution policy: fail-closed, seeded INACTIVE --------------
    op.execute(
        """
        INSERT INTO policy.policy_sets(tenant_id,name,version,status,rules)
        SELECT id,'local-plugin-topology-isolated',1,'inactive',jsonb_build_array(
          jsonb_build_object('rule_id','d04e5f6a-7b8c-9d0e-1f2a-3b4c5d6e7f80','effect','allow',
            'match',jsonb_build_object('capabilities',jsonb_build_array('topology.chain.execute.isolated'),
              'risk_classes',jsonb_build_array('medium')))
        )
        FROM iam.tenants WHERE slug='local-dev'
        ON CONFLICT (tenant_id,name,version) DO NOTHING;
        """
    )


def downgrade() -> None:
    raise RuntimeError("Topology execution evidence is retained; archive explicitly.")