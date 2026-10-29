"""Local SQL evidence sources: tenant boundaries, provenance and both run ledgers."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from uuid import uuid4

import psycopg2
import pytest
from psycopg2.extras import Json

from packages.ai_planner.evidence import PlanningContext, PlanningInput, SourceQuery, fingerprint
from packages.ai_planner.source_adapters import SqlEvidenceSources

TEST_DB = os.getenv("AUDIT_NETWORK_TEST_DATABASE_URL") or (
    "postgresql://audit_app:admin@localhost:5432/audit_network_test"
)
MIGRATOR_DB = os.getenv("AUDIT_NETWORK_MIGRATOR_DATABASE_URL") or (
    "postgresql://audit_migrator:admin@localhost:5432/audit_network_test"
)


def _context(tenant_id: str) -> PlanningContext:
    return PlanningContext(tenant_id=tenant_id, trace_id=uuid4(), session_id=uuid4())


def _query(kind: str, text: str, **kwargs) -> SourceQuery:
    return SourceQuery(source_kind=kind, query=text, purpose="source integration test", **kwargs)


def _seed_corpus() -> dict:
    prefix = f"collab-source-{uuid4().hex}"
    tenants = [str(uuid4()), str(uuid4())]
    capability = f"audit.fixture.{uuid4().hex}"
    ids: dict = {"prefix": prefix, "tenants": tenants, "capability": capability}
    with psycopg2.connect(MIGRATOR_DB) as conn, conn.cursor() as cur:
        for index, tenant in enumerate(tenants):
            cur.execute(
                "INSERT INTO iam.tenants(id,slug,name) VALUES(%s,%s,%s)",
                (tenant, f"{prefix}-{index}", "Synthetic collaborative source tenant"),
            )
            cur.execute("SELECT set_config('app.tenant_id',%s,true)", (tenant,))
            space = str(uuid4())
            cur.execute(
                "INSERT INTO graph.spaces(id,tenant_id,key,level,name) VALUES(%s,%s,%s,'L2',%s)",
                (space, tenant, f"{prefix}-space", prefix),
            )
            node_ids = [str(uuid4()), str(uuid4()), str(uuid4())]
            for n, node_id in enumerate(node_ids):
                cur.execute(
                    """INSERT INTO graph.nodes
                    (id,tenant_id,space_id,canonical_key,node_type,label,properties,row_version,valid_to)
                    VALUES(%s,%s,%s,%s,'capability',%s,%s,7,
                           CASE WHEN %s THEN now()-interval '1 day' ELSE NULL END)""",
                    (node_id, tenant, space, f"capability:{capability}-{n}", f"{prefix} plugin {n}",
                     Json({"capability": capability if n == 0 else f"{capability}.{n}",
                           "plugin_id": f"fixture.plugin-{n}", "description": f"{prefix} {index}"}), n == 2),
                )
            edge_id = str(uuid4())
            cur.execute(
                """INSERT INTO graph.edges
                (id,tenant_id,space_id,source_node_id,target_node_id,relation_type,properties)
                VALUES(%s,%s,%s,%s,%s,'depends_on',%s)""",
                (edge_id, tenant, space, node_ids[0], node_ids[1], Json({"basis": "synthetic declaration"})),
            )
            # An expired relation must not reappear as a current compatibility fact.
            cur.execute(
                """INSERT INTO graph.edges
                (tenant_id,space_id,source_node_id,target_node_id,relation_type,valid_to)
                VALUES(%s,%s,%s,%s,'fallback',now()-interval '1 day')""",
                (tenant, space, node_ids[0], node_ids[1]),
            )
            document = str(uuid4())
            chunk = str(uuid4())
            current = f"{prefix} current source {index} " + "文" * 8200
            content_sha = hashlib.sha256(current.encode()).hexdigest()
            cur.execute(
                """INSERT INTO semantic.documents
                (id,tenant_id,source_uri,title,current_version,content_sha256)
                VALUES(%s,%s,%s,%s,2,%s)""",
                (document, tenant, f"file:///synthetic/{prefix}-{index}.md", prefix, content_sha),
            )
            cur.execute(
                """INSERT INTO semantic.chunks(id,tenant_id,document_id,document_version,ordinal,content,metadata)
                VALUES(%s,%s,%s,2,0,%s,%s)""",
                (chunk, tenant, document, current, Json({"page": 3})),
            )
            cur.execute(
                """INSERT INTO semantic.chunks(tenant_id,document_id,document_version,ordinal,content)
                VALUES(%s,%s,1,0,%s)""", (tenant, document, f"{prefix} outdated chunk"),
            )
            retired = str(uuid4())
            cur.execute(
                """INSERT INTO semantic.documents(id,tenant_id,source_uri,title,status)
                VALUES(%s,%s,%s,%s,'retired')""",
                (retired, tenant, f"file:///synthetic/{prefix}-retired-{index}.md", prefix),
            )
            cur.execute(
                "INSERT INTO semantic.chunks(tenant_id,document_id,ordinal,content) VALUES(%s,%s,0,%s)",
                (tenant, retired, f"{prefix} retired content"),
            )
            cw3_runs = []
            for outcome in ("succeeded", "failed"):
                run_id = str(uuid4())
                cw3_runs.append(run_id)
                cur.execute(
                    """INSERT INTO control.node_attempts
                    (attempt_id,tenant_id,run_id,plan_key,execution_hash,node_instance_id,capability,plugin_id,
                     attempt_seq,status,trace_id,finished_at,plugin_version,runtime_code_sha256,
                     error_kind,error_message,input_bindings,output_refs)
                    VALUES(gen_random_uuid(),%s,%s,%s,%s,'fixture-node',%s,'fixture.plugin',1,%s,%s,now(),'1.2.3',%s,
                     %s,%s,%s,%s)""",
                    (tenant, run_id, prefix, "c" * 64, capability, outcome, str(uuid4()), "d" * 64,
                     "fixture_failure" if outcome == "failed" else None,
                     "Synthetic failure retained for planning" if outcome == "failed" else None,
                     Json({"raw": {"sha256": "a" * 64, "source_instance": "seed"}}),
                     Json({"out": {"sha256": "b" * 64}}) if outcome == "succeeded" else Json({})),
                )
            release, plan, chain, m6_run = (str(uuid4()) for _ in range(4))
            cur.execute(
                """INSERT INTO topology.topology_releases
                (id,tenant_id,release_key,version,catalog_checksum,blueprint_snapshots,edge_snapshots,status)
                VALUES(%s,%s,%s,'1.0.0',%s,'[]','[]','published')""",
                (release, tenant, prefix, "e" * 64),
            )
            cur.execute(
                """INSERT INTO topology.routing_plans
                (id,tenant_id,plan_key,mission_key,plan_json,topology_release_id,planner_version,checksum,trace_id)
                VALUES(%s,%s,%s,%s,'{}',%s,'1.0.0',%s,%s)""",
                (plan, tenant, prefix, prefix, release, "f" * 64, str(uuid4())),
            )
            cur.execute(
                """INSERT INTO topology.invocation_chains
                (id,tenant_id,chain_key,plan_id,chain_json,chain_checksum,planner_version,trace_id)
                VALUES(%s,%s,%s,%s,'{}',%s,'1.0.0',%s)""",
                (chain, tenant, prefix, plan, "1" * 64, str(uuid4())),
            )
            cur.execute(
                """INSERT INTO topology.invocation_intents
                (tenant_id,chain_id,plan_node_slot_key,capability,intent_json,policy_decision,
                 status,idempotency_key,trace_id)
                VALUES(%s,%s,'fixture-slot',%s,'{}','allowed','policy_allowed',%s,%s)""",
                (tenant, chain, capability, prefix, str(uuid4())),
            )
            cur.execute(
                """INSERT INTO topology.execution_runs
                (id,tenant_id,chain_id,chain_key,status,reason,idempotency_key,trace_id,
                 node_total,node_failed,chain_checksum,planner_version,finished_at)
                VALUES(%s,%s,%s,%s,'failed',%s,%s,%s,1,1,%s,'1.0.0',now())""",
                (m6_run, tenant, chain, prefix, prefix, prefix, str(uuid4()), "1" * 64),
            )
            cur.execute(
                """INSERT INTO topology.execution_ledger
                (tenant_id,chain_id,plan_node_slot_key,ordinal,mode,output_checksum,status,
                 idempotency_key,trace_id,run_id,plugin_id,plugin_version,runtime_code_sha256,finished_at)
                VALUES(%s,%s,'fixture-slot',0,'isolated',%s,'failed',%s,%s,%s,
                       'fixture.plugin','1.2.3',%s,now())""",
                (tenant, chain, "2" * 64, prefix, str(uuid4()), m6_run, "3" * 64),
            )
            ids[tenant] = {"nodes": node_ids, "edge": edge_id, "document": document,
                           "chunk": chunk, "content_sha": content_sha, "cw3_runs": cw3_runs,
                           "m6_run": m6_run}
            # Deferred graph constraints must run while this tenant's RLS is set.
            conn.commit()
    return ids


@pytest.fixture(scope="module")
def corpus() -> dict:
    return _seed_corpus()


@pytest.fixture()
def mutable_corpus() -> dict:
    return _seed_corpus()


def test_graph_evidence_has_current_relationships_and_immutable_snapshot(corpus: dict) -> None:
    tenant = corpus["tenants"][0]
    result = SqlEvidenceSources(TEST_DB).query(
        _context(tenant), _query("graph", corpus["prefix"], capabilities=[corpus["capability"]]),
    )
    assert result.status == "available", result
    assert result.items
    for item in result.items:
        assert str(item.tenant_id) == tenant
        assert item.source_kind == "graph"
        assert item.sha256 == fingerprint(item.facts)
        assert item.origin_refs and item.version
    text = str([item.model_dump() for item in result.items])
    assert corpus[tenant]["edge"] in text
    assert "depends_on" in text
    assert corpus[tenant]["nodes"][2] not in text
    assert "fallback" not in text
    assert corpus[corpus["tenants"][1]]["edge"] not in text


def test_history_covers_both_ledgers_and_keeps_failed_examples(corpus: dict) -> None:
    tenant = corpus["tenants"][0]
    result = SqlEvidenceSources(TEST_DB).query(
        _context(tenant), _query("history", corpus["prefix"], capabilities=[corpus["capability"]], limit=8),
    )
    assert result.status == "available", result
    refs = [item.source_ref for item in result.items]
    assert any("execution_runs" in ref for ref in refs)
    assert any("node_attempts" in ref for ref in refs)
    assert any(item.facts["status"] == "failed" for item in result.items)
    for item in result.items:
        assert item.sha256 == fingerprint(item.facts)
        assert str(item.tenant_id) == tenant
        assert corpus["capability"] in item.facts["capabilities"]
    serialized = str([item.model_dump() for item in result.items])
    assert corpus[tenant]["m6_run"] in serialized
    assert all(run in serialized for run in corpus[tenant]["cw3_runs"])
    assert corpus[corpus["tenants"][1]]["m6_run"] not in serialized


def test_history_filters_capabilities_and_enforces_top_k(corpus: dict) -> None:
    context = _context(corpus["tenants"][0])
    sources = SqlEvidenceSources(TEST_DB)
    limited = sources.query(context, _query("history", corpus["prefix"], limit=1))
    assert len(limited.items) == 1
    missing = sources.query(
        context, _query("history", corpus["prefix"], capabilities=["fixture.no_such_capability"]),
    )
    assert missing.status == "empty"
    assert missing.items == []


def test_knowledge_is_keyword_only_current_and_source_grounded(
    corpus: dict, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from packages.knowledge import retrieval

    def forbidden_embedder():
        raise AssertionError("keyword evidence must never construct an embedder")

    monkeypatch.setattr(retrieval, "default_embedder", forbidden_embedder)
    tenant = corpus["tenants"][0]
    result = SqlEvidenceSources(TEST_DB).query(_context(tenant), _query("knowledge", corpus["prefix"]))
    assert result.status == "available", result
    assert len(result.items) == 1
    item = result.items[0]
    assert item.version == "2"
    assert len(item.content) == 8000
    assert corpus[tenant]["document"] in item.source_ref
    assert corpus[tenant]["chunk"] in str(item.origin_refs)
    assert item.facts["source_sha256"] == corpus[tenant]["content_sha"]
    assert item.sha256 == fingerprint(item.facts)
    assert item.facts["retrieval_mode"] == "keyword"
    assert "outdated" not in item.content and "retired" not in item.content


def test_unknown_tenant_cannot_read_any_source() -> None:
    context = _context(str(uuid4()))
    for kind in ("graph", "history", "knowledge"):
        result = SqlEvidenceSources(TEST_DB).query(context, _query(kind, "audit"))
        assert result.status == "error"
        assert result.items == []


def _register_artifact(tenant: str, uri: str, raw: bytes) -> str:
    artifact_id = str(uuid4())
    sha = hashlib.sha256(raw).hexdigest()
    with psycopg2.connect(MIGRATOR_DB) as conn, conn.cursor() as cur:
        cur.execute("SELECT set_config('app.tenant_id',%s,true)", (tenant,))
        cur.execute(
            """INSERT INTO artifact.blobs(sha256,byte_size,media_type,storage_uri)
            VALUES(%s,%s,'application/json',%s) ON CONFLICT(sha256) DO NOTHING""", (sha, len(raw), uri),
        )
        cur.execute(
            """INSERT INTO artifact.artifacts(id,tenant_id,blob_sha256,artifact_type,classification)
            VALUES(%s,%s,%s,'test-source','internal')""", (artifact_id, tenant, sha),
        )
    return artifact_id


def test_registered_input_is_verified_by_full_hash_and_tenant(corpus: dict, tmp_path: Path) -> None:
    tenant = corpus["tenants"][0]
    raw = f'{{"synthetic":"{uuid4().hex}"}}'.encode()
    source = tmp_path / "input.json"
    source.write_bytes(raw)
    artifact = _register_artifact(tenant, source.as_uri(), raw)
    input_ref = PlanningInput(port_id="raw", schema_ref="artifact-ref", artifact_id=artifact)
    sources = SqlEvidenceSources(TEST_DB, allowed_roots=(tmp_path,))
    [resolved] = sources.resolve_inputs(_context(tenant), [input_ref])
    assert resolved.available is True
    assert resolved.sha256 == hashlib.sha256(raw).hexdigest()
    [other] = sources.resolve_inputs(_context(corpus["tenants"][1]), [input_ref])
    assert other.available is False
    source.write_bytes(raw + b"tampered")
    [tampered] = sources.resolve_inputs(_context(tenant), [input_ref])
    assert tampered.available is False


@pytest.mark.parametrize("mode", ["outside", "network", "missing"])
def test_input_resolution_rejects_unsafe_or_missing_registered_paths(
    corpus: dict, tmp_path: Path, mode: str,
) -> None:
    raw = f'{{"synthetic":"{uuid4().hex}"}}'.encode()
    source = tmp_path / "source.json"
    source.write_bytes(raw)
    uri = source.as_uri() if mode == "outside" else (
        "https://example.invalid/no-network" if mode == "network" else (tmp_path / "absent.json").as_uri()
    )
    tenant = corpus["tenants"][0]
    artifact = _register_artifact(tenant, uri, raw)
    sources = SqlEvidenceSources(TEST_DB, allowed_roots=(tmp_path / "trusted",))
    [resolved] = sources.resolve_inputs(_context(tenant), [
        PlanningInput(port_id="raw", schema_ref="artifact-ref", artifact_id=artifact),
    ])
    assert resolved.available is False
    assert resolved.detail


def test_sources_use_readonly_transactions_with_a_query_timeout(corpus: dict) -> None:
    sources = SqlEvidenceSources(TEST_DB)
    with sources._cursor(_context(corpus["tenants"][0])) as cur:
        cur.execute("SHOW transaction_read_only")
        assert cur.fetchone()[0] == "on"
        cur.execute("SHOW statement_timeout")
        assert cur.fetchone()[0] == "5s"


def test_evidence_revalidation_accepts_current_snapshots_and_rejects_tampering(corpus: dict) -> None:
    sources = SqlEvidenceSources(TEST_DB)
    context = _context(corpus["tenants"][0])
    items = [item for kind in ("graph", "history", "knowledge")
             for item in sources.query(context, _query(kind, corpus["prefix"])).items]
    assert items
    assert sources.validate_evidence(context, items) == []
    tampered = items[0].model_copy(update={"content": "invented replacement"})
    assert sources.validate_evidence(context, [tampered])
    unknown = items[0].model_copy(update={"source_ref": "https://example.invalid/not-a-database-ref"})
    assert sources.validate_evidence(context, [unknown])
    assert sources.validate_evidence(_context(corpus["tenants"][1]), items)


def test_evidence_revalidation_detects_source_version_and_validity_changes(mutable_corpus: dict) -> None:
    corpus = mutable_corpus
    tenant = corpus["tenants"][0]
    sources = SqlEvidenceSources(TEST_DB)
    context = _context(tenant)
    items = [item for kind in ("graph", "history", "knowledge")
             for item in sources.query(context, _query(kind, corpus["prefix"])).items]
    with psycopg2.connect(MIGRATOR_DB) as conn, conn.cursor() as cur:
        cur.execute("SELECT set_config('app.tenant_id',%s,true)", (tenant,))
        cur.execute("UPDATE graph.edges SET valid_to=now() WHERE id=%s", (corpus[tenant]["edge"],))
        cur.execute("UPDATE semantic.documents SET current_version=3 WHERE id=%s", (corpus[tenant]["document"],))
        cur.execute(
            "UPDATE control.node_attempts SET status='failed',error_kind='fixture_changed' WHERE run_id=%s",
            (corpus[tenant]["cw3_runs"][0],),
        )
        cur.execute(
            "UPDATE topology.execution_runs SET reason='changed synthetic run metadata' WHERE id=%s",
            (corpus[tenant]["m6_run"],),
        )
    issues = sources.validate_evidence(context, items)
    assert any(item.evidence_id in str(issues) for item in items if item.source_kind == "graph")
    assert all(item.evidence_id in str(issues) for item in items if item.source_kind == "knowledge")
    assert any(item.evidence_id in str(issues) for item in items if "execution_runs" in item.source_ref)
    assert any(item.evidence_id in str(issues) for item in items
               if corpus[tenant]["cw3_runs"][0] in item.source_ref)


def test_explicit_lineage_links_graph_knowledge_and_history_to_the_same_run(mutable_corpus: dict) -> None:
    corpus = mutable_corpus
    tenant = corpus["tenants"][0]
    cw3_run = corpus[tenant]["cw3_runs"][0]
    m6_run = corpus[tenant]["m6_run"]
    untrusted_url = "https://example.invalid/lineage-only-do-not-fetch"
    with psycopg2.connect(MIGRATOR_DB) as conn, conn.cursor() as cur:
        cur.execute("SELECT set_config('app.tenant_id',%s,true)", (tenant,))
        cur.execute(
            "UPDATE graph.nodes SET properties=properties||%s::jsonb WHERE id=%s",
            (Json({"evidence_run_ids": [cw3_run], "source_refs": [untrusted_url],
                   "derived_from": {"run_id": cw3_run}}), corpus[tenant]["nodes"][0]),
        )
        cur.execute(
            "UPDATE graph.edges SET properties=properties||%s::jsonb WHERE id=%s",
            (Json({"source_refs": [f"db://topology.execution_runs/{m6_run}"]}), corpus[tenant]["edge"]),
        )
        cur.execute(
            "UPDATE semantic.chunks SET metadata=metadata||%s::jsonb WHERE id=%s",
            (Json({"derived_from": {"source_refs": [f"db://control.node_attempts/run/{cw3_run}"],
                                    "run_id": m6_run}}), corpus[tenant]["chunk"]),
        )
    context = _context(tenant)
    sources = SqlEvidenceSources(TEST_DB)
    graph = sources.query(context, _query("graph", corpus["prefix"], capabilities=[corpus["capability"]]))
    knowledge = sources.query(context, _query("knowledge", corpus["prefix"]))
    history = sources.query(context, _query("history", corpus["prefix"], capabilities=[corpus["capability"]]))
    expected = {f"db://runs/{cw3_run}", f"db://runs/{m6_run}"}
    graph_origins = {ref for item in graph.items for ref in item.origin_refs}
    knowledge_origins = {ref for item in knowledge.items for ref in item.origin_refs}
    history_origins = {ref for item in history.items for ref in item.origin_refs}
    assert expected <= graph_origins & knowledge_origins & history_origins
    assert untrusted_url in graph_origins  # an untrusted lineage string, never a retrieval target
    for item in history.items:
        assert f"db://runs/{item.facts['run_id']}" in item.origin_refs
    assert sources.validate_evidence(context, [*graph.items, *knowledge.items, *history.items]) == []


def test_lineage_is_bounded_and_arbitrary_urls_do_not_become_run_identity(mutable_corpus: dict) -> None:
    corpus = mutable_corpus
    tenant = corpus["tenants"][0]
    run_id = corpus[tenant]["cw3_runs"][0]
    urls = [f"https://example.invalid/untrusted/{index}" for index in range(100)]
    with psycopg2.connect(MIGRATOR_DB) as conn, conn.cursor() as cur:
        cur.execute("SELECT set_config('app.tenant_id',%s,true)", (tenant,))
        cur.execute(
            "UPDATE graph.nodes SET properties=properties||%s::jsonb WHERE id=%s",
            (Json({"source_refs": urls, "evidence_run_ids": [run_id, *(str(uuid4()) for _ in range(70))]}),
             corpus[tenant]["nodes"][0]),
        )
    sources = SqlEvidenceSources(TEST_DB)
    context = _context(tenant)
    [item] = sources.query(context, _query("graph", corpus["prefix"], capabilities=[corpus["capability"]])).items
    assert len(item.origin_refs) <= 32
    assert f"db://runs/{run_id}" in item.origin_refs
    with psycopg2.connect(MIGRATOR_DB) as conn, conn.cursor() as cur:
        cur.execute("SELECT set_config('app.tenant_id',%s,true)", (tenant,))
        cur.execute(
            "UPDATE graph.nodes SET properties=properties-'evidence_run_ids'||%s::jsonb WHERE id=%s",
            (Json({"source_refs": [f"https://example.invalid/runs/{run_id}"]}), corpus[tenant]["nodes"][0]),
        )
    [item] = sources.query(context, _query("graph", corpus["prefix"], capabilities=[corpus["capability"]])).items
    assert f"db://runs/{run_id}" not in item.origin_refs
    assert f"https://example.invalid/runs/{run_id}" in item.origin_refs
