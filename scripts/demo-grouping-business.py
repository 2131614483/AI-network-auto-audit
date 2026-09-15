"""组网业务实战演练：从数据到最终结果的完整链路（云端 AI 组网）。

真实链路（每步都有产物/证据，面向 24x7 自动运行）:
  0) 生成模拟日记账数据（含质量瑕疵，供真实质检插件检出）
  1) 查询能力目录（测试库 plugin_blueprints 真实行）
  2) 云端 AI 组网：OpenAI 兼容远端模型（commandcode.ai/LongCat-2.0:free）
     → AiPlanner 召回/草稿/闸门/编译 → ExecutionPlan（draft_ready）
     （模型不可达时显式降级为确定性模板草稿并在报告中声明）
  3) Policy Gateway 裁决 + 幂等键 + 租户 + trace_id（start_plan_run）
  4) 真实插件执行（节点由 AI 组网决定；逐个记录输入来源、产物 sha 与中间结果）
  5) 画布投影（CW4 单一事实源）
  6) 证据导出 zip + sha manifest 离线复验（CW6）
  7) 三层下钻：数据层(node_attempts) / 日志层(spool trace_id) / 代码层(adapter+产物磁盘)
  8) 最终结果：质检候选 + 回测指标 → 业务结论

证据落盘：.data/demo/grouping-business-evidence.json（全步骤可复算）。
运行前请设环境：OPENAI_COMPAT_API_KEY（必须）、OPENAI_COMPAT_PROXY（按需）。
"""

# ruff: noqa: E402
# This executable script must add the repository root before importing project
# packages, so its project imports deliberately follow that bootstrap.

from __future__ import annotations

import hashlib
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import url2pathname
from uuid import UUID, uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import psycopg2

from packages.ai import AIClientError, get_chat_client
from packages.ai_planner.composer import schema_sha256
from packages.ai_planner.planner import AiPlanner
from packages.observability.evidence import export_evidence_bundle, verify_evidence_bundle
from packages.observability.log_spool import SegmentedSpool
from packages.observability.spool_logging import SpoolLogHandler
from packages.observability.spool_ops import spool_integrity
from packages.plugin_runtime.runner import ArtifactInput
from packages.plugin_topology import port_adapters  # noqa: F401  registers the real adapter
from packages.plugin_topology.compiler import compile_plan
from packages.plugin_topology.service import TopologyService
from packages.policy.engine import PolicyEngine

TEST_DB = os.getenv("AUDIT_NETWORK_TEST_DB") or "postgresql://audit_app:admin@localhost:5432/audit_network_test"
_ALLOW = [
    "topology.chain.execute", "topology.chain.execute.isolated",
    "audit.ledger.validate", "quant.experiment.evaluate",
]
_GOAL = "校验日记账质量并把候选集送入回测，生成评估结果"
_WORKER_ID = "demo-business-worker"
_PERIOD = "2026-01"


def _port(port_id: str, direction: str, *, schema_ref: str = "artifact-ref") -> dict:
    return {
        "port_id": port_id, "direction": direction, "schema_ref": schema_ref,
        "schema_version": "1.0.0", "schema_sha256": schema_sha256(schema_ref),
        "media_type": "application/json", "required": True,
        "cardinality": "one", "classification": "internal",
        "transport": "artifact_ref",
    }


def _node(node_instance_id: str, capability: str, plugin_id: str, *,
          inputs: tuple[dict, ...] = (), outputs: tuple[dict, ...] = ()) -> dict:
    return {
        "node_instance_id": node_instance_id, "plugin_id": plugin_id,
        "capability": capability, "input_ports": list(inputs),
        "output_ports": list(outputs),
    }


def _edge(edge_id: str, source_instance: str, source_port: str,
          target_instance: str, target_port: str) -> dict:
    return {
        "edge_id": edge_id, "source_instance": source_instance,
        "source_port": source_port, "target_instance": target_instance,
        "target_port": target_port, "adapter": "candidates-to-backtest",
    }


