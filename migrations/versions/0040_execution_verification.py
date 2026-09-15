"""Plugin Topology M4: invocation approvals and the shadow execution ledger.

Adds the relational home for governed precise invocation: a human-approval
ledger over ``requires_approval`` intents and an immutable per-node execution
ledger whose entries are pure ``simulated`` state projections (mode checksum
reproduces the service formula exactly; integration tests assert a re-execute
of the seeded chain is skipped as already-run).

Both tables are RLS FORCE protected, INSERT/SELECT only - evidence is never
overwritten or hard-deleted.
"""

from __future__ import annotations

import hashlib
import json

from alembic import op

revision = "0040_execution_verification"
down_revision = "0039_invocation_chain"

_SEED_CHAIN_KEY = "chain-" + hashlib.sha256(
    b"plan-" + hashlib.sha256(b"ledger-quality-slot|research-note-slot").hexdigest()[:16].encode()
    + b"|ledger-quality-slot|research-note-slot"
).hexdigest()[:16]
_SEED_VERSION = "1.0.0"
_SEED_TRACE = "00000000-0000-0000-0000-000000000040"

_LEDGER_TABLES = ("invocation_approvals", "execution_ledger")


def _exec_id(slot: str) -> str:
    return "exec-" + hashlib.sha256(f"{slot}|simulated|{_SEED_VERSION}".encode("utf-8")).hexdigest()[:16]


