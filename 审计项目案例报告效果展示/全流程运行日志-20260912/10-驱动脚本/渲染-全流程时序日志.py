"""Render a human-readable, chronological trace of one composed run.

Sources merged into the timeline, so a reader can follow what the system
actually did without opening a debugger:

* ``control.node_attempts``  — per node: capability, plugin, status, start/finish,
  input bindings (which upstream artifact fed which port) and output refs;
* the staged artifacts under ``chains/<plan_key>/<node>/output-*.json`` — the
  plugin's real intermediate result;
* ``event.outbox`` — the per-node commit events;
* ``policy.decisions`` — the gateway verdicts for the run's trace.

Output is written under the archive only; nothing is deleted or overwritten
outside it.
"""
from __future__ import annotations

import csv
import hashlib
import json
import pathlib
from datetime import datetime

import psycopg2
from psycopg2.extras import RealDictCursor

REPO = pathlib.Path(r"D:\pythonpro\audit_network")
ARCHIVE = REPO / "docs" / "全流程运行日志-20260912"
STAGING = ARCHIVE / "12-本次运行产物"
RUN_ID = "416b0efc-2f18-4c9a-841f-0a1f4370c7c2"
DB = "postgresql://audit_app:admin@localhost:5432/audit_network"

DETAILS = ARCHIVE / "03-逐插件详情"
TIMELINE = ARCHIVE / "02-全流程时序日志" / "全流程时序日志.log"
STATES = ARCHIVE / "04-中间数据状态"
DBSNAP = ARCHIVE / "08-数据库快照"
FINAL = ARCHIVE / "05-最终结果"


def _ts(value: object) -> str:
    if not isinstance(value, datetime):
        return "—"
    return value.strftime("%H:%M:%S.%f")[:-3]


