"""CW5: declare input ports on blueprint contracts so the canvas AI planner can
link capabilities into data edges.

Previously the seeded blueprint contracts declared only ``outputs`` (and no
``inputs``), so every node compiled with zero input ports and no data edge
could be formed — the planner honestly reported ``gap_report / contract_mismatch``.
This migration adds the input ports (idempotently): ``ledger-quality-slot``
consumes ``ledger``, ``finding-draft-slot`` consumes ``candidates`` (the
anomaly-candidate output of ``audit.ledger.validate``, same ``schema_ref``
``audit-quality-candidates`` so the edge compiles without an adapter).
"""

from alembic import op

revision = "0053_blueprint_input_contracts"
down_revision = "0052_node_attempts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        DECLARE r record;
        BEGIN
          FOR r IN SELECT id FROM iam.tenants WHERE slug='local-dev' LOOP
            PERFORM set_config('app.tenant_id', r.id::text, true);
            UPDATE topology.plugin_blueprints
            SET capability_contract = capability_contract || '{"inputs":["ledger"]}'::jsonb
            WHERE key='ledger-quality-slot' AND NOT capability_contract ? 'inputs';
            UPDATE topology.plugin_blueprints
            SET capability_contract = capability_contract || '{"inputs":["candidates"]}'::jsonb
            WHERE key='finding-draft-slot' AND NOT capability_contract ? 'inputs';
          END LOOP;
        END $$;
        """
    )


def downgrade() -> None:
    raise RuntimeError("Blueprint contracts are evidence; archive explicitly.")