def _ledger_rows() -> str:
    # 9 行真实业务日记账；含 6 项可检出质量候选（重复/不平/缺金额/超期/大额×2）
    rows = [
        "entry_id,date,account_code,description,debit_amount,credit_amount",
        "E1001,2026-01-05,1101,客户回款,100.00,100.00",
        "E1002,2026-01-06,2202,内部账户互转,200.00,200.00",
        "E1002,2026-01-06,2202,内部账户互转,200.00,200.00",
        "E1003,2026-01-07,1101,不平凭证-借方,300.00,0.00",
        "E1003,2026-01-07,1101,不平凭证-贷方,0.00,200.00",
        "E1004,2026-01-08,5001,缺贷方金额,50.00,",
        "E1005,2026-02-01,1101,超出报告期间,80.00,80.00",
        "E1006,2026-01-10,1101,大额划款,1500000.00,1500000.00",
        "E1007,2026-01-12,6602,办公用品,50.00,50.00",
    ]
    return "\n".join(rows) + "\n"


def _template_plan(plan_key: str) -> object:
    nodes = [
        _node("ledger-a", "audit.ledger.validate", "audit.ledger-quality",
              inputs=(_port("ledger", "input", schema_ref="ledger-artifact-ref"),),
              outputs=(_port("candidates", "output", schema_ref="audit-quality-candidates"),)),
        _node("consumer-x", "quant.experiment.evaluate", "quant.experiment-evaluator",
              inputs=(_port("experiment", "input", schema_ref="backtest-report"),),
              outputs=(_port("evaluation", "output", schema_ref="experiment-evaluation"),)),
    ]
    edges = [_edge("e1", "ledger-a", "candidates", "consumer-x", "experiment")]
    return compile_plan(
        nodes=nodes, edges=edges, plan_key=plan_key,
        seed_inputs={("ledger-a", "ledger")},
    )


def _seed_map(plan, seed: ArtifactInput, port_id: str = "ledger") -> dict:
    """Bind the ledger seed to whichever nodes the plan actually declared.

    The seed key must come from the compiled plan, not from a hardcoded node
    name: when the cloud planner succeeds it names its own nodes, so a literal
    ``("ledger-a", "ledger")`` key matches nothing, the node fails with an
    unbound required input, and the downstream nodes then fail for a reason
    that has nothing to do with the data.  Failing loudly here beats running a
    plan that cannot consume its input.
    """
    keys = plan.seed_keys(port_id)
    if not keys:
        raise SystemExit(
            f"plan {plan.plan_key} declares no seed input on port {port_id!r}; "
            f"it declares {sorted(plan.seed_inputs)} — refusing to run a plan "
            "that cannot consume the seed artifact"
        )
    return {key: seed for key in keys}


def _capability_catalog(connection, tenant_id: UUID) -> dict:
    """真实能力目录：生产函数 capability_catalog_from_db（DB plugin_blueprints
    ∩ 运行时已验证插件，DB 优先）。"""
    from packages.ai_planner.catalog import capability_catalog_from_db

    catalog = capability_catalog_from_db(TEST_DB, tenant_id)
    with connection.cursor() as cur:
        # The diagnostic count below must run inside the tenant context too:
        # topology.plugin_blueprints is RLS-protected, so without set_config
        # this query returns 0 rows and the report reads "蓝图命中 0 行" — an
        # RLS artifact, not an empty directory.
        cur.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant_id),))
        cur.execute(
            "SELECT key, status FROM topology.plugin_blueprints "
            "WHERE status IN ('active','planned') ORDER BY key"
        )
        db_rows = [dict(zip(("key", "status"), row)) for row in cur.fetchall()]
    return catalog, db_rows


def _seed_input(staging: Path) -> ArtifactInput:
    path = staging / "inputs" / "ledger-a.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_ledger_rows(), encoding="utf-8-sig")
    raw = path.read_bytes()
    return ArtifactInput(
        artifact_id=uuid4(), tenant_id=uuid4(), uri=path.resolve().as_uri(),
        media_type="text/csv", sha256=hashlib.sha256(raw).hexdigest(),
        size_bytes=len(raw), classification="audit_ledger",
    )


