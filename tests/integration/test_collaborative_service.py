"""Real three-source retrieval and persistence with an injected offline model."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import uuid4

import psycopg2
import pytest
from psycopg2.extras import Json

from packages.ai_planner.composer import discover_plugins
from packages.ai_planner.evidence import PlanningContext, PlanningRequest, fingerprint
from packages.ai_planner.planning_service import CollaborativePlanningService
from packages.ai_planner.source_adapters import SqlEvidenceSources

DB = "postgresql://audit_app:admin@localhost:5432/audit_network_test"


class OfflineModel:
    def complete_json(self, messages, *, temperature=0.0):
        context = json.loads(messages[-1]["content"])["untrusted_context"]
        ids = [item["evidence_id"] for item in context["evidence"]]
        if "audit brain role" in messages[0]["content"]:
            return {"goal_hash": context["goal_hash"], "knowledge_hash": context["knowledge_hash"],
                "objective": "验证样本量规划", "scope": "合成资料 plan_only", "risk_hypotheses": ["缺适用依据或输入"],
                "procedures": [{"procedure_id": "sample-program", "requirement_keys": [item["key"] for item in context["request"]["requirements"]],
                    "objective": "形成样本量计算方案", "method": "依据知识方法规划并核验适用条件", "required_evidence": ["登记输入及适用条件"],
                    "acceptance_criteria": ["输入可用、目标产物覆盖、适用条件有正文依据"], "knowledge_evidence_ids": ids}]}
        if "planning role" in messages[0]["content"]:
            return {
                "nodes": [{"node_id": "compute", "plugin_id": "audit.plan.sample-size-compute"}],
                "edges": [], "evidence_ids": ids, "decision_evidence": {"compute": ids},
                "rationale": "补充依据后修订" if context["round_no"] > 1 else "初稿",
                "procedure_nodes": {"sample-program": ["compute"]},
            }
        sufficient = context["round_no"] > 1 and any("followup-conditions-qz" in item["content"] for item in context["evidence"])
        return {
            "goal_hash": context["goal_hash"], "candidate_hash": context["candidate_hash"],
            "evidence_hash": context["evidence_hash"],
            "requirements": [{"key": "sample", "met": sufficient, "evidence_ids": ids, "explanation": "适用条件核验"}],
            "blockers": [] if sufficient else ["缺适用条件"],
            "followup_queries": [] if sufficient else [{"source_kind": "knowledge", "query": "followup-conditions-qz", "purpose": "核验适用条件"}],
            "summary": "核验通过" if sufficient else "需要补查",
            "audit_brief_hash": context["audit_brief_hash"],
            "procedures": [{"procedure_id": "sample-program", "met": sufficient, "evidence_ids": ids, "explanation": "逐程序适用条件核验"}],
            "return_to_audit_brain": not sufficient,
        }


@pytest.fixture()
def fixture(tmp_path: Path):
    tenant, trace = uuid4(), uuid4()
    source = tmp_path / "input.json"
    source.write_text(json.dumps({"population": 100, "fixture_id": str(uuid4())}), encoding="utf-8")
    import hashlib
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    artifact = uuid4()
    specs = discover_plugins(domain="audit")
    port = specs["audit.plan.sample-size-compute"].inputs[0]
    with psycopg2.connect(DB) as conn, conn.cursor() as cur:
        cur.execute("INSERT INTO iam.tenants(id,slug,name) VALUES(%s,%s,'collaborative fixture')", (str(tenant), f"collab-service-{tenant.hex}"))
        cur.execute("SELECT set_config('app.tenant_id',%s,true)", (str(tenant),))
        cur.execute("INSERT INTO policy.policy_sets(tenant_id,name,version,status,rules) VALUES(%s,'fixture-plan',1,'active',%s)", (
            str(tenant), Json([{"rule_id": str(uuid4()), "effect": "allow", "match": {"capabilities": ["topology.collaborative.*"]}}]),
        ))
        cur.execute("INSERT INTO artifact.blobs(sha256,byte_size,media_type,storage_uri) VALUES(%s,%s,'application/json',%s) ON CONFLICT(sha256) DO NOTHING", (digest, source.stat().st_size, source.as_uri()))
        cur.execute("INSERT INTO artifact.artifacts(id,tenant_id,blob_sha256,artifact_type,classification) VALUES(%s,%s,%s,'fixture','internal')", (str(artifact), str(tenant), digest))
        space, node = uuid4(), uuid4()
        cur.execute("INSERT INTO graph.spaces(id,tenant_id,key,level,name) VALUES(%s,%s,%s,'L2','fixture')", (str(space), str(tenant), f"fixture-{space.hex}"))
        cur.execute("INSERT INTO graph.nodes(id,tenant_id,space_id,canonical_key,node_type,label,properties) VALUES(%s,%s,%s,%s,'capability',%s,%s)", (str(node), str(tenant), str(space), 'capability:audit.plan.sample-size-compute', 'collaborative-main-goal-xz', Json({"capability": "audit.plan.sample-size-compute"})))
        document = uuid4()
        cur.execute("INSERT INTO semantic.documents(id,tenant_id,source_uri,title,content_sha256) VALUES(%s,%s,%s,'planning methods',%s)", (str(document), str(tenant), f"fixture://{document}", fingerprint('document')))
        for index, content in enumerate(["collaborative-main-goal-xz initial method", "followup-conditions-qz checked method"]):
            cur.execute("INSERT INTO semantic.chunks(tenant_id,document_id,ordinal,content,document_version) VALUES(%s,%s,%s,%s,1)", (str(tenant), str(document), index, content))
    request = PlanningRequest.model_validate({
        "goal": "collaborative-main-goal-xz", "reason": "synthetic local test",
        "requirements": [{"key": "sample", "description": "sample size", "output_ports": ["sample-size"]}],
        "inputs": [{"artifact_id": str(artifact), "port_id": port.port_id, "schema_ref": port.schema_ref}],
        "idempotency_key": f"service-{uuid4().hex}",
    })
    context = PlanningContext(tenant_id=tenant, trace_id=trace, session_id=uuid4())
    service = CollaborativePlanningService(DB, sources=SqlEvidenceSources(DB, allowed_roots=(tmp_path,)), model=OfflineModel())
    return service, context, request


def test_real_sources_drive_two_rounds_and_persist_evaluation(fixture) -> None:
    service, context, request = fixture
    created = service.create(context, request)
    result = service.process_next(context.tenant_id, worker_id="offline-integration")
    assert result is not None
    assert result["status"] == "accepted", result
    assert result["rounds"] == 2
    assert result["model_calls"] == 6
    assert result["audit_brief"]["procedures"]
    events = service.get(context)["events"]
    assert [event["payload"]["role"] for event in events if event["event_type"] == "model_started"] == ["audit_brain", "planner", "reviewer"] * 2
    assert result["assessment"]["passed"] is True
    row = service.get(context)
    assert row["session_id"] == created["session_id"]
    assert row["result_json"]["status"] == "accepted"
    assert any(event["event_type"] == "source_query" for event in row["events"])
    assert {event["payload"]["query"]["source_kind"] for event in row["events"] if event["event_type"] == "source_query"} == {"graph", "history", "knowledge"}
    replay = service.process(context, worker_id="another-worker")
    assert replay["assessment"] == result["assessment"]


def test_model_access_snapshot_survives_checkpoints_and_terminal_replay(fixture) -> None:
    service, context, request = fixture
    created = service.create(context, request)
    snapshot = created["model_access"]
    assert snapshot["mode"] == "injected"
    result = service.process(context, worker_id="model-snapshot")
    assert result["status"] == "accepted"
    assert result["model_access"] == snapshot
    row = service.get(context)
    assert row["checkpoint_json"]["model_access"] == snapshot
    assert row["events"][0]["payload"]["model_access"] == snapshot
    service.model = None
    assert service.process(context, worker_id="replay")["model_access"] == snapshot


def test_pending_session_cannot_silently_change_mode(fixture, monkeypatch) -> None:
    service, context, request = fixture
    service.create(context, request)
    service.model = None
    monkeypatch.setenv("AI_PLANNING_MODE", "debug")
    result = service.process(context, worker_id="mode-changed")
    assert result["status"] == "failed"
    assert "configuration changed" in result["detail"]
    assert not any(event["event_type"] == "model_started" for event in service.get(context)["events"])


def test_configured_api_runs_same_loop_and_records_transport(fixture, monkeypatch) -> None:
    """A controlled loopback provider, never a cloud or production request."""
    service, context, request = fixture
    bodies = []
    offline = OfflineModel()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            assert self.path == "/v1/chat/completions"
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            bodies.append(body)
            answer = offline.complete_json(body["messages"])
            raw = json.dumps({"choices": [{"message": {"content": json.dumps(answer)}}]}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        monkeypatch.setenv("AUDIT_NETWORK_SKIP_DOTENV", "1")
        monkeypatch.setenv("AI_PLANNING_MODE", "external")
        monkeypatch.setenv("AI_PROVIDER", "openai_compat")
        monkeypatch.setenv("AI_BASE_URL", f"http://127.0.0.1:{server.server_port}/v1")
        monkeypatch.setenv("AI_API_KEY", "placeholder-api-mode-integration")
        monkeypatch.setenv("AI_MODEL", "controlled-provider")
        service.model = None
        created = service.create(context, request)
        assert created["model_access"]["mode"] == "external"
        result = service.process(context, worker_id="external-api-loopback")
        assert result["status"] == "accepted", result
        assert len(bodies) == result["model_calls"] == 6
        events = service.get(context)["events"]
        started = [event for event in events if event["event_type"] == "model_started"]
        assert all(event["payload"]["model_mode"] == "external" for event in started)
        assert all(event["payload"]["transport"] == "configured-ai-api" for event in started)
        assert "placeholder-api-mode-integration" not in json.dumps(events)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_external_network_policy_blocks_before_transport(fixture, monkeypatch) -> None:
    from packages.ai.gateway import UnifiedAIClient

    service, context, request = fixture
    monkeypatch.setenv("AI_PLANNING_MODE", "external")
    monkeypatch.setenv("AI_PROVIDER", "openai_compat")
    monkeypatch.setenv("AI_API_KEY", "placeholder-policy-test")
    monkeypatch.setenv("AI_BASE_URL", "https://example.invalid/v1")
    service.model = None
    with psycopg2.connect(DB) as conn, conn.cursor() as cur:
        cur.execute("SELECT set_config('app.tenant_id',%s,true)", (str(context.tenant_id),))
        cur.execute("INSERT INTO policy.policy_sets(tenant_id,name,version,status,rules) VALUES(%s,'deny-external',1,'active',%s)", (
            str(context.tenant_id), Json([{"effect": "deny", "match": {"capabilities": ["topology.collaborative.model.call"],
                "risk_classes": ["medium"], "side_effects": ["external_network"], "argument_equals": {"model_mode": "external"}}}]),
        ))
    def forbidden(*args, **kwargs):
        pytest.fail("policy must reject before provider access")
    monkeypatch.setattr(UnifiedAIClient, "complete_json", forbidden)
    service.create(context, request)
    result = service.process(context, worker_id="denied-external")
    assert result["status"] == "blocked_policy"
    assert result["assessment"] is None


@pytest.mark.parametrize("catalog_state", ["changed", "unavailable"])
def test_final_catalog_change_revokes_passing_assessment(fixture, monkeypatch, catalog_state) -> None:
    from packages.ai_planner import planning_service

    service, context, request = fixture
    service.create(context, request)
    original = planning_service.frozen_catalog
    calls = 0

    def changing_catalog(domain):
        nonlocal calls
        calls += 1
        if calls == 1:
            return original(domain)
        if catalog_state == "unavailable":
            raise OSError("catalog disappeared")
        specs, _ = original(domain)
        return specs, "changed"

    monkeypatch.setattr(planning_service, "frozen_catalog", changing_catalog)
    result = service.process(context, worker_id="catalog-change-validation")
    assert result["status"] == "failed"
    assert result["assessment"]["passed"] is False
    assert result["assessment"]["plan"] is None
    assert result["checkpoint"]["assessment"]["passed"] is False
    assert service.get(context)["result_json"]["assessment"]["plan"] is None


def test_source_denial_is_durable_and_never_produces_a_passing_plan(fixture) -> None:
    service, context, request = fixture
    service.create(context, request)
    with psycopg2.connect(DB) as conn, conn.cursor() as cur:
        cur.execute("SELECT set_config('app.tenant_id',%s,true)", (str(context.tenant_id),))
        cur.execute("INSERT INTO policy.policy_sets(tenant_id,name,version,status,rules) VALUES(%s,'deny-knowledge',1,'active',%s)", (str(context.tenant_id), Json([{"effect": "deny", "match": {"capabilities": ["topology.collaborative.knowledge.read"]}}])))
    result = service.process(context, worker_id="denied-source-test")
    assert result["status"] == "blocked_policy"
    assert not result["assessment"]
    assert service.get(context)["result_json"]["status"] == "blocked_policy"


def test_session_submission_replays_and_conflicting_request_is_rejected(fixture) -> None:
    service, context, request = fixture
    first = service.create(context, request)
    second = service.create(context.model_copy(update={"session_id": uuid4(), "trace_id": uuid4()}), request)
    assert first["session_id"] == second["session_id"]
    with pytest.raises(ValueError, match="different"):
        service.create(context, request.model_copy(update={"goal": "a changed goal"}))


def test_api_submits_and_reads_a_persistent_session(fixture, monkeypatch) -> None:
    from fastapi.testclient import TestClient

    from apps.api import main

    service, context, request = fixture
    monkeypatch.setattr(main, "_collaborative_service", lambda url: service)
    client = TestClient(main.create_app(main.Settings(database_url=DB)))
    headers = {"X-Tenant-Id": str(context.tenant_id), "X-Trace-Id": str(context.trace_id)}
    base = "/api/v1/topology/planning/sessions"
    created = client.post(base, headers=headers, json=request.model_dump(mode="json"))
    assert created.status_code == 201, created.text
    session_id = created.json()["session_id"]
    replay = client.post(base, headers=headers, json=request.model_dump(mode="json"))
    assert replay.status_code == 200 and replay.json()["session_id"] == session_id
    listed = client.get(base, headers=headers)
    assert listed.status_code == 200 and listed.json()["items"][0]["session_id"] == session_id
    detail = client.get(f"{base}/{session_id}", headers=headers)
    assert detail.status_code == 200 and detail.json()["events"]
    assert "lease_token" not in detail.json()
    run = client.post(f"{base}/{session_id}/process", headers=headers)
    assert run.status_code == 200 and run.json()["status"] == "accepted", run.text


def test_api_unknown_tenant_is_not_reported_as_missing_migrations(fixture, monkeypatch) -> None:
    from fastapi.testclient import TestClient

    from apps.api.main import Settings, create_app

    client = TestClient(create_app(Settings(database_url=DB)))
    response = client.get("/api/v1/topology/planning/catalog", headers={"X-Tenant-Id": str(uuid4())})
    assert response.status_code == 404
    assert "tenant" in response.json()["detail"]
    assert "migration" not in response.text


def test_api_rejects_execution_fields_and_ungranted_reads(fixture, monkeypatch) -> None:
    from fastapi.testclient import TestClient

    from apps.api import main

    service, context, request = fixture
    monkeypatch.setattr(main, "_collaborative_service", lambda url: service)
    client = TestClient(main.create_app(main.Settings(database_url=DB)))
    headers = {"X-Tenant-Id": str(context.tenant_id), "X-Trace-Id": str(context.trace_id)}
    base = "/api/v1/topology/planning/sessions"
    rejected = client.post(base, headers=headers, json={**request.model_dump(mode="json"), "mode": "execute"})
    assert rejected.status_code == 422
    with psycopg2.connect(DB) as conn, conn.cursor() as cur:
        cur.execute("SELECT set_config('app.tenant_id',%s,true)", (str(context.tenant_id),))
        cur.execute("UPDATE policy.policy_sets SET status='inactive' WHERE tenant_id=%s", (str(context.tenant_id),))
    blocked = client.get(base, headers=headers)
    assert blocked.status_code in (403, 409), blocked.text
