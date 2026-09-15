"""Plugin Topology M3: invocation chains, chain nodes and invocation intents.

Adds the relational home for precise relationship-chain invocation: a
deterministic chain derived from a plan_only routing plan, per-node intents
with Policy decisions, and (in the seed) a real plan + chain + intents so the
desktop workbench has non-empty read-only data.

Seed checksums reproduce the service formulas (RoutingPlan.checksum_digest /
InvocationChain.checksum_digest) exactly; integration tests assert that
re-materializing the seeded plan yields the identical chain_key and checksum
so drift to the canonical digest is caught early.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from alembic import op

revision = "0039_invocation_chain"
down_revision = "0038_topology_bridge_seeds"

_SEED_PLAN_KEY = "plan-" + hashlib.sha256(b"ledger-quality-slot|research-note-slot").hexdigest()[:16]
_SEED_VERSION = "1.0.0"
_SEED_TRACE = "00000000-0000-0000-0000-000000000039"

_CHAIN_TABLES = ("invocation_chains", "invocation_chain_nodes", "invocation_intents")


def _plan_checksum(nodes: list[dict[str, Any]], edges: list[dict[str, str]]) -> str:
    """Canonical routing plan digest (mirrors RoutingPlan.checksum_digest)."""
    payload = {
        "nodes": [
            {
                "slot_key": node["slot_key"],
                "capability": node["capability"],
                "blueprint_key": node["blueprint_key"],
                "alternatives": list(node["alternatives"]),
            }
            for node in nodes
        ],
        "edges": [{"source_node": e["source_node"], "target_node": e["target_node"], "relation_type": e["relation_type"]} for e in edges],
        "mode": "plan_only",
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _chain_checksum(chain_key: str, plan_key: str, order: list[dict[str, Any]], fallbacks: list[dict[str, Any]], bindings: list[dict[str, Any]]) -> str:
    """Canonical invocation chain digest (mirrors InvocationChain.checksum_digest)."""
    payload = {
        "chain_key": chain_key,
        "plan_key": plan_key,
        "mode": "plan_only",
        "chain_order": [
            {"slot_key": n["slot_key"], "blueprint_key": n["blueprint_key"], "capability": n["capability"], "ordinal": n["ordinal"], "role": n["role"]}
            for n in order
        ],
        "fallbacks": [{"slot_key": f["slot_key"], "blueprint_key": f["blueprint_key"], "order": f["order"], "reason": f["reason"]} for f in fallbacks],
        "port_bindings": [
            {"producer_slot": b["producer_slot"], "consumer_slot": b["consumer_slot"], "relation_type": b["relation_type"], "bind_mode": b["bind_mode"], "contract_ref": b["contract_ref"], "payload_disallowed": b["payload_disallowed"]}
            for b in bindings
        ],
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _chain_key(plan_key: str, order: list[str]) -> str:
    return "chain-" + hashlib.sha256("|".join([plan_key, *order]).encode("utf-8")).hexdigest()[:16]


def upgrade() -> None:
    # -- DDL ---------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS topology.invocation_chains (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id uuid NOT NULL REFERENCES iam.tenants(id),
          chain_key text NOT NULL,
          plan_id uuid NOT NULL REFERENCES topology.routing_plans(id),
          mode text NOT NULL DEFAULT 'plan_only' CHECK (mode = 'plan_only'),
          chain_json jsonb NOT NULL,
          chain_checksum text NOT NULL,
          planner_version text NOT NULL,
          reason text NOT NULL DEFAULT '',
          trace_id text NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE(tenant_id, chain_key)
        );
        CREATE INDEX IF NOT EXISTS topology_invocation_chains_plan_idx
          ON topology.invocation_chains (tenant_id, plan_id, created_at DESC);

        CREATE TABLE IF NOT EXISTS topology.invocation_chain_nodes (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id uuid NOT NULL REFERENCES iam.tenants(id),
          chain_id uuid NOT NULL REFERENCES topology.invocation_chains(id),
          plan_node_slot_key text NOT NULL,
          blueprint_id uuid NOT NULL REFERENCES topology.plugin_blueprints(id),
          ordinal int NOT NULL,
          role text NOT NULL DEFAULT 'primary' CHECK (role IN ('primary', 'fallback')),
          input_bindings jsonb NOT NULL DEFAULT '[]',
          expected_output jsonb NOT NULL DEFAULT '[]',
          UNIQUE(tenant_id, chain_id, plan_node_slot_key, role, ordinal)
        );

        CREATE TABLE IF NOT EXISTS topology.invocation_intents (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id uuid NOT NULL REFERENCES iam.tenants(id),
          chain_id uuid NOT NULL REFERENCES topology.invocation_chains(id),
          plan_node_slot_key text NOT NULL,
          role text NOT NULL DEFAULT 'primary' CHECK (role IN ('primary', 'fallback')),
          capability text NOT NULL,
          intent_json jsonb NOT NULL,
          policy_decision text NOT NULL CHECK (policy_decision IN ('allowed', 'requires_approval', 'denied')),
          approval_ref text,
          status text NOT NULL CHECK (status IN ('materialized', 'policy_allowed', 'approved_projection', 'denied')),
          idempotency_key text NOT NULL,
          trace_id text NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE(tenant_id, chain_id, plan_node_slot_key, role, policy_decision, status)
        );
        """
    )
    for table in _CHAIN_TABLES:
        op.execute(f"ALTER TABLE topology.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE topology.{table} FORCE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON topology.{table}")
        op.execute(
            f"CREATE POLICY {table}_tenant_isolation ON topology.{table} "
            "USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid) "
            "WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
        )
        op.execute(f"GRANT SELECT, INSERT, UPDATE ON topology.{table} TO audit_app")

    # -- deterministic seed plan + chain + intents -------------------------
    seed_nodes = [
        {"slot_key": "audit.ledger.validate", "capability": "audit.ledger.validate",
         "blueprint_key": "ledger-quality-slot", "alternatives": []},
        {"slot_key": "quant.research-note.draft", "capability": "quant.research-note.draft",
         "blueprint_key": "research-note-slot", "alternatives": []},
    ]
    seed_edges = [
        {"source_node": "ledger-quality-slot", "target_node": "research-note-slot", "relation_type": "bridge"},
    ]
    seed_plan_checksum = _plan_checksum(seed_nodes, seed_edges)
    plan_json = {
        "kind": "plugin_routing_plan",
        "mode": "plan_only",
        "plan_key": _SEED_PLAN_KEY,
        "checksum": seed_plan_checksum,
        "nodes": seed_nodes,
        "edges": seed_edges,
        "release_locked": True,
        "planner_version": _SEED_VERSION,
    }

    chain_key = _chain_key(_SEED_PLAN_KEY, ["ledger-quality-slot", "research-note-slot"])
    order = [
        {"slot_key": "audit.ledger.validate", "blueprint_key": "ledger-quality-slot", "capability": "audit.ledger.validate", "ordinal": 0, "role": "primary"},
        {"slot_key": "quant.research-note.draft", "blueprint_key": "research-note-slot", "capability": "quant.research-note.draft", "ordinal": 1, "role": "primary"},
    ]
    bindings = [
        {"producer_slot": "audit.ledger.validate", "consumer_slot": "quant.research-note.draft",
         "relation_type": "bridge", "bind_mode": "bridge_ref",
         "contract_ref": "contracts/jsonschema/audit-quality-candidates@1", "payload_disallowed": True},
    ]
    chain_checksum = _chain_checksum(chain_key, _SEED_PLAN_KEY, order, [], bindings)
    chain_json = {
        "kind": "invocation_chain",
        "chain_key": chain_key,
        "plan_key": _SEED_PLAN_KEY,
        "mode": "plan_only",
        "planner_version": _SEED_VERSION,
        "chain_order": order,
        "fallbacks": [],
        "port_bindings": bindings,
        "checksum": chain_checksum,
        "release_locked": True,
    }

    intent_template = {
        "isolation": "isolated_subprocess",
        "side_effects": "read_only",
        "policy_decision": "allowed",
        "approval_ref": None,
        "status": "policy_allowed",
    }
    seed_intents = [
        {
            "kind": "invocation_intent",
            "slot_key": "audit.ledger.validate",
            "role": "primary",
            "capability": "audit.ledger.validate",
            "expected_inputs": [],
            "expected_outputs": ["audit-quality-candidates"],
            **intent_template,
        },
        {
            "kind": "invocation_intent",
            "slot_key": "quant.research-note.draft",
            "role": "primary",
            "capability": "quant.research-note.draft",
            "expected_inputs": ["contracts/jsonschema/audit-quality-candidates@1"],
            "expected_outputs": ["research-note-draft"],
            **intent_template,
        },
    ]

    seed_values = {
        "plan_key": _SEED_PLAN_KEY,
        "plan_json": json.dumps(plan_json),
        "plan_checksum": seed_plan_checksum,
        "version": _SEED_VERSION,
        "trace": _SEED_TRACE,
        "budget": json.dumps({"max_candidates": 8, "max_chain_length": 4, "max_latency_ms": 5000}),
        "chain_key": chain_key,
        "chain_checksum": chain_checksum,
        "chain_json": json.dumps(chain_json),
        "node_ledger": json.dumps(["audit-quality-candidates"]),
        "node_research": json.dumps(["research-note-draft"]),
        "intent_ledger": json.dumps(seed_intents[0]),
        "intent_research": json.dumps(seed_intents[1]),
        "ikey_ledger": f"seed-{chain_key}-audit.ledger.validate",
        "ikey_research": f"seed-{chain_key}-quant.research-note.draft",
    }
    op.execute(
        f"""
        SELECT set_config('app.tenant_id', id::text, true) FROM iam.tenants WHERE slug='local-dev';

        INSERT INTO topology.routing_plans
          (tenant_id, plan_key, mission_key, mode, plan_json, topology_release_id, planner_version, budget, checksum, trace_id)
        SELECT t.id, '{seed_values["plan_key"]}', 'audit.ledger.validate|quant.research-note.draft', 'plan_only',
               '{seed_values["plan_json"]}'::jsonb, r.id, '{seed_values["version"]}',
               '{seed_values["budget"]}'::jsonb, '{seed_values["plan_checksum"]}', '{seed_values["trace"]}'
        FROM iam.tenants t
          JOIN topology.topology_releases r ON r.tenant_id = t.id AND r.status = 'published'
        WHERE t.slug = 'local-dev'
        ON CONFLICT (tenant_id, plan_key) DO NOTHING;

        INSERT INTO topology.routing_plan_nodes(tenant_id, plan_id, blueprint_id, slot_key, alternatives)
        SELECT t.id, p.id, b.id, 'audit.ledger.validate', '[]'
        FROM iam.tenants t
          JOIN topology.routing_plans p ON p.tenant_id = t.id AND p.plan_key = '{seed_values["plan_key"]}'
          JOIN topology.plugin_blueprints b ON b.tenant_id = t.id AND b.key = 'ledger-quality-slot'
        WHERE t.slug = 'local-dev'
          AND NOT EXISTS (
            SELECT 1 FROM topology.routing_plan_nodes n
            JOIN topology.routing_plans p2 ON p2.id = n.plan_id
            WHERE n.tenant_id = t.id AND p2.plan_key = '{seed_values["plan_key"]}' AND n.slot_key = 'audit.ledger.validate'
          );

        INSERT INTO topology.routing_plan_nodes(tenant_id, plan_id, blueprint_id, slot_key, alternatives)
        SELECT t.id, p.id, b.id, 'quant.research-note.draft', '[]'
        FROM iam.tenants t
          JOIN topology.routing_plans p ON p.tenant_id = t.id AND p.plan_key = '{seed_values["plan_key"]}'
          JOIN topology.plugin_blueprints b ON b.tenant_id = t.id AND b.key = 'research-note-slot'
        WHERE t.slug = 'local-dev'
          AND NOT EXISTS (
            SELECT 1 FROM topology.routing_plan_nodes n
            JOIN topology.routing_plans p2 ON p2.id = n.plan_id
            WHERE n.tenant_id = t.id AND p2.plan_key = '{seed_values["plan_key"]}' AND n.slot_key = 'quant.research-note.draft'
          );

        INSERT INTO topology.routing_plan_edges(tenant_id, plan_id, source_node, target_node, relation_type)
        SELECT t.id, p.id, 'ledger-quality-slot', 'research-note-slot', 'bridge'
        FROM iam.tenants t
          JOIN topology.routing_plans p ON p.tenant_id = t.id AND p.plan_key = '{seed_values["plan_key"]}'
        WHERE t.slug = 'local-dev'
        ON CONFLICT (plan_id, source_node, target_node) DO NOTHING;

        INSERT INTO topology.invocation_chains
          (tenant_id, chain_key, plan_id, mode, chain_json, chain_checksum, planner_version, trace_id)
        SELECT t.id, '{seed_values["chain_key"]}', p.id, 'plan_only',
               '{seed_values["chain_json"]}'::jsonb, '{seed_values["chain_checksum"]}', '{seed_values["version"]}', '{seed_values["trace"]}'
        FROM iam.tenants t
          JOIN topology.routing_plans p ON p.tenant_id = t.id AND p.plan_key = '{seed_values["plan_key"]}'
        WHERE t.slug = 'local-dev'
        ON CONFLICT (tenant_id, chain_key) DO NOTHING;

        INSERT INTO topology.invocation_chain_nodes
          (tenant_id, chain_id, plan_node_slot_key, blueprint_id, ordinal, role, input_bindings, expected_output)
        SELECT t.id, c.id, 'audit.ledger.validate', b.id, 0, 'primary', '[]', '{seed_values["node_ledger"]}'::jsonb
        FROM iam.tenants t
          JOIN topology.invocation_chains c ON c.tenant_id = t.id AND c.chain_key = '{seed_values["chain_key"]}'
          JOIN topology.plugin_blueprints b ON b.tenant_id = t.id AND b.key = 'ledger-quality-slot'
        WHERE t.slug = 'local-dev'
          AND NOT EXISTS (
            SELECT 1 FROM topology.invocation_chain_nodes n
            JOIN topology.invocation_chains c2 ON c2.id = n.chain_id
            WHERE n.tenant_id = t.id AND c2.chain_key = '{seed_values["chain_key"]}'
              AND n.plan_node_slot_key = 'audit.ledger.validate' AND n.role = 'primary' AND n.ordinal = 0
          );

        INSERT INTO topology.invocation_chain_nodes
          (tenant_id, chain_id, plan_node_slot_key, blueprint_id, ordinal, role, input_bindings, expected_output)
        SELECT t.id, c.id, 'quant.research-note.draft', b.id, 1, 'primary', '[]', '{seed_values["node_research"]}'::jsonb
        FROM iam.tenants t
          JOIN topology.invocation_chains c ON c.tenant_id = t.id AND c.chain_key = '{seed_values["chain_key"]}'
          JOIN topology.plugin_blueprints b ON b.tenant_id = t.id AND b.key = 'research-note-slot'
        WHERE t.slug = 'local-dev'
          AND NOT EXISTS (
            SELECT 1 FROM topology.invocation_chain_nodes n
            JOIN topology.invocation_chains c2 ON c2.id = n.chain_id
            WHERE n.tenant_id = t.id AND c2.chain_key = '{seed_values["chain_key"]}'
              AND n.plan_node_slot_key = 'quant.research-note.draft' AND n.role = 'primary' AND n.ordinal = 1
          );

        INSERT INTO topology.invocation_intents
          (tenant_id, chain_id, plan_node_slot_key, role, capability, intent_json, policy_decision, approval_ref, status, idempotency_key, trace_id)
        SELECT t.id, c.id, 'audit.ledger.validate', 'primary', 'audit.ledger.validate',
               '{seed_values["intent_ledger"]}'::jsonb, 'allowed', NULL, 'policy_allowed', '{seed_values["ikey_ledger"]}', '{seed_values["trace"]}'
        FROM iam.tenants t
          JOIN topology.invocation_chains c ON c.tenant_id = t.id AND c.chain_key = '{seed_values["chain_key"]}'
        WHERE t.slug = 'local-dev'
          AND NOT EXISTS (
            SELECT 1 FROM topology.invocation_intents x
            JOIN topology.invocation_chains c2 ON c2.id = x.chain_id
            WHERE x.tenant_id = t.id AND c2.chain_key = '{seed_values["chain_key"]}'
              AND x.plan_node_slot_key = 'audit.ledger.validate' AND x.role = 'primary'
          );

        INSERT INTO topology.invocation_intents
          (tenant_id, chain_id, plan_node_slot_key, role, capability, intent_json, policy_decision, approval_ref, status, idempotency_key, trace_id)
        SELECT t.id, c.id, 'quant.research-note.draft', 'primary', 'quant.research-note.draft',
               '{seed_values["intent_research"]}'::jsonb, 'allowed', NULL, 'policy_allowed', '{seed_values["ikey_research"]}', '{seed_values["trace"]}'
        FROM iam.tenants t
          JOIN topology.invocation_chains c ON c.tenant_id = t.id AND c.chain_key = '{seed_values["chain_key"]}'
        WHERE t.slug = 'local-dev'
          AND NOT EXISTS (
            SELECT 1 FROM topology.invocation_intents x
            JOIN topology.invocation_chains c2 ON c2.id = x.chain_id
            WHERE x.tenant_id = t.id AND c2.chain_key = '{seed_values["chain_key"]}'
              AND x.plan_node_slot_key = 'quant.research-note.draft' AND x.role = 'primary'
          );

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
              'risk_classes',jsonb_build_array('low')))
        )
        FROM iam.tenants WHERE slug='local-dev'
        ON CONFLICT (tenant_id,name,version) DO UPDATE
          SET status=EXCLUDED.status,rules=EXCLUDED.rules;
        """
    )


def downgrade() -> None:
    raise RuntimeError("Topology releases and recycle history are retained; archive explicitly.")