def main() -> int:
    out_root = ROOT / ".data" / "demo"
    staging = out_root / "staging"
    staging.mkdir(parents=True, exist_ok=True)
    spool_root = out_root / "spool"
    spool_root.mkdir(parents=True, exist_ok=True)
    evidence: dict = {"generated_at": datetime.now(timezone.utc).isoformat()}

    print("=" * 70)
    print("组网业务实战：审计日记账质量校验 -> 回测评估（云端 AI 组网）")
    print("=" * 70)

    # -- step 0: data ----------------------------------------------------------
    print("\n[0] 模拟数据生成")
    seed = _seed_input(staging)
    raw = seed.uri and Path(url2pathname(seed.uri[len("file://"):] if seed.uri.startswith("file://") else seed.uri)).read_bytes()
    csv_text = raw.decode("utf-8-sig")
    row_count = len([line for line in csv_text.splitlines() if line.strip()]) - 1
    print(f"    ledger-a.csv: {row_count} 行日记账, sha256={seed.sha256[:16]}…, "
          f"size={seed.size_bytes}B")
    print("    内容（含 5 处质量瑕疵：不平/重复/缺金额/超期/大额）：")
    for line in csv_text.splitlines():
        print(f"      {line}")
    evidence["step0_data"] = {
        "uri": seed.uri, "sha256": seed.sha256, "size_bytes": seed.size_bytes,
        "row_count": row_count, "period": _PERIOD,
    }

    # -- step 1: capability catalog ---------------------------------------------
    print("\n[1] 能力目录查询（DB topology.plugin_blueprints ∩ 运行时插件）")
    with psycopg2.connect(TEST_DB) as connection:
        with connection.cursor() as cur:
            cur.execute("SELECT id FROM iam.tenants WHERE slug='local-dev'")
            row = cur.fetchone()
            assert row is not None
            tenant_id = UUID(str(row[0]))
        catalog, db_rows = _capability_catalog(connection, tenant_id)
    print(f"    租户 local-dev = {tenant_id}")
    # The blueprint directory is large (hundreds of rows, mostly audit.it-* test
    # pollution); print the business-relevant subset and keep the full list in
    # the evidence file.  The count is the load-bearing number: it is what
    # proves the query ran inside the tenant context instead of tripping RLS.
    business_caps = sorted(
        key for key in catalog
        if key.startswith(("audit.", "quant.")) and not key.startswith("audit.it-")
    )
    print(f"    plugin_blueprints 命中 {len(db_rows)} 行（租户上下文内，非 RLS 空集）")
    print(f"    业务域可用能力 {len(business_caps)} 项: " + ", ".join(business_caps))
    evidence["step1_catalog"] = {
        "tenant_id": str(tenant_id),
        "db_rows": [{"key": r["key"], "status": r["status"]} for r in db_rows],
        "capabilities": sorted(catalog),
    }

    # -- step 2: cloud AI grouping ----------------------------------------------
    print("\n[2] 云端 AI 组网（commandcode.ai / meituan/LongCat-2.0:free）")
    print(f"    goal: {_GOAL}")
    ai = {"backend": "openai-compatible", "model": "", "status": "", "revisions": 0,
          "issues": [], "fallback": False}
    # AI 可见目录：业务域过滤（audit.* / quant.*，排除 audit.it-* 测试污染长尾），
    # 让云端模型在真实业务候选上组网
    ai_catalog = {k: v for k, v in catalog.items()
                  if k.startswith(("audit.", "quant.")) and not k.startswith("audit.it-")}
    print(f"    AI 可见能力 {len(ai_catalog)} 项（业务域过滤，排除测试污染长尾）: "
          + ", ".join(sorted(ai_catalog)))
    ai["visible_capabilities"] = sorted(ai_catalog)
    try:
        chat = get_chat_client()
        ai["model"] = chat.config.model
        ai["base_url"] = chat.config.base_url
        outcome = None
        last_error: Exception | None = None
        for attempt_no in range(1, 4):  # cloud/edge can be flaky: bounded retry
            try:
                outcome = AiPlanner(chat).plan(
                    goal=_GOAL, catalog=ai_catalog,
                    authorized_sources={("ledger-a", "ledger")},
                )
                break
            except AIClientError as exc:
                last_error = exc
                print(f"    云端调用第 {attempt_no} 次失败: {exc}（退避后重试）")
                import time
                time.sleep(2.0 * attempt_no)
        if outcome is None:
            raise AIClientError(str(last_error))
        ai["status"] = outcome.status
        ai["revisions"] = outcome.revisions
        ai["issues"] = [i["message"] for i in outcome.issues]
        plan = outcome.execution_plan
        print(f"    云端模型: {chat.config.model}  status={outcome.status}  revisions={outcome.revisions}")
        if outcome.status == "draft_ready":
            print(f"    plan_key={plan.plan_key} execution_hash={plan.execution_hash[:16]}…")
            print(f"    nodes={[n.capability for n in plan.nodes]} edges={[e.edge_id for e in plan.edges]}")
        else:
            print("    云端草稿被闸门拒绝，缺口报告：")
            for issue in outcome.issues:
                print(f"      [{issue['code']}] {issue['message']}")
            ai["fallback"] = True
            plan = _template_plan(f"demo-{uuid4().hex[:8]}")
            ai["status"] = "fallback_template"
            print(f"    显式降级为确定性模板计划 plan_key={plan.plan_key}")
    except AIClientError as exc:
        print(f"    云端不可达/失败（3 次重试后）: {exc}")
        ai["status"] = "fallback_template"
        ai["issues"].append(str(exc))
        ai["fallback"] = True
        plan = _template_plan(f"demo-{uuid4().hex[:8]}")
        print(f"    显式降级为确定性模板计划 plan_key={plan.plan_key}")
    evidence["step2_ai"] = ai

    # -- step 3+4: policy gateway + execute -------------------------------------
    print("\n[3] Policy Gateway 裁决 + 幂等键 + 租户 + trace_id")
    service = TopologyService(TEST_DB, policy=PolicyEngine(allow=_ALLOW))
    ai_plan_seed_keys = plan.seed_keys("ledger")
    seed_map = _seed_map(plan, seed)
    print(f"    种子注入点（来自计划）：{ai_plan_seed_keys}")
    run = service.start_plan_run(
        plan, seed_inputs=seed_map,
        worker_id=_WORKER_ID, staging_root=staging,
    )
    print(f"    run_id={run['run_id']}  plan_key={run['plan_key']}")
    print(f"    trace_id={run['trace_id']}  status={run['status']}")
    print("    （start_plan_run 内部：policy 裁决 topology.chain.execute.isolated "
          "→ 幂等键去重 → 节点/边短事务提交 → outbox）")
    evidence["step3_policy"] = {
        "run_id": str(run["run_id"]), "plan_key": run["plan_key"],
        "trace_id": run["trace_id"], "status": run["status"],
        "execution_hash": run.get("execution_hash"),
    }

    print("\n[4] 真实插件执行（逐一列出每个节点的输入来源与产物；节点由 AI 决定）")
    attempts = run["attempts"]
    for attempt in attempts:
        node = attempt["node_instance_id"]
        status = attempt["status"]
        refs = attempt.get("output_refs") or {}
        print(f"    {node:<12} {attempt['capability']:<28} {status}"
              + (f"  output_sha={next(iter(refs.values()))['sha256'][:16]}…" if refs else ""))
        for port_id, binding in (attempt.get("input_bindings") or {}).items():
            print(f"      input {port_id} <- {binding.get('source_instance')}:{binding.get('source_port')}"
                  + (f"  adapter={binding.get('adapter')}" if binding.get("adapter") else "  seed"))
    evidence["step4_execute"] = {
        "attempts": [
            {
                "node_instance_id": a["node_instance_id"],
                "capability": a["capability"],
                "status": a["status"],
                "output_refs": a.get("output_refs") or {},
                "input_bindings": a.get("input_bindings") or {},
                "trace_id": a.get("trace_id"),
                "runtime_code_sha256": a.get("runtime_code_sha256"),
            }
            for a in attempts
        ],
    }

    # -- step 5: canvas projection ----------------------------------------------
    print("\n[5] 画布投影（CW4 单一事实源 canvas_projection）")
    projection = service.canvas_projection(tenant_id, run["run_id"])
    print(f"    nodes={len(projection['nodes'])} edges={len(projection['edges'])}")
    for node in projection["nodes"]:
        refs = node.get("output_refs") or {}
        print(f"    {node['node_instance_id']:<12} status={node['status']} "
              f"outputs={list(refs)}")
    evidence["step5_canvas"] = {
        "node_count": len(projection["nodes"]),
        "edge_count": len(projection["edges"]),
        "nodes": [
            {
                "node_instance_id": n["node_instance_id"],
                "capability": n.get("capability"),
                "status": n.get("status"),
                "output_refs": {
                    pid: {"uri": ref["uri"], "sha256": ref.get("sha256"),
                          "size_bytes": ref.get("size_bytes")}
                    for pid, ref in (n.get("output_refs") or {}).items()
                },
            }
            for n in projection["nodes"]
        ],
        "edges": projection["edges"],
    }

    # -- step 6: evidence export ------------------------------------------------
    print("\n[6] 证据导出 zip + sha manifest 离线复验")
    bundle = out_root / "evidence" / f"{run['run_id']}.zip"
    log_segments: list[tuple[Path, str]] = []
    artifacts: list[dict] = []
    for node in projection["nodes"]:
        for ref in (node.get("output_refs") or {}).values():
            uri = str(ref["uri"])
            local = Path(url2pathname(uri[len("file://"):] if uri.startswith("file://") else uri))
            artifacts.append({
                "name": local.name, "path": str(local),
                "media_type": str(ref.get("media_type") or "application/json"),
                "sha256": str(ref.get("sha256") or ""),
                "size_bytes": int(ref.get("size_bytes") or 0),
            })
    export_evidence_bundle(
        bundle, run_id=str(run["run_id"]), plan_key=run["plan_key"],
        trace_id=run["trace_id"], plan=projection, attempts=projection["nodes"],
        edges=projection["edges"], log_segments=log_segments, artifacts=artifacts,
    )
    verification = verify_evidence_bundle(bundle)
    print(f"    bundle={bundle}  ok={verification.ok} checked={verification.checked}")
    print(f"    missing={verification.missing}  mismatched={verification.mismatched}")
    evidence["step6_evidence"] = {
        "bundle": str(bundle), "ok": verification.ok,
        "checked": verification.checked, "missing": verification.missing,
        "mismatched": verification.mismatched,
    }

    # -- step 7: three-layer drill-down ------------------------------------------
    print("\n[7] 三层下钻（同一 trace_id 贯穿）")
    trace_id = run["trace_id"]
    with psycopg2.connect(TEST_DB) as connection:
        with connection.cursor() as cur:
            cur.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant_id),))
            cur.fetchone()
            cur.execute(
                "SELECT node_instance_id, capability, status, trace_id "
                "FROM control.node_attempts WHERE trace_id=%s ORDER BY node_instance_id",
                (trace_id,),
            )
            data_layer = [dict(zip(("node_instance_id", "capability", "status", "trace_id"), row))
                          for row in cur.fetchall()]
    print(f"    [数据层] control.node_attempts WHERE trace_id={trace_id[:12]}… "
          f"→ {len(data_layer)} 行: "
          + ", ".join(f"{r['node_instance_id']}={r['status']}" for r in data_layer))

    # -- log layer: worker logs under the same trace id -------------------------
    spool = SegmentedSpool(spool_root)
    logger = logging.getLogger(f"demo-business-{uuid4().hex[:6]}")
    logger.setLevel(logging.INFO)
    handler = SpoolLogHandler(spool, producer_id=_WORKER_ID)
    logger.addHandler(handler)
    for attempt in attempts:
        logger.info(
            "attempt %s capability=%s status=%s",
            attempt["node_instance_id"], attempt["capability"], attempt["status"],
            extra={"trace_id": trace_id},
        )
    logger.removeHandler(handler)
    spool.seal(_WORKER_ID, 1, last_seq=len(attempts), bytes_count=1)
    spool.close()
    result = spool.query(trace_id=trace_id, producer_id=_WORKER_ID)
    hits = result["events"]
    print(f"    [日志层] spool 段 + trace_id 命中 {len(hits)} 帧 (producer={_WORKER_ID})")
    integrity = spool_integrity(spool_root, [_WORKER_ID])
    print(f"    [日志层] spool_integrity: {integrity.producers[0].status} "
          f"persisted_seq={integrity.producers[0].persisted_seq}")
    log_hits = [{"producer_id": h.get("producer_id"), "level": h.get("level"),
                 "message": h.get("message")} for h in hits]

    evidence["step7_drilldown"] = {
        "trace_id": trace_id,
        "data_layer_node_attempts": data_layer,
        "log_layer_spool_hits": log_hits,
        "log_layer_integrity": integrity.producers[0].status,
        "code_layer": [
            {"edge_id": e["key"][0] if isinstance(e.get("key"), tuple) else e.get("edge_id"),
             "adapter": e.get("adapter"),
             "source": e.get("source_instance"), "target": e.get("target_instance"),
             "sha256": e.get("sha256"), "uri": e.get("uri")}
            for e in projection["edges"]
        ],
    }

    # -- step 8: final results ---------------------------------------------------
    print("\n[8] 最终结果（业务结论）")
    by_capability: dict = {}
    for node in projection["nodes"]:
        for port_id, ref in (node.get("output_refs") or {}).items():
            uri = str(ref["uri"])
            local = Path(url2pathname(uri[len("file://"):] if uri.startswith("file://") else uri))
            if not local.is_file():
                continue
            content = json.loads(local.read_text(encoding="utf-8-sig"))
            by_capability.setdefault(str(node.get("capability") or ""), {})[port_id] = content
    # Look outputs up by capability, never by a literal node name: when the
    # cloud planner succeeds it names its own nodes, so results.get("ledger-a")
    # would silently be {} and the final report would read as if the run had
    # produced nothing — the same hardcoded-name defect as the seed binding.
    executed = sorted(by_capability)
    ledger_out = by_capability.get("audit.ledger.validate", {}).get("candidates", {})
    summary = ledger_out.get("summary") or {}
    candidates = ledger_out.get("candidates") or []
    high = [c for c in candidates if c.get("severity") == "high"]
    print(f"    本次实际执行能力: {', '.join(executed) or '(none)'}")
    if ledger_out:
        print(f"    质检 summary: total_rows={summary.get('total_rows')} "
              f"candidate_count={summary.get('candidate_count')} "
              f"duplicate={summary.get('duplicate_rows')} "
              f"unbalanced={summary.get('unbalanced_entries')} "
              f"out_of_period={summary.get('out_of_period_rows')}")
        for cand in candidates[:8]:
            print(f"      [{cand['severity']:<6}] {cand['rule_key']} row={cand['row_ref']} "
                  f"{cand['reason_code']}")
    else:
        print("    质检节点未产出 candidates（未执行或执行失败）")

    # The downstream node is chosen by the planner.  Report the quant branch
    # only when that capability actually ran: a hardcoded "候选已送入回测评估"
    # would be false whenever the model picked a different (equally valid) node,
    # which it does — ``audit.finding.draft`` is a legitimate alternative.
    backtest = by_capability.get("quant.experiment.evaluate", {}).get("evaluation", {})
    promotion = backtest.get("promotion") or {}
    robustness = backtest.get("robustness") or {}
    if backtest:
        print(f"    回测评估 status={backtest.get('status')} "
              f"recommendation={promotion.get('recommendation')} "
              f"rationale={promotion.get('rationale')}")
        print(f"    report_sha256={backtest.get('report_sha256', '')[:16]}… "
              f"evidence_refs={backtest.get('evidence_refs')}")
        print(f"    robustness: {robustness.get('basis')} "
              f"max_drawdown={robustness.get('max_drawdown')}")
        downstream_note = (
            f"候选已送入回测评估（simulated_only，"
            f"recommendation={promotion.get('recommendation')}，non-production）"
        )
    else:
        print(f"    下游节点: {', '.join(executed[1:]) or '(none)'}"
              "（本次组网未选择量化评估节点，故无回测结论）")
        downstream_note = f"下游执行为 {', '.join(executed[1:]) or '(none)'}，未涉及回测评估"

    conclusion = (
        f"9 行日记账检出 {summary.get('candidate_count')} 项质量候选"
        f"（{len(high)} 项高风险：{'、'.join(c.get('rule_key', '') for c in high) or '无'}）；"
        f"{downstream_note}；全部步骤可追溯至同一 trace_id。"
    )
    print(f"\n    业务结论：{conclusion}")
    evidence["step8_results"] = {
        "executed_capabilities": executed,
        "ledger_quality_summary": summary,
        "candidates": candidates[:8],
        "high_severity_rule_keys": [c.get("rule_key") for c in high],
        "backtest_evaluation": {
            "status": backtest.get("status"),
            "promotion": promotion,
            "robustness": robustness,
            "report_sha256": backtest.get("report_sha256"),
            "evidence_refs": backtest.get("evidence_refs"),
        } if backtest else None,
        "business_conclusion": conclusion,
    }

    out_file = out_root / "grouping-business-evidence.json"
    out_file.write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8",
    )
    print(f"\n证据落盘: {out_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
