"""Align ``finding-draft-slot``'s declared input to the contract name its
producer actually publishes.

Defect this fixes: ``chain.ChainResolver._contract_binding`` binds a
``depends_on`` edge by the **intersection of the producer's declared outputs and
the consumer's declared inputs**.  The seeds disagreed on the name:

* ``0037`` publishes ``ledger-quality-slot.outputs = ['audit-quality-candidates']``
  (the **blueprint-layer** contract name, the same one ``0039``'s seeded
  invocation chain and ``0038``'s domain bridge reference);
* ``0053`` declared ``finding-draft-slot.inputs = ['candidates']`` — the
  **runtime capability layer** name from ``catalog.RUNTIME_CAPABILITIES``.

The intersection is therefore empty and materializing the seeded
``ledger-quality-slot -> finding-draft-slot`` edge raises

    ValueError: no shared contract item between ledger-quality-slot->finding-draft-slot

0053's own docstring claims the edge "compiles without an adapter" because both
sides share ``schema_ref`` — but the resolver compares contract *names*, not
schema refs, so the stated intent was never what the code does.  Aligning the
name is the minimal fix; teaching the resolver to bind on schema equality would
change binding semantics globally (two inputs with the same schema_ref would
become ambiguous) and contradicts what a ``depends_on`` edge means.

Idempotent and local-dev only, mirroring 0053.
"""

from alembic import op

revision = "0059_align_finding_draft_input"
down_revision = "0058_ai_settings_policy"
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
            SET capability_contract = jsonb_set(
                    capability_contract, '{inputs}', '["audit-quality-candidates"]'::jsonb, true
                )
            WHERE key='finding-draft-slot'
              AND capability_contract -> 'inputs' IS DISTINCT FROM '["audit-quality-candidates"]'::jsonb;
          END LOOP;
        END $$;
        """
    )


def downgrade() -> None:
    raise RuntimeError("Blueprint contracts are evidence; archive explicitly.")
