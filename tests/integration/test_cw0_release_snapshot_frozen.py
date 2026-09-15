"""CW0-C contract test: materialization must read the frozen release snapshot,
not the live catalog, so later catalog edits never change how an old release
is interpreted.

Acceptance (方案 第13节 验收矩阵 CW0 出口4「目录修改不改变旧发布解释」):
  - publish release R freezing blueprint contracts;
  - materialize chain A under R (baseline);
  - edit a blueprint's contract in the live catalog;
  - materialize chain B under the *same* R: chain B's input_bindings must
    still come from R's frozen snapshot, not from the edited live catalog.

Current defect: service._load_plan re-reads topology.plugin_blueprints even
when routing_plans.topology_release_id is set, so a later catalog edit changes
the interpretation of an already-published release.
"""

from __future__ import annotations

import hashlib
from uuid import uuid4

import psycopg2

from packages.plugin_topology.service import TopologyService
from packages.policy.engine import PolicyEngine

TEST_DB = "postgresql://audit_app:admin@localhost:5432/audit_network_test"


def _service(tenant_slug: str) -> TopologyService:
    return TopologyService(
        TEST_DB,
        tenant_slug=tenant_slug,
        policy=PolicyEngine(allow=[
            "topology.cluster.write", "topology.blueprint.write", "topology.membership.write",
            "topology.edge.write", "topology.contract.write", "topology.release.publish",
            "topology.plan.read", "topology.chain.write",
        ]),
    )


def _create_tenant(slug: str) -> None:
    with psycopg2.connect(TEST_DB) as connection, connection.cursor() as cur:
        cur.execute(
            "INSERT INTO iam.tenants(slug,name) VALUES(%s,%s) ON CONFLICT(slug) DO NOTHING",
            (slug, "CW0C 隔离租户"),
        )


def _register_domain(service: TopologyService, marker: str, suffix: str) -> tuple[str, str]:
    """One producer->consumer domain; returns (producer_key, consumer_key)."""
    domain_key = f"cw0c-{marker}-{suffix}-domain"
    producer_key = f"cw0c-{marker}-{suffix}-producer"
    consumer_key = f"cw0c-{marker}-{suffix}-consumer"
    shared = f"cw0c-{marker}-{suffix}-shared-contract"

    service.upsert({
        "kind": "cluster",
        "idempotency_key": f"{marker}-cw0c-{suffix}-cluster",
        "payload": {
            "key": domain_key, "name": f"CW0C {suffix} 域-{marker}", "axis": "business_domain", "layer": "L0",
            "domains": ["audit"],
            "routing_budget": {"max_candidates": 8, "max_chain_length": 4, "max_latency_ms": 5000},
        },
    })
    for key, capability, contract in (
        (producer_key, f"audit.cw0c-{suffix}.produce", {
            "capability": f"audit.cw0c-{suffix}.produce", "version": "1.0.0", "outputs": [shared],
        }),
        (consumer_key, f"audit.cw0c-{suffix}.consume", {
            "capability": f"audit.cw0c-{suffix}.consume", "version": "1.0.0",
            "inputs": [shared], "outputs": [],
        }),
    ):
        service.upsert({
            "kind": "blueprint",
            "idempotency_key": f"{marker}-cw0c-{suffix}-bp-{key}",
            "payload": {
                "key": key, "name": f"槽位-{key}", "blueprint_type": "capability",
                "primary_cluster_key": domain_key, "lifecycle": "planned",
                "capability_contract": contract,
                "source_refs": [{"kind": "design_document", "uri": f"reference://cw0c-{suffix}"}],
            },
        })
        service.upsert({
            "kind": "membership",
            "idempotency_key": f"{marker}-cw0c-{suffix}-mb-{key}",
            "payload": {"blueprint_key": key, "cluster_key": domain_key, "axis": "business"},
        })
    service.upsert({
        "kind": "edge",
        "idempotency_key": f"{marker}-cw0c-{suffix}-edge",
        "payload": {
            "source_blueprint_key": producer_key, "target_blueprint_key": consumer_key,
            "relation_type": "depends_on",
        },
    })
    return producer_key, consumer_key


