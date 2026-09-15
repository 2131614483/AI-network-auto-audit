"""Plugin Topology M1 integration tests against the isolated test database.

Covers the full path: register -> release -> plan -> policy gating ->
idempotency -> recycle/restore -> RLS cross-tenant isolation.  Nothing here
executes a plugin or touches host state; every write carries an idempotency key.
"""

from __future__ import annotations

import os
from uuid import UUID, uuid4

import psycopg2
import pytest

from packages.plugin_topology.service import TopologyService
from packages.policy.engine import PolicyEngine

DB = os.getenv("AUDIT_NETWORK_TEST_DATABASE_URL", "postgresql://audit_app:admin@localhost:5432/audit_network_test")


def _tenant(connection) -> UUID:
    with connection.cursor() as cur:
        cur.execute("SELECT id FROM iam.tenants WHERE slug='local-dev'")
        tenant_id = cur.fetchone()[0]
        cur.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant_id),))
        cur.fetchone()
    return tenant_id


def _allow_all() -> PolicyEngine:
    return PolicyEngine(
        allow=[
            "topology.cluster.write",
            "topology.blueprint.write",
            "topology.membership.write",
            "topology.edge.write",
            "topology.contract.write",
            "topology.bridge.write",
            "topology.release.publish",
            "topology.release.rollback",
            "topology.recycle.write",
            "topology.recycle.restore",
            "topology.plan.read",
        ]
    )


@pytest.fixture(scope="module")
def marker() -> str:
    return uuid4().hex[:8]


def _service() -> TopologyService:
    return TopologyService(DB, policy=_allow_all())


def _cluster(marker: str) -> dict:
    return {
        "kind": "cluster",
        "idempotency_key": f"{marker}-cluster",
        "payload": {
            "key": f"it-{marker}-domain",
            "name": f"集成测试域-{marker}",
            "axis": "business_domain",
            "layer": "L0",
            "domains": ["audit"],
            "routing_budget": {"max_candidates": 8, "max_chain_length": 4, "max_latency_ms": 5000},
            "allowed_bridge_kinds": ["artifact_ref", "capability_contract"],
        },
    }


def _blueprint(marker: str, key: str, capability: str, outputs: list[str]) -> dict:
    return {
        "kind": "blueprint",
        "idempotency_key": f"{marker}-blueprint-{key}",
        "payload": {
            "key": key,
            "name": f"槽位-{key}",
            "blueprint_type": "capability",
            "primary_cluster_key": f"it-{marker}-domain",
            "lifecycle": "planned",
            "capability_contract": {"capability": capability, "version": "1.0.0", "outputs": outputs},
            "source_refs": [{"kind": "design_document", "uri": f"reference://M1-{marker}"}],
        },
    }