def _sha_of(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    for d in (DETAILS, STATES, DBSNAP, FINAL):
        d.mkdir(parents=True, exist_ok=True)

    conn = psycopg2.connect(DB)
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("select set_config('app.tenant_id',(select id::text from iam.tenants where slug='local-dev'),false)")
    cur.execute("select * from control.node_attempts where run_id=%s order by created_at, node_instance_id", (RUN_ID,))
    attempts = [dict(r) for r in cur.fetchall()]
    if not attempts:
        raise SystemExit(f"no attempts for run {RUN_ID} in {DB}")

    plan_key = attempts[0]["plan_key"]
    tids = sorted({str(a["trace_id"]) for a in attempts if a.get("trace_id")})
    cur.execute("select * from policy.tool_calls where trace_id = any(%s) order by requested_at", (tids,))
    tool_calls = [dict(r) for r in cur.fetchall()]
    cur.execute(
        """select d.* from policy.decisions d join policy.tool_calls t on t.id=d.tool_call_id
           where t.trace_id = any(%s) order by d.decided_at""",
        (tids,),
    )
    decisions = [dict(r) for r in cur.fetchall()]
    cur.execute("select * from event.outbox where aggregate_id = any(%s::uuid[]) order by id",
                ([RUN_ID] + [str(a["attempt_id"]) for a in attempts],))
    outbox = [dict(r) for r in cur.fetchall()]

    # ---- DB snapshot (raw rows, everything we queried) -----------------------
    (DBSNAP / f"run-{RUN_ID[:8]}-node_attempts.json").write_text(
        json.dumps(attempts, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    (DBSNAP / f"run-{RUN_ID[:8]}-tool_calls.json").write_text(
        json.dumps(tool_calls, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    (DBSNAP / f"run-{RUN_ID[:8]}-policy-decisions.json").write_text(
        json.dumps(decisions, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    (DBSNAP / f"run-{RUN_ID[:8]}-outbox-events.json").write_text(
        json.dumps(outbox, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    with open(DBSNAP / f"run-{RUN_ID[:8]}-node_attempts.csv", "w", newline="", encoding="utf-8-sig") as fh:
        cols = ["node_instance_id", "capability", "plugin_id", "status", "created_at", "finished_at",
                "error_kind", "error_message", "output_refs"]
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(attempts)

    # ---- timeline + per-plugin details --------------------------------------
    lines: list[str] = []
    add = lines.append
    n = len(attempts)
    total_ok = sum(1 for a in attempts if a["status"] == "succeeded")
    add("=" * 100)
    add(f"全流程时序日志   run_id={RUN_ID}")
    add(f"plan_key={plan_key}   节点={n}   成功={total_ok}   失败={n - total_ok}")
    add(f"trace_id={tids[0] if tids else '—'}")
    add("=" * 100)
    add("")
    add("【阅读说明】每个节点一块；「输入」列出该插件实际收到的数据从哪来、多大、sha256 前 16 位；")
    add("          「输出」是该插件真正产出的中间数据（完整 JSON 见 03-逐插件详情/）；")
    add("          「状态」是 pending→running→succeeded 的落库事实。")
    add("")

    states_index: list[dict] = []
    order = sorted(attempts, key=lambda a: (a["created_at"], a["node_instance_id"]))
    for i, a in enumerate(order, 1):
        node = a["node_instance_id"]
        cap = a["capability"]
        node_dir = STAGING / "chains" / plan_key / node
        outputs = {}
        for p in sorted(node_dir.glob("output-*.json")):
            port = p.name[len("output-"):].rsplit("-", 1)[0]
            try:
                outputs[port] = json.loads(p.read_text(encoding="utf-8-sig"))
            except Exception as exc:  # keep going, record the failure honestly
                outputs[port] = {"__unreadable__": str(exc)}
        detail_dir = DETAILS / f"{i:03d}-{node}"
        detail_dir.mkdir(parents=True, exist_ok=True)

        bindings = a.get("input_bindings") or {}
        (detail_dir / "输入.json").write_text(
            json.dumps({"node_instance_id": node, "capability": cap,
                        "input_bindings": bindings}, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8")
        (detail_dir / "输出.json").write_text(
            json.dumps(outputs, ensure_ascii=False, indent=2), encoding="utf-8")

        add(f"{'─' * 100}")
        add(f"[{i:>3}/{n}] {node}   能力={cap}   插件={a['plugin_id']}")
        add(f"        开始 {_ts(a['created_at'])}   结束 {_ts(a['finished_at'])}   状态 {a['status']}")
        if bindings:
            for port_id, items in (bindings.items() if isinstance(bindings, dict) else []):
                for it in (items if isinstance(items, list) else [items]):
                    src = it.get("source_instance") if isinstance(it, dict) else None
                    sha = str(it.get("sha256") or "")[:16] if isinstance(it, dict) else ""
                    add(f"        输入 {port_id} ← {src}:{it.get('source_port') if isinstance(it, dict) else ''}"
                        f"  sha256={sha}…")
        else:
            add("        输入 (无入边与 seed，插件自行生成)")
        for port, payload in outputs.items():
            size = len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
            summary = ""
            if isinstance(payload, dict):
                for key in ("summary", "status", "promotion", "recommendation"):
                    if key in payload:
                        summary += f" {key}={json.dumps(payload[key], ensure_ascii=False)[:120]}"
            add(f"        输出 {port}  {size}B{summary}")
            states_index.append({"step": i, "node": node, "capability": cap, "port": port})
            (STATES / f"{i:03d}-{node}-{port}.json").write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        if a.get("error_message"):
            add(f"        错误 [{a.get('error_kind')}] {a['error_message']}")
        (detail_dir / "状态流转.txt").write_text(
            "\n".join([
                f"节点        {node}",
                f"能力        {cap}",
                f"插件        {a['plugin_id']}",
                f"attempt_seq {a['attempt_seq']}",
                f"worker       {a.get('worker_id')}",
                f"创建(pending) {a['created_at']}",
                f"终结(finished) {a['finished_at']}",
                f"最终状态      {a['status']}",
                f"error_kind    {a.get('error_kind')}",
                f"error_message {a.get('error_message')}",
                f"idempotency  {a.get('idempotency_key')}",
                "",
                "说明：pending→running→succeeded 的中间态由 AttemptStore 的租约短事务维护；",
                "此处记录的是该 attempt 落库后的最终事实。",
            ]), encoding="utf-8")

    add(f"{'─' * 100}")
    add("")
    add("【策略裁决】")
    if decisions:
        for d in decisions:
            add(f"  {_ts(d['decided_at'])}  {d['decision']:<10} risk_score={d['risk_score']}  {d['reason']}")
    else:
        add("  (本次运行的策略裁决由内存 PolicyEngine 在编排前给出，未逐节点落 policy.decisions)")
    add("")
    add("【事务事件 event.outbox】")
    for e in outbox:
        add(f"  {_ts(e['occurred_at'])}  {e['event_type']:<22} aggregate={str(e['aggregate_id'])[:8]}")
    add("")
    add("【汇总】")
    add(f"  节点总数 {n}    成功 {total_ok}    失败 {n - total_ok}")
    add(f"  产出中间数据文件 {len(states_index)} 个（见 04-中间数据状态/）")
    add(f"  逐插件详情目录   {n} 个（见 03-逐插件详情/）")
    TIMELINE.write_text("\n".join(lines), encoding="utf-8")
    print(f"时序日志: {TIMELINE}  ({len(lines)} 行)")
    print(f"逐插件详情: {n} 个目录")
    print(f"中间数据状态: {len(states_index)} 个文件")
    print(f"数据库快照: {DBSNAP}")
    conn.close()


if __name__ == "__main__":
    main()
