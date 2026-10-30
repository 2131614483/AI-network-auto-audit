"""Restricted installer gateway: bootstrap journal first, live policy on upgrades.

This entry point accepts no SQL, shell commands or model requests. A bootstrap
tenant is a workspace identity, explicitly linked to the business tenant when
the newly created schema can store policy decisions. The local hash chain is
an integrity check, not protection against a user who controls the whole disk.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import psycopg2
from psycopg2.extensions import parse_dsn
from psycopg2.extras import Json
from pydantic import BaseModel, ConfigDict, Field

from .engine import PolicyEngine, PolicyResult
from .gateway import decision_name, record_decision


class MaintenanceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    action: Literal["initialize", "upgrade", "configure_planning"]
    target: str = Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9_]+$")
    current: str | None = Field(default=None, max_length=128, pattern=r"^[a-zA-Z0-9_]+$")
    confirmed: bool = Field(strict=True)
    idempotency_key: str = Field(min_length=8, max_length=128)
    expected_model_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


def planning_rules(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {"rule_id": "managed-plan-only-write", "effect": "allow", "match": {
            "capabilities": ["topology.collaborative.plan"], "risk_classes": ["low"], "side_effects": ["write_data"]}},
        {"rule_id": "managed-plan-only-read", "effect": "allow", "match": {
            "capabilities": ["topology.collaborative.read", "topology.collaborative.graph.read",
                "topology.collaborative.history.read", "topology.collaborative.knowledge.read", "topology.collaborative.input.read"],
            "risk_classes": ["read_only"], "side_effects": ["read_only"]}},
        {"rule_id": "managed-planning-model", "effect": "allow", "match": {
            "capabilities": ["topology.collaborative.model.call"], "risk_classes": ["medium"], "side_effects": ["external_network"],
            "argument_equals": {"model_mode": "external", "model": snapshot["model"],
                "endpoint": snapshot["endpoint"], "model_config_hash": snapshot["config_hash"]}}},
    ]


def _hash(value: Any) -> str:
    data = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(data.encode()).hexdigest()


class MaintenanceGateway:
    def __init__(self, workspace: Path, *, database_url: str | None = None) -> None:
        self.workspace = workspace.resolve()
        self.directory = self.workspace / "maintenance"
        self.history = self.directory / "history.jsonl"
        self.database_url = database_url
        self.workspace_id = str(uuid5(NAMESPACE_URL, str(self.workspace)))

    @contextmanager
    def _locked(self) -> Iterator[None]:
        self.directory.mkdir(parents=True, exist_ok=True)
        with (self.directory / "journal.lock").open("a+b") as lock:
            if lock.tell() == 0:
                lock.write(b"0")
                lock.flush()
            lock.seek(0)
            if os.name == "nt":
                import msvcrt

                getattr(msvcrt, "locking")(lock.fileno(), getattr(msvcrt, "LK_NBLCK"), 1)
            else:
                import fcntl

                getattr(fcntl, "flock")(lock.fileno(), getattr(fcntl, "LOCK_EX") | getattr(fcntl, "LOCK_NB"))
            try:
                yield
            finally:
                lock.seek(0)
                if os.name == "nt":
                    getattr(msvcrt, "locking")(lock.fileno(), getattr(msvcrt, "LK_UNLCK"), 1)
                else:
                    getattr(fcntl, "flock")(lock.fileno(), getattr(fcntl, "LOCK_UN"))

    def records(self) -> list[dict[str, Any]]:
        if not self.history.exists():
            return []
        result: list[dict[str, Any]] = []
        previous = "0" * 64
        try:
            for line in self.history.read_text(encoding="utf-8").splitlines():
                record = json.loads(line)
                digest = record.pop("sha256")
                if (record["previous_sha256"] != previous or _hash(record) != digest
                        or record["workspace_id"] != self.workspace_id):
                    raise ValueError("maintenance history integrity check failed")
                record["sha256"] = digest
                previous = digest
                result.append(record)
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise ValueError("maintenance history is unreadable") from exc
        return result

    def _append(self, value: dict[str, Any]) -> dict[str, Any]:
        records = self.records()
        record = {**value, "previous_sha256": records[-1]["sha256"] if records else "0" * 64,
                  "at": datetime.now(timezone.utc).isoformat()}
        record["sha256"] = _hash(record)
        with self.history.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        return record

    def _live_policy(self) -> tuple[str, list[dict[str, Any]]]:
        if not self.database_url or parse_dsn(self.database_url).get("host") not in {"localhost", "127.0.0.1", "::1"}:
            raise PermissionError("maintenance requires the managed loopback database")
        with psycopg2.connect(self.database_url, connect_timeout=5) as connection, connection.cursor() as cur:
            cur.execute("SELECT id FROM iam.tenants WHERE slug='local-dev'")
            row = cur.fetchone()
            if row is None:
                raise PermissionError("maintenance tenant is unavailable")
            tenant = str(row[0])
            cur.execute("SELECT set_config('app.tenant_id',%s,true)", (tenant,))
            cur.execute("SELECT rules FROM policy.policy_sets WHERE tenant_id=%s AND status='active' ORDER BY created_at,id", (tenant,))
            rules = [rule for row in cur.fetchall() for rule in row[0] if isinstance(rule, dict)]
        return tenant, rules

    def begin(self, payload: dict[str, Any]) -> dict[str, Any]:
        request = MaintenanceRequest.model_validate(payload)
        if request.action == "upgrade" and not request.current:
            raise ValueError("upgrade requires a current revision")
        arguments = {**request.model_dump(), "workspace_id": self.workspace_id}
        if request.action == "configure_planning":
            snapshot = self.planning_preview()
            if not request.expected_model_hash or snapshot["config_hash"] != request.expected_model_hash:
                raise ValueError("planning configuration changed; preview and confirm again")
            arguments["model_access"] = snapshot
        request_hash = _hash(arguments)
        with self._locked():
            records = self.records()
            for record in records:
                if record["idempotency_key"] == request.idempotency_key:
                    if record["request_hash"] != request_hash:
                        raise ValueError("maintenance idempotency conflict")
                    if record["decision"] != "ALLOW":
                        raise PermissionError("workspace maintenance was not authorized")
                    return record
            latest = {r["operation_id"]: r for r in records}
            if any(r["status"] == "started" for r in latest.values()):
                raise ValueError("unfinished maintenance requires explicit recovery")
            tenant = self.workspace_id
            capability = f"workspace.maintenance.{request.action}"
            if not request.confirmed:
                decision = PolicyResult("deny", "high", "explicit local confirmation is required")
            elif request.action == "initialize":
                existing = (self.workspace / "pgdata" / "PG_VERSION").exists() or (self.workspace / "initialized.json").exists()
                rules = [] if existing else [{"rule_id": "local-empty-workspace-bootstrap", "effect": "allow",
                    "match": {"capabilities": [capability], "argument_equals": {"confirmed": True, "workspace_id": self.workspace_id}}}]
                decision = PolicyEngine(rules=rules).evaluate(capability, arguments, "high", side_effects="write_data")
            else:
                tenant, rules = self._live_policy()
                decision = PolicyEngine(rules=rules).evaluate(capability, arguments, "high", side_effects="write_data")
            record = self._append({"operation_id": str(uuid4()), "workspace_id": self.workspace_id,
                "tenant_id": tenant, "tenant_kind": "bootstrap" if request.action == "initialize" else "business",
                "trace_id": str(uuid4()), "decision_id": str(uuid4()), "capability": capability,
                "decision": decision_name(decision.decision), "reason": decision.reason,
                "request_hash": request_hash, "idempotency_key": request.idempotency_key,
                "arguments": arguments, "status": "started" if decision.decision == "allow" else "denied"})
            if request.action != "initialize":
                self._import_records([record], tenant)
            if decision.decision != "allow":
                raise PermissionError("workspace maintenance was not authorized; inspect maintenance/history.jsonl")
            return record

    def planning_preview(self) -> dict[str, Any]:
        from packages.ai_planner.model_access import resolve_model_access

        access = resolve_model_access()
        if access.mode != "external":
            raise ValueError("此普通工作区请先在 AI 设置选择外接 API；Debug 保留在独立合成测试库中使用。")
        return {"mode": access.mode, "provider": access.provider, "model": access.model,
                "endpoint": access.endpoint, "config_hash": access.config_hash}

    def configure_planning(self, payload: dict[str, Any]) -> dict[str, Any]:
        if payload.get("action") != "configure_planning":
            raise ValueError("planning setup requires its fixed capability")
        before = len(self.records())
        record = self.begin(payload)
        if len(self.records()) == before:
            raise ValueError("planning setup was already recorded; inspect its outcome")
        try:
            self._write_planning_rules(record)
        except Exception:
            self.finish(record["operation_id"], "failed")
            raise
        return self.finish(record["operation_id"], "succeeded")

    def _write_planning_rules(self, record: dict[str, Any]) -> None:
        if not self.database_url:
            raise PermissionError("the managed application database is required")
        snapshot = record["arguments"]["model_access"]
        if self.planning_preview() != snapshot:
            raise ValueError("planning configuration changed before setup")
        tenant = record["tenant_id"]
        name = f"managed-planning-{self.workspace_id}"
        with psycopg2.connect(self.database_url, connect_timeout=5) as connection, connection.cursor() as cur:
            cur.execute("SELECT set_config('app.tenant_id',%s,true)", (tenant,))
            cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (name,))
            cur.execute("SELECT COALESCE(MAX(version::int),0)+1 FROM policy.policy_sets WHERE tenant_id=%s AND name=%s", (tenant, name))
            row = cur.fetchone()
            assert row is not None
            version = row[0]
            cur.execute("UPDATE policy.policy_sets SET status='inactive' WHERE tenant_id=%s AND name=%s AND status='active'", (tenant, name))
            cur.execute("""INSERT INTO policy.policy_sets(tenant_id,name,version,status,rules)
                VALUES(%s,%s,%s,'active',%s)""", (tenant, name, str(version), Json(planning_rules(snapshot))))

    def finish(self, operation_id: str, status: str) -> dict[str, Any]:
        if status not in {"succeeded", "failed"}:
            raise ValueError("invalid maintenance outcome")
        with self._locked():
            records = [r for r in self.records() if r["operation_id"] == str(UUID(operation_id))]
            if not records or records[0]["decision"] != "ALLOW":
                raise ValueError("no authorized maintenance operation")
            latest = records[-1]
            if latest["status"] != "started":
                if latest["status"] != status:
                    raise ValueError("maintenance terminal outcome conflict")
                record = latest
            else:
                record = self._append({k: v for k, v in {**latest, "status": status}.items()
                                       if k not in {"sha256", "previous_sha256", "at"}})
            if record["capability"].endswith("initialize"):
                if status == "succeeded":
                    self._import_bootstrap(record)
            else:
                self._import_records([record], record["tenant_id"])
            return record

    def _import_records(self, records: list[dict[str, Any]], tenant: str) -> None:
        if not self.database_url:
            raise PermissionError("the application database is required to persist maintenance decisions")
        with psycopg2.connect(self.database_url, connect_timeout=5) as connection, connection.cursor() as cur:
            cur.execute("SELECT set_config('app.tenant_id',%s,true)", (tenant,))
            self._record_rows(cur, records, tenant)

    @staticmethod
    def _record_rows(cur: Any, records: list[dict[str, Any]], tenant: str) -> None:
        for record in records:
            cur.execute("SELECT id FROM policy.tool_calls WHERE tenant_id=%s AND arguments->>'maintenance_record_sha256'=%s", (tenant, record["sha256"]))
            if cur.fetchone() is not None:
                continue
            result = PolicyResult("allow" if record["decision"] == "ALLOW" else "deny", "high", record["reason"])
            record_decision(cur, UUID(tenant), record["capability"], {
                **record["arguments"], "maintenance_record_sha256": record["sha256"],
                "bootstrap_tenant_id": record["tenant_id"], "maintenance_operation_id": record["operation_id"],
                "maintenance_status": record["status"], "local_decision_id": record["decision_id"],
            }, result, UUID(record["trace_id"]), create_approval=False)

    def _import_bootstrap(self, record: dict[str, Any]) -> None:
        tenant, _ = self._live_policy()
        assert self.database_url is not None
        records = [r for r in self.records() if r["operation_id"] == record["operation_id"]]
        with psycopg2.connect(self.database_url, connect_timeout=5) as connection, connection.cursor() as cur:
            cur.execute("SELECT set_config('app.tenant_id',%s,true)", (tenant,))
            self._record_rows(cur, records, tenant)
            rules = [{"rule_id": "managed-workspace-upgrade", "effect": "allow", "match": {
                "capabilities": ["workspace.maintenance.upgrade", "workspace.maintenance.configure_planning"], "risk_classes": ["high"],
                "side_effects": ["write_data"], "argument_equals": {"confirmed": True, "workspace_id": self.workspace_id}}}]
            cur.execute("""INSERT INTO policy.policy_sets(tenant_id,name,version,status,rules)
                VALUES(%s,%s,1,'active',%s) ON CONFLICT(tenant_id,name,version) DO NOTHING""",
                (tenant, f"managed-maintenance-{self.workspace_id}", Json(rules)))
            subject = f"managed-workspace-owner:{self.workspace_id}"
            cur.execute("""INSERT INTO iam.principals(tenant_id,kind,subject,display_name)
                VALUES(%s,'human',%s,'Local workspace owner')
                ON CONFLICT(tenant_id,subject) DO NOTHING""", (tenant, subject))
            cur.execute("SELECT id FROM iam.principals WHERE tenant_id=%s AND subject=%s", (tenant, subject))
            owner = cur.fetchone()
            assert owner is not None
        metadata = {"workspace_id": self.workspace_id, "tenant_id": tenant, "actor_id": str(owner[0])}
        owner_path = self.workspace / "workspace-owner.json"
        try:
            with owner_path.open("x", encoding="utf-8") as stream:
                json.dump(metadata, stream)
        except FileExistsError:
            if json.loads(owner_path.read_text(encoding="utf-8")) != metadata:
                raise ValueError("workspace owner metadata conflicts; preserve and inspect the workspace")


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("begin", "finish", "preview-planning", "enable-planning"))
    parser.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args()
    gateway = MaintenanceGateway(args.workspace, database_url=os.getenv("DATABASE_URL"))
    try:
        if args.operation == "preview-planning":
            print(json.dumps({"model_access": gateway.planning_preview()}))
            return 0
        payload = json.load(sys.stdin)
        before = len(gateway.records())
        if args.operation == "begin":
            record = gateway.begin(payload)
        elif args.operation == "enable-planning":
            record = gateway.configure_planning(payload)
        else:
            record = gateway.finish(payload["operation_id"], payload["status"])
        print(json.dumps({"record": record, "replayed": len(gateway.records()) == before}))
        return 0
    except (ValueError, PermissionError, OSError, psycopg2.Error):
        # Never print driver exception details or environment credentials.
        print("Local maintenance gateway rejected the operation; inspect maintenance/history.jsonl", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