def test_register_release_plan_end_to_end(marker: str) -> None:
    service = _service()
    suffix = marker
    domain_key = f"it-{suffix}-domain"
    source_key = f"it-{suffix}-producer"
    target_key = f"it-{suffix}-consumer"

    # register cluster + two blueprints + membership + edge + contract
    cluster = service.upsert(_cluster(suffix))
    assert cluster["kind"] == "cluster"
    assert cluster["idempotent"] is False
    replay = service.upsert(_cluster(suffix))
    assert replay["idempotent"] is True

    producer = service.upsert(
        _blueprint(suffix, source_key, f"audit.it-{suffix}.produce", ["it-contract-x"])
    )
    assert producer["idempotent"] is False
    consumer = service.upsert(
        _blueprint(suffix, target_key, f"audit.it-{suffix}.consume", ["it-contract-y"])
    )
    assert consumer["idempotent"] is False

    service.upsert(
        {
            "kind": "membership",
            "idempotency_key": f"{suffix}-membership",
            "payload": {"blueprint_key": source_key, "cluster_key": domain_key, "axis": "business"},
        }
    )
    service.upsert(
        {
            "kind": "membership",
            "idempotency_key": f"{suffix}-membership-2",
            "payload": {"blueprint_key": target_key, "cluster_key": domain_key, "axis": "business"},
        }
    )
    service.upsert(
        {
            "kind": "edge",
            "idempotency_key": f"{suffix}-edge",
            "payload": {"source_blueprint_key": source_key, "target_blueprint_key": target_key, "relation_type": "depends_on"},
        }
    )
    service.upsert(
        {
            "kind": "contract",
            "idempotency_key": f"{suffix}-contract",
            "payload": {
                "contract_id": f"it-{suffix}-contract",
                "contract_version": "1.0.0",
                "kind": "output",
                "format": "json",
                "classification": "internal",
                "schema_ref": f"contracts/jsonschema/it-{suffix}.schema.json",
            },
        }
    )

    # publish a frozen catalog (checksum protected)
    checksum = "a" * 64
    version = f"1.0.0-{suffix}"
    released = service.release(
        {
            "action": "publish",
            "idempotency_key": f"{suffix}-publish",
            "version": version,
            "catalog_checksum": checksum,
            "cluster_keys": [domain_key],
            "blueprint_keys": [source_key, target_key],
        }
    )
    assert released["action"] == "publish"
    assert released["catalog_checksum"] == checksum
    assert released["blueprint_count"] == 2
    # idempotent replay
    replay = service.release(
        {
            "action": "publish",
            "idempotency_key": f"{suffix}-publish",
            "version": version,
            "catalog_checksum": checksum,
            "cluster_keys": [domain_key],
            "blueprint_keys": [source_key, target_key],
        }
    )
    assert replay["idempotent"] is True
    assert replay["release_id"] == released["release_id"]

    # plan_only routing across the registered catalog
    plan = service.plan(
        {
            "intent": f"M1 集成测试意图-{suffix}",
            "idempotency_key": f"{suffix}-plan",
            "mode": "plan_only",
            "capability_requirements": [f"audit.it-{suffix}.produce", f"audit.it-{suffix}.consume"],
            "budget": {"max_candidates": 8, "max_chain_length": 4, "max_latency_ms": 5000},
        }
    )
    assert plan["mode"] == "plan_only"
    assert plan["node_count"] == 2
    assert plan["edge_count"] == 1
    assert len(plan["checksum"]) == 64
    assert plan["release_locked"] is False

    # plan is persisted and reproducible
    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        tenant_id = _tenant(connection)
        cur.execute(
            "SELECT mode,checksum FROM topology.routing_plans WHERE tenant_id=%s AND plan_key=%s",
            (tenant_id, plan["plan_key"]),
        )
        row = cur.fetchone()
        assert row is not None
        assert row[0] == "plan_only"
        assert row[1] == plan["checksum"]

    # read-only queries (large limit: the test database accumulates rows across
    # regression runs, and list_blueprints orders by key with a default cap)
    assert any(item["key"] == source_key for item in service.list_blueprints(limit=5000))
    assert any(item["release_key"] == f"catalog-1-0-0-{suffix}" for item in service.list_releases(limit=5000))


def test_plan_only_rejects_execute_and_unknown_requirements(marker: str) -> None:
    service = _service()
    with pytest.raises(ValueError, match="plan_only"):
        service.plan(
            {
                "intent": "execute path must be rejected",
                "idempotency_key": f"{marker}-bad-plan",
                "mode": "execute",
                "capability_requirements": ["audit.ledger.validate"],
                "budget": {"max_candidates": 8, "max_chain_length": 4, "max_latency_ms": 5000},
            }
        )
    with pytest.raises(ValueError, match="no planned blueprint"):
        service.plan(
            {
                "intent": "unknown capability",
                "idempotency_key": f"{marker}-bad-plan-2",
                "mode": "plan_only",
                "capability_requirements": [f"audit.it-{marker}.nonexistent"],
                "budget": {"max_candidates": 8, "max_chain_length": 4, "max_latency_ms": 5000},
            }
        )


def test_blueprint_execution_fields_rejected_and_seed_keys_reserved(marker: str) -> None:
    service = _service()
    with pytest.raises(ValueError, match="invalid topology upsert"):
        service.upsert(
            {
                "kind": "blueprint",
                "idempotency_key": f"{marker}-exec-fields",
                "payload": {
                    "key": f"it-{marker}-forbidden",
                    "name": "带执行字段",
                    "blueprint_type": "capability",
                    "lifecycle": "planned",
                    "capability_contract": {"capability": "audit.x.y", "version": "1.0.0"},
                    "source_refs": [{"kind": "design_document", "uri": "reference://X"}],
                    "runtime": "isolated-process",
                },
            }
        )
    with pytest.raises(ValueError, match="reserved"):
        service.upsert(
            {
                "kind": "cluster",
                "idempotency_key": f"{marker}-seed-cluster",
                "payload": {
                    "key": "business-financial-audit",
                    "name": "不得覆盖种子集群",
                    "axis": "business_domain",
                    "layer": "L0",
                    "domains": ["audit"],
                    "routing_budget": {"max_candidates": 8, "max_chain_length": 4, "max_latency_ms": 5000},
                },
            }
        )


