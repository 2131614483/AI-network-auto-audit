"""Local installer bootstrap decisions precede side effects and remain auditable."""
from __future__ import annotations

import json
import subprocess
import sys
from uuid import uuid4

import pytest


def request(**changes):
    return {"action": "initialize", "target": "0069_collaborative_planning",
            "confirmed": True, "idempotency_key": str(uuid4()), **changes}


def test_maintenance_lock_excludes_other_processes_and_releases(tmp_path):
    from packages.policy.maintenance import MaintenanceGateway

    script = """from pathlib import Path
import sys
from packages.policy.maintenance import MaintenanceGateway
try:
    with MaintenanceGateway(Path(sys.argv[1]))._locked():
        pass
except (OSError, BlockingIOError):
    sys.exit(17)
"""
    def probe():
        return subprocess.run([sys.executable, "-c", script, str(tmp_path)], check=False, capture_output=True).returncode

    gateway = MaintenanceGateway(tmp_path)
    with gateway._locked():
        assert probe() == 17
    assert probe() == 0


def test_confirmation_is_required_and_denial_is_recorded(tmp_path):
    from packages.policy.maintenance import MaintenanceGateway

    gateway = MaintenanceGateway(tmp_path)
    with pytest.raises(PermissionError):
        gateway.begin(request(confirmed=False))
    record = gateway.records()[-1]
    assert record["decision"] == "DENY" and record["trace_id"] and record["tenant_id"]


def test_initialization_has_a_narrow_allow_and_identity(tmp_path):
    from packages.policy.maintenance import MaintenanceGateway

    gateway = MaintenanceGateway(tmp_path)
    record = gateway.begin(request())
    assert record["decision"] == "ALLOW"
    assert record["capability"] == "workspace.maintenance.initialize"
    assert record["status"] == "started"
    assert gateway.records() == [record]
    assert "password" not in json.dumps(record)


def test_idempotency_replay_and_conflict(tmp_path):
    from packages.policy.maintenance import MaintenanceGateway

    gateway = MaintenanceGateway(tmp_path)
    payload = request()
    first = gateway.begin(payload)
    assert gateway.begin(payload) == first
    with pytest.raises(ValueError, match="idempotency"):
        gateway.begin({**payload, "target": "other_revision"})


def test_existing_cluster_cannot_use_bootstrap_initialization(tmp_path):
    from packages.policy.maintenance import MaintenanceGateway

    (tmp_path / "pgdata").mkdir()
    (tmp_path / "pgdata" / "PG_VERSION").write_text("16")
    with pytest.raises(PermissionError):
        MaintenanceGateway(tmp_path).begin(request())


def test_unknown_fields_and_commands_are_rejected(tmp_path):
    from packages.policy.maintenance import MaintenanceGateway

    with pytest.raises(ValueError):
        MaintenanceGateway(tmp_path).begin(request(command="arbitrary"))
    with pytest.raises(ValueError):
        MaintenanceGateway(tmp_path).begin(request(action="execute"))


def test_upgrade_requires_live_policy_and_records_denial(tmp_path, monkeypatch):
    from packages.policy.maintenance import MaintenanceGateway

    gateway = MaintenanceGateway(tmp_path, database_url="fixture")
    monkeypatch.setattr(gateway, "_live_policy", lambda: (str(uuid4()), []))
    imported = []
    monkeypatch.setattr(gateway, "_import_records", lambda records, tenant: imported.extend(records))
    with pytest.raises(PermissionError):
        gateway.begin(request(action="upgrade", current="0068"))
    assert gateway.records()[-1]["decision"] != "ALLOW"
    assert len(imported) == 1


def test_failure_keeps_the_start_and_appends_a_terminal_record(tmp_path):
    from packages.policy.maintenance import MaintenanceGateway

    gateway = MaintenanceGateway(tmp_path)
    started = gateway.begin(request())
    gateway.finish(started["operation_id"], "failed")
    records = gateway.records()
    assert [r["status"] for r in records] == ["started", "failed"]
    assert records[1]["previous_sha256"] == records[0]["sha256"]
    assert records[1]["trace_id"] == records[0]["trace_id"]
    assert gateway.finish(started["operation_id"], "failed") == records[1]
    with pytest.raises(ValueError):
        gateway.finish(started["operation_id"], "succeeded")


def test_modified_history_blocks_new_maintenance(tmp_path):
    from packages.policy.maintenance import MaintenanceGateway

    gateway = MaintenanceGateway(tmp_path)
    gateway.begin(request())
    log = tmp_path / "maintenance" / "history.jsonl"
    log.write_text(log.read_text().replace('"ALLOW"', '"DENY"'))
    with pytest.raises(ValueError, match="history"):
        gateway.begin(request())


def test_successful_initialization_imports_evidence_before_returning(tmp_path, monkeypatch):
    from packages.policy.maintenance import MaintenanceGateway

    gateway = MaintenanceGateway(tmp_path, database_url="fixture")
    started = gateway.begin(request())
    imported = []
    monkeypatch.setattr(gateway, "_import_bootstrap", lambda record: imported.append(record))
    result = gateway.finish(started["operation_id"], "succeeded")
    assert imported == [result]


def test_an_unfinished_operation_blocks_a_second_mutation(tmp_path):
    from packages.policy.maintenance import MaintenanceGateway

    gateway = MaintenanceGateway(tmp_path)
    gateway.begin(request())
    with pytest.raises(ValueError, match="unfinished"):
        gateway.begin(request())


def test_planning_setup_only_accepts_the_previewed_external_configuration(tmp_path, monkeypatch):
    from packages.policy.maintenance import MaintenanceGateway

    gateway = MaintenanceGateway(tmp_path, database_url="fixture")
    monkeypatch.setattr(gateway, "planning_preview", lambda: {"mode": "external", "model": "fixture",
        "endpoint": "http://127.0.0.1:12345/v1", "config_hash": "a" * 64})
    with pytest.raises(ValueError, match="configuration"):
        gateway.begin(request(action="configure_planning", expected_model_hash="b" * 64))


def test_planning_grants_are_scoped_and_audited(tmp_path, monkeypatch):
    from packages.policy.maintenance import MaintenanceGateway, planning_rules

    snapshot = {"mode": "external", "model": "fixture", "endpoint": "http://127.0.0.1:12345/v1", "config_hash": "a" * 64}
    rules = planning_rules(snapshot)
    capabilities = [cap for rule in rules for cap in rule["match"]["capabilities"]]
    assert all(cap.startswith("topology.collaborative.") and "*" not in cap for cap in capabilities)
    model = next(rule for rule in rules if "topology.collaborative.model.call" in rule["match"]["capabilities"])
    assert model["match"]["argument_equals"]["endpoint"] == snapshot["endpoint"]
    assert model["match"]["argument_equals"]["model_config_hash"] == snapshot["config_hash"]
    gateway = MaintenanceGateway(tmp_path, database_url="fixture")
    monkeypatch.setattr(gateway, "planning_preview", lambda: snapshot)
    monkeypatch.setattr(gateway, "_live_policy", lambda: (str(uuid4()), [{"effect": "allow",
        "match": {"capabilities": ["workspace.maintenance.configure_planning"]}}]))
    imported, writes = [], []
    monkeypatch.setattr(gateway, "_import_records", lambda records, tenant: imported.extend(records))
    monkeypatch.setattr(gateway, "_write_planning_rules", lambda record: writes.append(record))
    gateway.configure_planning(request(action="configure_planning", expected_model_hash="a" * 64))
    assert len(writes) == 1 and len(imported) == 2
    assert [r["status"] for r in gateway.records()] == ["started", "succeeded"]