def _materialize_under_release(service: TopologyService, marker: str, suffix: str, release_lock: dict) -> str:
    plan = service.plan({
        "intent": f"CW0C {suffix} 链",
        "idempotency_key": f"{marker}-cw0c-{suffix}-plan",
        "mode": "plan_only",
        "capability_requirements": [f"audit.cw0c-{suffix}.produce", f"audit.cw0c-{suffix}.consume"],
        "budget": {"max_candidates": 8, "max_chain_length": 4, "max_latency_ms": 5000},
        "release_lock": release_lock,
    })
    chain = service.materialize_chain({
        "plan_key": plan["plan_key"],
        "idempotency_key": f"{marker}-cw0c-{suffix}-materialize",
        "reason": "CW0C 物化",
    })
    assert chain["release_locked"] is True
    return chain["chain_key"]


def _consumer_input_bindings(chain_key: str, consumer_capability: str, slug: str) -> list[str]:
    with psycopg2.connect(TEST_DB) as connection, connection.cursor() as cur:
        cur.execute("SELECT set_config('app.tenant_id', (SELECT id::text FROM iam.tenants WHERE slug=%s), false)", (slug,))
        cur.fetchone()
        cur.execute(
            """SELECT n.input_bindings
            FROM topology.invocation_chain_nodes n
            JOIN topology.invocation_chains c ON c.id=n.chain_id AND c.tenant_id=n.tenant_id
            WHERE c.chain_key=%s AND n.plan_node_slot_key=%s""",
            (chain_key, consumer_capability),
        )
        row = cur.fetchone()
        assert row is not None, f"consumer node not materialized for {chain_key}"
        return [str(item) for item in (row[0] or [])]


def test_release_snapshot_is_frozen_against_live_catalog_edits() -> None:
    marker = uuid4().hex[:8]
    slug = f"cw0c-{marker}-t"
    _create_tenant(slug)
    service = _service(slug)

    # two independent domains frozen in the same release
    a_producer, a_consumer = _register_domain(service, marker, "a")
    b_producer, b_consumer = _register_domain(service, marker, "b")

    release = service.release({
        "action": "publish",
        "idempotency_key": f"{marker}-cw0c-release",
        "version": f"0.1.0-{marker}",
        "catalog_checksum": hashlib.sha256(f"cw0c-{marker}".encode("utf-8")).hexdigest(),
        "blueprint_keys": [a_producer, a_consumer, b_producer, b_consumer],
        "cluster_keys": [f"cw0c-{marker}-a-domain", f"cw0c-{marker}-b-domain"],
        "created_by": str(uuid4()),
        "reason": "CW0C frozen release",
    })
    release_lock = {
        "release_id": release["release_id"],
        "version": release["version"],
        "checksum_sha256": release["catalog_checksum"],
    }

    # baseline: chain A under release R
    chain_a = _materialize_under_release(service, marker, "a", release_lock)
    assert _consumer_input_bindings(chain_a, "audit.cw0c-a.consume", slug) == [
        f"cw0c-{marker}-a-shared-contract"
    ]

    # live catalog edit: consumer B now declares an extra input port
    service.upsert({
        "kind": "blueprint",
        "idempotency_key": f"{marker}-cw0c-edit-b",
        "payload": {
            "key": b_consumer, "name": f"槽位-{b_consumer}", "blueprint_type": "capability",
            "primary_cluster_key": f"cw0c-{marker}-b-domain", "lifecycle": "planned",
            "capability_contract": {
                "capability": "audit.cw0c-b.consume", "version": "1.0.0",
                "inputs": [f"cw0c-{marker}-b-shared-contract", f"cw0c-{marker}-b-new-port"],
                "outputs": [],
            },
            "source_refs": [{"kind": "design_document", "uri": "reference://cw0c-edit"}],
        },
    })

    # chain B under the *same* release R must still interpret the frozen contract
    chain_b = _materialize_under_release(service, marker, "b", release_lock)
    assert _consumer_input_bindings(chain_b, "audit.cw0c-b.consume", slug) == [
        f"cw0c-{marker}-b-shared-contract"
    ]

    # baseline chain A is untouched by the edit
    assert _consumer_input_bindings(chain_a, "audit.cw0c-a.consume", slug) == [
        f"cw0c-{marker}-a-shared-contract"
    ]