def test_policy_gate_denies_without_allow(marker: str) -> None:
    denied = TopologyService(DB)  # PolicyEngine() has no allow rules
    with pytest.raises(PermissionError, match="policy denied"):
        denied.upsert(_cluster(marker))


def test_recycle_and_restore_preserve_evidence(marker: str) -> None:
    service = _service()
    target_key = f"it-{marker}-recyclable"
    service.upsert(
        _blueprint(marker, target_key, f"audit.it-{marker}.recycle", ["it-recyclable-out"])
    )
    recycled = service.recycle(
        {
            "recycle_type": "recycled",
            "entity_kind": "blueprint",
            "entity_key": target_key,
            "idempotency_key": f"{marker}-recycle",
            "reason": "M1 集成测试回收",
        }
    )
    assert recycled["recycle_type"] == "recycled"
    assert recycled["snapshot_sha256"]

    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        tenant_id = _tenant(connection)
        cur.execute(
            "SELECT status FROM topology.plugin_blueprints WHERE tenant_id=%s AND key=%s",
            (tenant_id, target_key),
        )
        assert cur.fetchone()[0] == "archived"
        cur.execute(
            "SELECT count(*) FROM topology.topology_recycle_bin WHERE tenant_id=%s AND entity_kind='blueprint'",
            (tenant_id,),
        )
        assert cur.fetchone()[0] >= 1

    restored = service.recycle(
        {
            "recycle_type": "restored",
            "entity_kind": "blueprint",
            "entity_key": target_key,
            "idempotency_key": f"{marker}-restore",
            "restored_from_recycle_id": recycled["recycle_id"],
            "reason": "M1 集成测试恢复",
        }
    )
    assert restored["recycle_type"] == "restored"
    assert restored["restored_from_recycle_id"] == recycled["recycle_id"]

    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        tenant_id = _tenant(connection)
        cur.execute(
            "SELECT status FROM topology.plugin_blueprints WHERE tenant_id=%s AND key=%s",
            (tenant_id, target_key),
        )
        assert cur.fetchone()[0] == "planned"

    with pytest.raises(ValueError, match="recycled record not found"):
        service.recycle(
            {
                "recycle_type": "restored",
                "entity_kind": "blueprint",
                "entity_key": target_key,
                "idempotency_key": f"{marker}-restore-bad",
                "restored_from_recycle_id": str(uuid4()),
                "reason": "不存在的回收记录",
            }
        )


def test_rls_hides_other_tenant_topology(marker: str) -> None:
    """A second tenant must never see the local-dev topology rows."""
    service = _service()
    service.upsert(_cluster(marker))

    with psycopg2.connect(DB) as connection:
        with connection.cursor() as cur:
            cur.execute(
                "INSERT INTO iam.tenants(slug,name) VALUES(%s,%s) ON CONFLICT(slug) DO NOTHING",
                (f"it-{marker}-other", f"其他租户-{marker}"),
            )
            cur.execute("SELECT id FROM iam.tenants WHERE slug=%s", (f"it-{marker}-other",))
            other_id = cur.fetchone()[0]
            cur.execute("SELECT set_config('app.tenant_id', %s, false)", (str(other_id),))
            cur.execute(
                "SELECT count(*) FROM topology.plugin_clusters WHERE key=%s",
                (f"it-{marker}-domain",),
            )
            assert cur.fetchone()[0] == 0
            cur.execute(
                "SELECT count(*) FROM topology.plugin_blueprints WHERE key LIKE %s",
                (f"it-{marker}-%",),
            )
            assert cur.fetchone()[0] == 0


def test_publish_rejects_bad_checksum_and_missing_blueprint(marker: str) -> None:
    service = _service()
    with pytest.raises(ValueError, match="not-a-sha256"):
        service.release(
            {
                "action": "publish",
                "idempotency_key": f"{marker}-bad-checksum",
                "version": "1.0.0",
                "catalog_checksum": "not-a-sha256",
                "cluster_keys": [f"it-{marker}-domain"],
                "blueprint_keys": [f"it-{marker}-producer"],
            }
        )
    with pytest.raises(ValueError, match="planned blueprint not found"):
        service.release(
            {
                "action": "publish",
                "idempotency_key": f"{marker}-missing-bp",
                "version": "1.0.0",
                "catalog_checksum": "b" * 64,
                "cluster_keys": [f"it-{marker}-domain"],
                "blueprint_keys": [f"it-{marker}-ghost"],
            }
        )


