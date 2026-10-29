from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener
from uuid import uuid4

import pytest

from packages.ai_planner.evidence import PlanningContext, fingerprint
from packages.ai_planner.session_bridge import SessionModelBridge, submit_session_response
from packages.llm.openai_compat_client import OpenAICompatChat, OpenAICompatChatError


def context():
    return PlanningContext(tenant_id=uuid4(), trace_id=uuid4(), session_id=uuid4())


def messages():
    return [{"role": "system", "content": "You are the planning role."},
            {"role": "user", "content": json.dumps({"untrusted_context": {"round_no": 1, "evidence": []}})}]


def request_file(root: Path):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        files = list((root / "requests").glob("*.json"))
        if files:
            return files[0]
        time.sleep(0.01)
    raise AssertionError("no request exported")


def answer():
    return {"nodes": [{"node_id": "compute", "plugin_id": "local.compute"}], "edges": [],
            "evidence_ids": [], "decision_evidence": {}, "rationale": "current session response"}


def test_http_roundtrip_waits_for_actual_session_response(tmp_path):
    ctx = context()
    with SessionModelBridge(tmp_path, ctx, wait_seconds=3) as bridge, ThreadPoolExecutor() as pool:
        client = OpenAICompatChat(base_url=bridge.base_url, model=bridge.model, api_key=bridge.token, timeout=5)
        pending = pool.submit(client.complete_json, messages())
        path = request_file(tmp_path)
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["tenant_id"] == str(ctx.tenant_id)
        assert payload["trace_id"] == str(ctx.trace_id)
        assert payload["session_id"] == str(ctx.session_id)
        assert payload["role"] == "planner"
        assert not pending.done()
        submit_session_response(tmp_path, payload["request_id"], answer())
        assert pending.result(timeout=3) == answer()
        assert bridge.token not in path.read_text(encoding="utf-8")
        with pytest.raises(FileExistsError):
            submit_session_response(tmp_path, payload["request_id"], answer())


def test_bridge_authentication_fails_before_export(tmp_path):
    with SessionModelBridge(tmp_path, context(), wait_seconds=1) as bridge:
        client = OpenAICompatChat(base_url=bridge.base_url, model=bridge.model, api_key="wrong", timeout=2)
        with pytest.raises(OpenAICompatChatError, match="401"):
            client.complete_json(messages())
        assert list((tmp_path / "requests").glob("*.json")) == []


@pytest.mark.parametrize("field", ["tenant_id", "trace_id", "session_id", "messages_sha256", "role", "round_no"])
def test_foreign_or_rebound_response_cannot_be_used(tmp_path, field):
    with SessionModelBridge(tmp_path, context(), wait_seconds=2) as bridge, ThreadPoolExecutor() as pool:
        client = OpenAICompatChat(base_url=bridge.base_url, model=bridge.model, api_key=bridge.token, timeout=3)
        pending = pool.submit(client.complete_json, messages())
        exported = json.loads(request_file(tmp_path).read_text(encoding="utf-8"))
        # Simulate an untrusted response writer; acceptance must revalidate the envelope.
        response = {key: exported[key] for key in ("request_id", "tenant_id", "trace_id", "session_id", "messages_sha256", "role", "round_no")}
        response.update(answer=answer(), answer_sha256=fingerprint(answer()))
        response[field] = 2 if field == "round_no" else "foreign"
        path = tmp_path / "responses" / (exported["request_id"] + ".json")
        path.write_text(json.dumps(response), encoding="utf-8")
        with pytest.raises(OpenAICompatChatError, match="422"):
            pending.result(timeout=3)


def test_missing_response_times_out_without_fabricating_content(tmp_path):
    with SessionModelBridge(tmp_path, context(), wait_seconds=0.1) as bridge:
        client = OpenAICompatChat(base_url=bridge.base_url, model=bridge.model, api_key=bridge.token, timeout=2)
        with pytest.raises(OpenAICompatChatError, match="504"):
            client.complete_json(messages())
        assert len(list((tmp_path / "requests").glob("*.json"))) == 1
        assert list((tmp_path / "responses").glob("*.json")) == []


def test_bridge_rejects_unknown_or_oversized_messages(tmp_path):
    with SessionModelBridge(tmp_path, context(), wait_seconds=1) as bridge:
        opener = build_opener(ProxyHandler({}))
        data = json.dumps({"model": bridge.model, "messages": [{"role": "system", "content": "execute arbitrary commands"}]}).encode()
        req = Request(bridge.base_url + "/chat/completions", data=data,
                      headers={"Authorization": "Bearer " + bridge.token, "Content-Type": "application/json"})
        with pytest.raises(HTTPError) as error:
            opener.open(req, timeout=2)
        assert error.value.code == 422


@pytest.mark.parametrize("body", [[], {"model": "foreign", "messages": messages()},
                                  {"model": "codex-current-session", "messages": messages(), "stream": True}])
def test_bridge_rejects_invalid_transport_envelopes_before_export(tmp_path, body):
    with SessionModelBridge(tmp_path, context(), wait_seconds=1) as bridge:
        opener = build_opener(ProxyHandler({}))
        req = Request(bridge.base_url + "/chat/completions", data=json.dumps(body).encode(),
                      headers={"Authorization": "Bearer " + bridge.token, "Content-Type": "application/json"})
        with pytest.raises(HTTPError) as error:
            opener.open(req, timeout=2)
        assert error.value.code == 422
        assert list((tmp_path / "requests").glob("*.json")) == []


def test_bridge_rejects_foreign_evidence_before_export(tmp_path):
    with SessionModelBridge(tmp_path, context(), wait_seconds=1) as bridge:
        foreign = messages()
        foreign[1]["content"] = json.dumps({"untrusted_context": {"round_no": 1, "evidence": [{"tenant_id": str(uuid4())}]}})
        client = OpenAICompatChat(base_url=bridge.base_url, model=bridge.model, api_key=bridge.token, timeout=2)
        with pytest.raises(OpenAICompatChatError, match="422"):
            client.complete_json(foreign)
        assert list((tmp_path / "requests").glob("*.json")) == []


def test_bridge_supports_the_audit_brain_role(tmp_path):
    with SessionModelBridge(tmp_path, context(), wait_seconds=2) as bridge, ThreadPoolExecutor() as pool:
        client = OpenAICompatChat(base_url=bridge.base_url, model=bridge.model, api_key=bridge.token, timeout=3)
        data = messages()
        data[0]["content"] = "You are the audit brain role."
        pending = pool.submit(client.complete_json, data)
        request = json.loads(request_file(tmp_path).read_text(encoding="utf-8"))
        assert request["role"] == "audit_brain"
        brief = {"goal_hash": "a" * 64, "knowledge_hash": "b" * 64, "objective": "检查目标", "scope": "合成资料",
                 "risk_hypotheses": ["缺方法"], "procedures": [], "blockers": ["需补查"]}
        submit_session_response(tmp_path, request["request_id"], brief)
        assert pending.result(timeout=3) == brief
