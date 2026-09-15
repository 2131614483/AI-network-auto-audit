"""Seed policy capabilities for the unified AI access layer.

Extends the local-dev ``local-plugin-topology-read`` set (last re-seeded by
0056) with three grants for ``packages/ai``:

* ``ai.settings.read``  — read the effective provider configuration (masked);
* ``ai.settings.write`` — persist provider settings into the project ``.env``;
* ``ai.provider.test``  — user-initiated connectivity probe of the channel.

No execution authority is added: these capabilities configure and diagnose the
model transport, they do not run plugins, plans or chains.  The write grant stays
``low`` risk and the probe is a read-only diagnostic.  Idempotent and local-dev
only, mirroring 0054/0056.
"""

from alembic import op

revision = "0058_ai_settings_policy"
down_revision = "0057_experience_solidify"
branch_labels = None
depends_on = None


# 0056 rules verbatim, plus the three AI access grants.
_RULES = """
          jsonb_build_object('rule_id','3c8d4e5f-6a7b-8c9d-0e1f-2a3b4c5d6e7f','effect','allow',
            'match',jsonb_build_object('capabilities',jsonb_build_array('topology.cluster.read'))),
          jsonb_build_object('rule_id','4d9e5f6a-7b8c-9d0e-1f2a-3b4c5d6e7f80','effect','allow',
            'match',jsonb_build_object('capabilities',jsonb_build_array('topology.blueprint.read'))),
          jsonb_build_object('rule_id','5eaf6b7c-8d9e-0f1a-2b3c-4d5e6f7a8b9c','effect','allow',
            'match',jsonb_build_object('capabilities',jsonb_build_array('topology.plan.read'))),
          jsonb_build_object('rule_id','6fb7c8d9-0e1f-2a3b-4c5d-6e7f8a9b0c1d','effect','allow',
            'match',jsonb_build_object('capabilities',jsonb_build_array('topology.bridge.read'),
              'risk_classes',jsonb_build_array('read_only'))),
          jsonb_build_object('rule_id','7ec8d9e0-1f2a-3b4c-5d6e-7f8a9b0c1d2e','effect','allow',
            'match',jsonb_build_object('capabilities',jsonb_build_array('topology.chain.read'),
              'risk_classes',jsonb_build_array('read_only'))),
          jsonb_build_object('rule_id','8fd9e0f1-2a3b-4c5d-6e7f-8a9b0c1d2e3f','effect','allow',
            'match',jsonb_build_object('capabilities',jsonb_build_array('topology.intent.read'),
              'risk_classes',jsonb_build_array('read_only'))),
          jsonb_build_object('rule_id','9eaf0b1c-3a4b-5c6d-7e8f-9a0b1c2d3e4f','effect','allow',
            'match',jsonb_build_object('capabilities',jsonb_build_array('topology.chain.write'),
              'risk_classes',jsonb_build_array('low'))),
          jsonb_build_object('rule_id','a1b2c3d4-0e1f-4a2b-9c3d-4e5f60718293','effect','allow',
            'match',jsonb_build_object('capabilities',jsonb_build_array('topology.plugin_nebula.read'),
              'risk_classes',jsonb_build_array('read_only'),
              'side_effects',jsonb_build_array('read_only'))),
          jsonb_build_object('rule_id','b2c3d4e5-1f2a-4b3c-ad4e-5f6071829304','effect','allow',
            'match',jsonb_build_object('capabilities',jsonb_build_array('topology.nebula_experience.read'),
              'risk_classes',jsonb_build_array('read_only'),
              'side_effects',jsonb_build_array('read_only'))),
          jsonb_build_object('rule_id','c3d4e5f6-2a3b-4c4d-be5f-607182930405','effect','allow',
            'match',jsonb_build_object('capabilities',jsonb_build_array('topology.nebula_experience.suggestion.write'),
              'risk_classes',jsonb_build_array('low'))),
          jsonb_build_object('rule_id','d4e5f607-3b4c-4d5e-cf60-718293040506','effect','allow',
            'match',jsonb_build_object('capabilities',jsonb_build_array('topology.nebula_experience.rebuild'),
              'risk_classes',jsonb_build_array('low'))),
          jsonb_build_object('rule_id','e5f60718-4c5d-4e6f-d071-829304050607','effect','allow',
            'match',jsonb_build_object('capabilities',jsonb_build_array('ai.settings.read'),
              'risk_classes',jsonb_build_array('read_only'),
              'side_effects',jsonb_build_array('read_only'))),
          jsonb_build_object('rule_id','f6071829-5d6e-4f70-e182-930405060708','effect','allow',
            'match',jsonb_build_object('capabilities',jsonb_build_array('ai.settings.write'),
              'risk_classes',jsonb_build_array('low'))),
          jsonb_build_object('rule_id','0718293a-6e7f-4081-f293-040506070809','effect','allow',
            'match',jsonb_build_object('capabilities',jsonb_build_array('ai.provider.test'),
              'risk_classes',jsonb_build_array('low'),
              'side_effects',jsonb_build_array('read_only')))
"""