# -- M2: cross-domain bridges ------------------------------------------------


def test_bridge_upsert_is_idempotent_and_rejects_private_payloads(marker: str) -> None:
    service = _service()
    producer_key = f"it-{marker}-bridge-producer"
    service.upsert(_blueprint(marker, producer_key, f"audit.it-{marker}.bridge-produce", ["bridge-out-x"]))

    registered = service.upsert(
        {
            "kind": "bridge",
            "idempotency_key": f"{marker}-bridge-1",
            "payload": {
                "blueprint_key": producer_key,
                "ref_kind": "capability_contract",
                "bridge_ref": "contracts/jsonschema/audit-quality-candidates@1",
            },
        }
    )
    assert registered["kind"] == "bridge"
    assert registered["entity_key"] == f"{producer_key}:capability_contract"
    assert registered["idempotent"] is False

    replay = service.upsert(
        {
            "kind": "bridge",
            "idempotency_key": f"{marker}-bridge-1",
            "payload": {
                "blueprint_key": producer_key,
                "ref_kind": "capability_contract",
                "bridge_ref": "contracts/jsonschema/audit-quality-candidates@1",
            },
        }
    )
    assert replay["idempotent"] is True
    assert replay["entity_id"] == registered["entity_id"]

    # a private payload never reaches the database (schema + service double gate)
    with pytest.raises(ValueError, match="invalid topology upsert"):
        service.upsert(
            {
                "kind": "bridge",
                "idempotency_key": f"{marker}-bridge-private",
                "payload": {
                    "blueprint_key": producer_key,
                    "ref_kind": "domain_private_table",
                    "bridge_ref": "schema://domain.private",
                },
            }
        )
    # prefix consistency is enforced for public ref kinds
    with pytest.raises(ValueError, match="does not match ref_kind"):
        service.upsert(
            {
                "kind": "bridge",
                "idempotency_key": f"{marker}-bridge-prefix",
                "payload": {
                    "blueprint_key": producer_key,
                    "ref_kind": "artifact_ref",
                    "bridge_ref": "file:///etc/domain/private",
                },
            }
        )


def test_bridge_edge_requires_registration_and_plan_keeps_bridge_edge(marker: str) -> None:
    service = _service()
    domain_key = f"it-{marker}-bridge-domain"
    producer_key = f"it-{marker}-bridge-src"
    target_key = f"it-{marker}-bridge-dst"
    service.upsert(
        {
            "kind": "cluster",
            "idempotency_key": f"{marker}-bridge-cluster",
            "payload": {
                "key": domain_key, "name": "M2 桥接域", "axis": "business_domain", "layer": "L0",
                "domains": ["audit"],
                "routing_budget": {"max_candidates": 8, "max_chain_length": 4, "max_latency_ms": 5000},
                "allowed_bridge_kinds": ["artifact_ref", "capability_contract"],
            },
        }
    )
    service.upsert(_blueprint(marker, producer_key, f"audit.it-{marker}.src", ["bridge-src-out"]))
    service.upsert(_blueprint(marker, target_key, f"audit.it-{marker}.dst", ["bridge-dst-out"]))
    for key in (producer_key, target_key):
        service.upsert(
            {
                "kind": "membership",
                "idempotency_key": f"{marker}-bridge-membership-{key}",
                "payload": {"blueprint_key": key, "cluster_key": domain_key, "axis": "business"},
            }
        )

    # bridge edge without a registered public bridge is refused
    with pytest.raises(ValueError, match="requires a registered public bridge"):
        service.upsert(
            {
                "kind": "edge",
                "idempotency_key": f"{marker}-bridge-edge-bad",
                "payload": {
                    "source_blueprint_key": producer_key,
                    "target_blueprint_key": target_key,
                    "relation_type": "bridge",
                },
            }
        )

    # register the public bridge, then the bridge edge becomes valid
    service.upsert(
        {
            "kind": "bridge",
            "idempotency_key": f"{marker}-bridge-edge-reg",
            "payload": {
                "blueprint_key": producer_key,
                "ref_kind": "capability_contract",
                "bridge_ref": "contracts/jsonschema/audit-quality-candidates@1",
            },
        }
    )
    edge = service.upsert(
        {
            "kind": "edge",
            "idempotency_key": f"{marker}-bridge-edge-ok",
            "payload": {
                "source_blueprint_key": producer_key,
                "target_blueprint_key": target_key,
                "relation_type": "bridge",
            },
        }
    )
    assert edge["kind"] == "edge"
    assert edge["entity_key"] == f"{producer_key}->{target_key}"

    # plan_only keeps the bridge edge in the DAG and persists it
    plan = service.plan(
        {
            "intent": "M2 桥接链规划-不执行",
            "idempotency_key": f"{marker}-bridge-plan",
            "mode": "plan_only",
            "capability_requirements": [f"audit.it-{marker}.src", f"audit.it-{marker}.dst"],
            "budget": {"max_candidates": 8, "max_chain_length": 4, "max_latency_ms": 5000},
        }
    )
    assert plan["mode"] == "plan_only"
    assert plan["node_count"] == 2
    assert plan["edge_count"] == 1
    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        tenant_id = _tenant(connection)
        cur.execute(
            """SELECT pe.relation_type FROM topology.routing_plan_edges pe
            JOIN topology.routing_plans p ON p.id=pe.plan_id
            WHERE p.tenant_id=%s AND p.plan_key=%s""",
            (tenant_id, plan["plan_key"]),
        )
        rows = cur.fetchall()
    assert [row[0] for row in rows] == ["bridge"]