def _node_checksum(slot: str, ordinal: int, input_refs: list[str], output_refs: list[str]) -> str:
    """Canonical shadow-execution digest (mirrors executor.node_output_checksum)."""
    payload = {
        "slot_key": slot,
        "ordinal": ordinal,
        "version": _SEED_VERSION,
        "input_refs": sorted(set(input_refs)),
        "output_refs": sorted(set(output_refs)),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def upgrade() -> None:
    # -- DDL ---------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS topology.invocation_approvals (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id uuid NOT NULL REFERENCES iam.tenants(id),
          chain_id uuid NOT NULL REFERENCES topology.invocation_chains(id),
          intent_id uuid REFERENCES topology.invocation_intents(id),
          slot_key text NOT NULL,
          decision text NOT NULL CHECK (decision IN ('approve', 'reject')),
          approver text NOT NULL,
          reason text NOT NULL DEFAULT '',
          idempotency_key text NOT NULL,
          trace_id text NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE(tenant_id, chain_id, slot_key, decision)
        );
        CREATE INDEX IF NOT EXISTS topology_invocation_approvals_chain_idx
          ON topology.invocation_approvals (tenant_id, chain_id, created_at DESC);

        CREATE TABLE IF NOT EXISTS topology.execution_ledger (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id uuid NOT NULL REFERENCES iam.tenants(id),
          chain_id uuid NOT NULL REFERENCES topology.invocation_chains(id),
          plan_node_slot_key text NOT NULL,
          ordinal int NOT NULL,
          mode text NOT NULL DEFAULT 'simulated' CHECK (mode = 'simulated'),
          input_refs jsonb NOT NULL DEFAULT '[]',
          output_refs jsonb NOT NULL DEFAULT '[]',
          output_contract text NOT NULL DEFAULT '',
          output_checksum text NOT NULL,
          status text NOT NULL CHECK (status IN ('pending', 'succeeded', 'failed')),
          policy_ref text NOT NULL DEFAULT '',
          idempotency_key text NOT NULL,
          trace_id text NOT NULL,
          started_at timestamptz NOT NULL DEFAULT now(),
          finished_at timestamptz,
          UNIQUE(tenant_id, chain_id, plan_node_slot_key, ordinal, mode)
        );
        CREATE INDEX IF NOT EXISTS topology_execution_ledger_chain_idx
          ON topology.execution_ledger (tenant_id, chain_id, ordinal);
        """
    )
    for table in _LEDGER_TABLES:
        op.execute(f"ALTER TABLE topology.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE topology.{table} FORCE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON topology.{table}")
        op.execute(
            f"CREATE POLICY {table}_tenant_isolation ON topology.{table} "
            "USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid) "
            "WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
        )
        # Append-only governance: no UPDATE/DELETE grant on evidence ledgers.
        op.execute(f"GRANT SELECT, INSERT ON topology.{table} TO audit_app")

    # -- deterministic seed ledger rows (mirrors service formulas) ---------
    checksum_ledger = _node_checksum("audit.ledger.validate", 0, [], ["audit-quality-candidates"])
    checksum_research = _node_checksum(
        "quant.research-note.draft", 1, ["contracts/jsonschema/audit-quality-candidates@1"], ["research-note-draft"]
    )
    seed_values = {
        "chain_key": _SEED_CHAIN_KEY,
        "mode": "simulated",
        "version": _SEED_VERSION,
        "trace": _SEED_TRACE,
        "exec_ledger": _exec_id("audit.ledger.validate"),
        "exec_research": _exec_id("quant.research-note.draft"),
        "checksum_ledger": checksum_ledger,
        "checksum_research": checksum_research,
        "refs_out_ledger": json.dumps(["audit-quality-candidates"]),
        "refs_out_research": json.dumps(["research-note-draft"]),
        "refs_in_research": json.dumps(["contracts/jsonschema/audit-quality-candidates@1"]),
        "ikey_ledger": f"seed-{_SEED_CHAIN_KEY}-audit.ledger.validate-exec",
        "ikey_research": f"seed-{_SEED_CHAIN_KEY}-quant.research-note.draft-exec",
        "ikey_approval": f"seed-{_SEED_CHAIN_KEY}-quant.research-note.draft-approve",
    }
    op.execute(
        f"""
        SELECT set_config('app.tenant_id', id::text, true) FROM iam.tenants WHERE slug='local-dev';

        INSERT INTO topology.execution_ledger
          (tenant_id, chain_id, plan_node_slot_key, ordinal, mode, input_refs, output_refs,
           output_contract, output_checksum, status, policy_ref, idempotency_key, trace_id)
        SELECT t.id, c.id, 'audit.ledger.validate', 0, '{seed_values["mode"]}', '[]'::jsonb,
               '{seed_values["refs_out_ledger"]}'::jsonb,
               'contracts/jsonschema/audit-quality-candidates@1',
               '{seed_values["checksum_ledger"]}', 'succeeded', 'policy_allowed',
               '{seed_values["ikey_ledger"]}', '{seed_values["trace"]}'
        FROM iam.tenants t
          JOIN topology.invocation_chains c ON c.tenant_id = t.id AND c.chain_key = '{seed_values["chain_key"]}'
        WHERE t.slug = 'local-dev'
        ON CONFLICT (tenant_id, chain_id, plan_node_slot_key, ordinal, mode) DO NOTHING;

        INSERT INTO topology.execution_ledger
          (tenant_id, chain_id, plan_node_slot_key, ordinal, mode, input_refs, output_refs,
           output_contract, output_checksum, status, policy_ref, idempotency_key, trace_id)
        SELECT t.id, c.id, 'quant.research-note.draft', 1, '{seed_values["mode"]}',
               '{seed_values["refs_in_research"]}'::jsonb, '{seed_values["refs_out_research"]}'::jsonb,
               'contracts/jsonschema/research-note-draft@1',
               '{seed_values["checksum_research"]}', 'succeeded', 'policy_allowed',
               '{seed_values["ikey_research"]}', '{seed_values["trace"]}'
        FROM iam.tenants t
          JOIN topology.invocation_chains c ON c.tenant_id = t.id AND c.chain_key = '{seed_values["chain_key"]}'
        WHERE t.slug = 'local-dev'
        ON CONFLICT (tenant_id, chain_id, plan_node_slot_key, ordinal, mode) DO NOTHING;

        -- Archived human-approval demo row: the seeded chain intents are all
        -- policy_allowed, so this is a read-only historical projection (the
        -- service would never mutate the intent status for this slot).
        INSERT INTO topology.invocation_approvals
          (tenant_id, chain_id, intent_id, slot_key, decision, approver, reason, idempotency_key, trace_id)
        SELECT t.id, c.id, NULL, 'quant.research-note.draft', 'approve', 'seed/archive-demo',
               '种子示例审批留痕（只读历史投影，合规演示）', '{seed_values["ikey_approval"]}', '{seed_values["trace"]}'
        FROM iam.tenants t
          JOIN topology.invocation_chains c ON c.tenant_id = t.id AND c.chain_key = '{seed_values["chain_key"]}'
        WHERE t.slug = 'local-dev'
        ON CONFLICT (tenant_id, chain_id, slot_key, decision) DO NOTHING;

        INSERT INTO policy.policy_sets(tenant_id,name,version,status,rules)
        SELECT id,'local-plugin-topology-read',1,'active',jsonb_build_array(
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
          jsonb_build_object('rule_id','ab1c2d3e-4f5a-6b7c-8d9e-0f1a2b3c4d5e','effect','allow',
            'match',jsonb_build_object('capabilities',jsonb_build_array('topology.chain.approve'),
              'risk_classes',jsonb_build_array('low'))),
          jsonb_build_object('rule_id','bc2d3e4f-5a6b-7c8d-9e0f-1a2b3c4d5e6f','effect','allow',
            'match',jsonb_build_object('capabilities',jsonb_build_array('topology.chain.execute'),
              'risk_classes',jsonb_build_array('medium'))),
          jsonb_build_object('rule_id','cd3e4f5a-6b7c-8d9e-0f1a-2b3c4d5e6f7a','effect','allow',
            'match',jsonb_build_object('capabilities',jsonb_build_array('topology.execution.read'),
              'risk_classes',jsonb_build_array('read_only')))
        )
        FROM iam.tenants WHERE slug='local-dev'
        ON CONFLICT (tenant_id,name,version) DO UPDATE
          SET status=EXCLUDED.status,rules=EXCLUDED.rules;
        """
    )


def downgrade() -> None:
    raise RuntimeError("Topology approvals and execution evidence are retained; archive explicitly.")