# 0056 rule set (used by downgrade to restore the pre-AI-access state).
_RULES_0056 = """
          jsonb_build_object('rule_id','3c8d4e5f-6a7b-8c9d-0e1f-2a3b4c5d6e7f','effect','allow',
            'match',jsonb_build_object('capabilities',jsonb_build_array('topology.cluster.read'))),
          jsonb_build_object('rule_id','4d9e5f6a-7b8c-9d0e-1f2a-3b4c5d6e7f80','effect','allow',
            'match',jsonb_build_object('capabilities',jsonb_build_array('topology.blueprint.read'))),
          jsonb_build_object('rule_id','5eaf6b7c-8d9e-0f1a-2b3c-4d5e6f7a8b9c','effect','allow',
            'match',jsonb_build_object('capabilities',jsonb_build_array('topology.plan.read'))),
          jsonb_build_object('rule_id','6fb7c8d9-0e1f-2a3b-4c5d-6e7f8a9b0c1d','effect','allow',
            'match',jsonb_build_object('capabilities',jsonb_build_array('topology.bridge.read'),
              'risk_classes',jsonb_build_array('read_only'))),
          jsonb_build_object('rule_id','7ec8d9e0-1f2a-3b4c-5d6e-7f8a9b0c1d2e','effect','allow',
            'match',jsonb_build_object('capabilities',jsonb_build_array('topology.chain.read'),
              'risk_classes',jsonb_build_array('read_only'))),
          jsonb_build_object('rule_id','8fd9e0f1-2a3b-4c5d-6e7f-8a9b0c1d2e3f','effect','allow',
            'match',jsonb_build_object('capabilities',jsonb_build_array('topology.intent.read'),
              'risk_classes',jsonb_build_array('read_only'))),
          jsonb_build_object('rule_id','9eaf0b1c-3a4b-5c6d-7e8f-9a0b1c2d3e4f','effect','allow',
            'match',jsonb_build_object('capabilities',jsonb_build_array('topology.chain.write'),
              'risk_classes',jsonb_build_array('low'))),
          jsonb_build_object('rule_id','a1b2c3d4-0e1f-4a2b-9c3d-4e5f60718293','effect','allow',
            'match',jsonb_build_object('capabilities',jsonb_build_array('topology.plugin_nebula.read'),
              'risk_classes',jsonb_build_array('read_only'),
              'side_effects',jsonb_build_array('read_only'))),
          jsonb_build_object('rule_id','b2c3d4e5-1f2a-4b3c-ad4e-5f6071829304','effect','allow',
            'match',jsonb_build_object('capabilities',jsonb_build_array('topology.nebula_experience.read'),
              'risk_classes',jsonb_build_array('read_only'),
              'side_effects',jsonb_build_array('read_only'))),
          jsonb_build_object('rule_id','c3d4e5f6-2a3b-4c4d-be5f-607182930405','effect','allow',
            'match',jsonb_build_object('capabilities',jsonb_build_array('topology.nebula_experience.suggestion.write'),
              'risk_classes',jsonb_build_array('low'))),
          jsonb_build_object('rule_id','d4e5f607-3b4c-4d5e-cf60-718293040506','effect','allow',
            'match',jsonb_build_object('capabilities',jsonb_build_array('topology.nebula_experience.rebuild'),
              'risk_classes',jsonb_build_array('low')))
"""


def _reseed(rules: str) -> None:
    op.execute(
        f"""
        SELECT set_config('app.tenant_id', (SELECT id::text FROM iam.tenants WHERE slug='local-dev'), true);

        INSERT INTO policy.policy_sets(tenant_id,name,version,status,rules)
        SELECT id,'local-plugin-topology-read',1,'active',jsonb_build_array(
        {rules}
        )
        FROM iam.tenants WHERE slug='local-dev'
        ON CONFLICT (tenant_id,name,version) DO UPDATE
          SET status=EXCLUDED.status,rules=EXCLUDED.rules;
        """
    )


def upgrade() -> None:
    _reseed(_RULES)


def downgrade() -> None:
    _reseed(_RULES_0056)