def test_seeded_bridge_catalog_plans_with_bridge_edge() -> None:
    """The migration seeds expose a plan that crosses domains via a bridge edge."""
    service = _service()
    plan = service.plan(
        {
            "intent": "M2 种子桥接链-不执行",
            "idempotency_key": "seed-bridge-plan",
            "mode": "plan_only",
            "capability_requirements": ["audit.ledger.validate", "quant.research-note.draft"],
            "budget": {"max_candidates": 8, "max_chain_length": 4, "max_latency_ms": 5000},
        }
    )
    assert plan["mode"] == "plan_only"
    # Idempotency contract: re-planning the same capability set (deterministic
    # plan_key) returns the already-persisted plan instead of a UniqueViolation,
    # so the suite is order-independent and replays are safe for 24x7 runners.
    replay = service.plan(
        {
            "intent": "M2 种子桥接链-重复重放",
            "idempotency_key": "seed-bridge-plan-replay",
            "mode": "plan_only",
            "capability_requirements": ["audit.ledger.validate", "quant.research-note.draft"],
            "budget": {"max_candidates": 8, "max_chain_length": 4, "max_latency_ms": 5000},
        }
    )
    assert replay["idempotent"] is True
    assert replay["plan_id"] == plan["plan_id"]
    assert replay["node_count"] == plan["node_count"]
    assert replay["edge_count"] == plan["edge_count"]
    assert plan["node_count"] == 2
    assert plan["edge_count"] == 1
    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        tenant_id = _tenant(connection)
        cur.execute(
            """SELECT pe.relation_type FROM topology.routing_plan_edges pe
            JOIN topology.routing_plans p ON p.id=pe.plan_id
            WHERE p.tenant_id=%s AND p.plan_key=%s""",
            (tenant_id, plan["plan_key"]),
        )
        rows = cur.fetchall()
    assert [row[0] for row in rows] == ["bridge"]
    # Assert presence explicitly rather than through the default page: the
    # catalog is shared with every other topology suite, whose ``it-*`` fixtures
    # are never removed (784 of 787 blueprints, 100 of 101 bridges in the test
    # database).  ``list_bridges()`` orders by blueprint key and truncates at
    # 100, so the seeded ``ledger-quality-slot`` was being pushed off the end by
    # alphabetically-earlier junk and the assertion failed for a reason that had
    # nothing to do with bridging.
    bridges = service.list_bridges(limit=2000)
    assert any(
        item["blueprint_key"] == "ledger-quality-slot" and item["ref_kind"] == "capability_contract"
        for item in bridges
    )


def test_bridge_write_is_policy_gated(marker: str) -> None:
    denied = TopologyService(DB)  # no allow rules at all
    with pytest.raises(PermissionError, match="policy denied topology.bridge.write"):
        denied.upsert(
            {
                "kind": "bridge",
                "idempotency_key": f"{marker}-bridge-denied",
                "payload": {
                    "blueprint_key": "ledger-quality-slot",
                    "ref_kind": "capability_contract",
                    "bridge_ref": "contracts/jsonschema/audit-quality-candidates@1",
                },
            }
        )
