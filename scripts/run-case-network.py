# -*- coding: utf-8 -*-
"""把三份审计案例数据集接进项目组网（真实执行 → 运行账本 → 证据包）。

**为什么做这件事**：三份案例（对照 / 实验组 / 高难度）此前只有**案例自带的确定性脚本**能跑；
产品自身的组网链路（`compile_plan` → 策略网关 → `start_plan_run` → 隔离运行时 → 证据包）
从没吃过真实案例数据。于是这三份数据只是"案例的资产"，不是"产品的测试资产"。

链路：
  1) 适配  案例 `记账凭证_*.csv` → 插件契约要求的 6 列
  2) 组网  编译两节点计划（audit.ledger.validate → quant.experiment.evaluate）
  3) 执行  PolicyGateway 裁决 → `start_plan_run`（隔离运行时，读取根 = staging）
  4) 复验  证据包导出 + sha256 离线复算

**映射里两个必须做对的决定**（错一个，结果就没意义）：
  * `entry_id` 取**凭证号**而非行号 —— 借贷平衡规则按 `entry_id` **分组求和**，
    一行一个 id 会让每一行都"不平"，235 条分录会刷出上百条假候选；
  * 借贷金额的**空白填 0.00** —— 借方分录没有贷方金额是正常记账，留空会被
    `missing_or_invalid_amount` 判为缺金额，同样刷一半假候选。

**口径**：插件的 `out_of_period` 按 `date[:7] != period` 判定，即**一份台账 = 一个月份**。
案例是全年凭证，所以这里**按月拆分**、每月一次组网，而不是把一年塞进一个月。

只读案例目录；派生数据与产物全部落在 `.data/case-network/` 下，不改案例一个字节。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from urllib.request import url2pathname
from uuid import UUID, uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from packages.ai_planner.composer import schema_sha256  # noqa: E402
from packages.ops.networking_quality import assess_plan  # noqa: E402
from packages.plugin_runtime.runner import ArtifactInput  # noqa: E402
from packages.plugin_topology import port_adapters  # noqa: E402,F401  # 注册真实适配器
from packages.plugin_topology.compiler import compile_plan  # noqa: E402
from packages.plugin_topology.service import TopologyService  # noqa: E402
from packages.policy.engine import PolicyEngine  # noqa: E402

CASE_ROOT = ROOT / "审计项目案例报告效果展示"
OUT_ROOT = ROOT / ".data" / "case-network"

#: (展示名, 案例目录名, slug)。slug 用于 plan_key —— 编译期门禁只接受
#: ``[A-Za-z0-9][A-Za-z0-9._-]*``，中文展示名直接进 plan_key 会被拒（这是防路径穿越的
#: 正确行为，不该绕过），所以另给一个 ASCII slug。
CASES: tuple[tuple[str, str, str], ...] = (
    ("对照-基础", "黔岭酒业2025年度财务报表审计", "control"),
    ("实验组", "黔岭酒业2025年度财务报表审计_实验组", "experiment"),
    ("高难度", "黔岭酒业2025年度财务报表审计_实验组_高难度", "hard"),
)

#: 插件契约要求的列 ← 案例记账凭证的列。前六个是插件的 REQUIRED_HEADERS_V1。
_COLUMNS: tuple[tuple[str, str], ...] = (
    ("entry_id", "凭证号"),
    ("date", "记账日期"),
    ("account_code", "科目编码"),
    ("description", "摘要"),
    ("debit_amount", "借方金额"),
    ("credit_amount", "贷方金额"),
)

_ALLOW = [
    "topology.chain.execute", "topology.chain.execute.isolated",
    "audit.ledger.validate", "quant.experiment.evaluate",
]
_WORKER_ID = "case-network-worker"
_GOAL = "校验这份月度记账凭证的质量，把质量候选集送入回测评估，生成评估结果"
DEFAULT_DB = "postgresql://audit_app:admin@localhost:5432/audit_network"


def _port(port_id: str, direction: str, *, schema_ref: str = "artifact-ref") -> dict:
    return {
        "port_id": port_id, "direction": direction, "schema_ref": schema_ref,
        "schema_version": "1.0.0", "schema_sha256": schema_sha256(schema_ref),
        "media_type": "application/json", "required": True,
        "cardinality": "one", "classification": "internal", "transport": "artifact_ref",
    }


def _node(node_instance_id: str, capability: str, plugin_id: str, *,
          inputs: tuple[dict, ...] = (), outputs: tuple[dict, ...] = (),
          parameters: dict | None = None) -> dict:
    node = {
        "node_instance_id": node_instance_id, "plugin_id": plugin_id,
        "capability": capability, "input_ports": list(inputs), "output_ports": list(outputs),
    }
    if parameters:
        # IRNode.parameters 进计划哈希，所以这里的覆盖是**可审计**的：
        # 计划 hash 会随阈值变化，事后能证明"那次运行用的是哪个阈值"。
        node["parameters"] = parameters
    return node


def _edge(edge_id: str, source_instance: str, source_port: str,
          target_instance: str, target_port: str) -> dict:
    return {
        "edge_id": edge_id, "source_instance": source_instance, "source_port": source_port,
        "target_instance": target_instance, "target_port": target_port,
        "adapter": "candidates-to-backtest",
    }


def _plan(plan_key: str, threshold: float | None = None):
    ledger_params = {"amount_threshold": threshold} if threshold is not None else None
    nodes = [
        _node("ledger-a", "audit.ledger.validate", "audit.ledger-quality",
              parameters=ledger_params,
              inputs=(_port("ledger", "input", schema_ref="ledger-artifact-ref"),),
              outputs=(_port("candidates", "output", schema_ref="audit-quality-candidates"),)),
        _node("consumer-x", "quant.experiment.evaluate", "quant.experiment-evaluator",
              inputs=(_port("experiment", "input", schema_ref="backtest-report"),),
              outputs=(_port("evaluation", "output", schema_ref="experiment-evaluation"),)),
    ]
    return compile_plan(
        nodes=nodes, edges=[_edge("e1", "ledger-a", "candidates", "consumer-x", "experiment")],
        plan_key=plan_key, seed_inputs={("ledger-a", "ledger")},
    )


def _ai_catalog(database_url: str, tenant_id: UUID) -> dict:
    """AI 可见的能力目录：真实目录 ∩ 业务域（排除 audit.it-* 测试污染长尾）。

    与 demo 同口径 —— 让模型在真实业务候选上组网，而不是在测试残留里乱挑。
    """
    from packages.ai_planner.catalog import capability_catalog_from_db, enrich_from_directory

    catalog = capability_catalog_from_db(database_url, tenant_id)
    visible = {k: v for k, v in catalog.items()
               if k.startswith(("audit.", "quant.")) and not k.startswith("audit.it-")}
    # 理解面：用插件目录补上 description/domains/lifecycle —— 模型此前只知道能力能接什么、
    # 能吐什么，不知道它是干什么的。目录里没有的不编造，保持留空。
    return enrich_from_directory(visible)


def _ai_plan(goal: str, catalog: dict, *, retries: int = 3):
    """云端模型组网：召回 → 草稿 → 校验 → 编译。失败不掩盖，退回模板计划并记明。"""
    import time

    from packages.ai import AIClientError, get_chat_client
    from packages.ai_planner.planner import AiPlanner

    chat = get_chat_client()
    info: dict = {"model": chat.config.model, "status": "", "revisions": 0,
                  "issues": [], "fallback": False}
    outcome = None
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            outcome = AiPlanner(chat).plan(
                goal=goal, catalog=catalog,
                authorized_sources={("ledger-a", "ledger")},
            )
            break
        except AIClientError as exc:
            last_error = exc
            print(f"      第 {attempt} 次调用失败：{exc}（退避重试）")
            time.sleep(2.0 * attempt)
    if outcome is None:
        # 不 SystemExit：一次瞬时网络抖动不该把整批测试打断。退回确定性模板计划继续跑数据，
        # 并把失败原因如实记进计划信息 —— 与 demo 的 fail-open 口径一致。
        info["fallback"] = True
        info["status"] = "model_unavailable"
        info["issues"] = [f"云端模型不可达：{last_error}"]
        return None, info
    info["status"] = outcome.status
    info["revisions"] = outcome.revisions
    info["issues"] = [i["message"] for i in outcome.issues]
    return outcome, info


def _seed_map(plan, seed: ArtifactInput) -> dict:
    """种子注入点必须从**编译后的计划**里取，不能硬编码节点名。

    计划里的节点名可能由调用方（或 AI）决定；用字面量 ``("ledger-a", "ledger")``
    去匹配，匹配不上时节点会因为"必填输入未绑定"而失败，后面的节点再连带失败 ——
    失败原因看上去像数据问题，其实与数据无关。这里匹配不上就直接报错。
    """
    keys = plan.seed_keys("ledger")
    if not keys:
        raise SystemExit(
            f"plan {plan.plan_key} 未声明 port 'ledger' 的种子注入点（声明的是 "
            f"{sorted(plan.seed_inputs)}）—— 拒绝运行一个吃不进输入的计划"
        )
    return {key: seed for key in keys}


def _tenant_id(database_url: str) -> UUID:
    """当前租户（案例数据与租户无关，但投影与账本按租户隔离，必须显式取）。"""
    import psycopg2

    with psycopg2.connect(database_url) as connection, connection.cursor() as cur:
        cur.execute("SELECT id FROM iam.tenants WHERE slug='local-dev'")
        row = cur.fetchone()
    if row is None:
        raise SystemExit("找不到租户 local-dev")
    return UUID(str(row[0]))


def _adapt_case(case_dir: Path, staging: Path) -> dict:
    """按**声明的映射**把案例资料变成契约工件（映射本体在 contracts/input-mappings.json）。

    这里刻意不再内联任何列名与口径：换一个数据集只需换一份声明，代码不动。
    """
    from packages.catalog.input_mapping import apply_mapping, mapping

    return apply_mapping(mapping("audit-ledger-csv-v1"), case_dir, staging / "inputs")


def _run_month(slug: str, month: str, info: dict, staging: Path, run_index: int,
               tenant_id: UUID, batch: str, threshold: float | None = None,
               ai: dict | None = None, goal: str = "") -> dict:
    seed = ArtifactInput(
        artifact_id=uuid4(), tenant_id=uuid4(), uri=info["path"].resolve().as_uri(),
        media_type=info["media_type"], sha256=info["sha256"], size_bytes=info["size_bytes"],
        classification=info["classification"], period=month,
    )
    # plan_key 每次都是新的：同一个 key 会被幂等键认成重复运行，第二次起不再执行。
    plan_info: dict = {"backend": "template"}
    if ai is not None:
        outcome, plan_info = _ai_plan(goal, ai)
        plan_info["backend"] = "cloud-ai" if outcome is not None else "template(fallback)"
        if outcome is not None and outcome.status == "draft_ready" and outcome.execution_plan is not None:
            candidate = outcome.execution_plan
            # 计划质量门：编译通过 ≠ 这份数据配得上这张网。不合格就不执行它。
            gate = assess_plan(candidate, seed_rows=info["rows"])
            plan_info["gate"] = gate
            if not gate["ok"]:
                plan_info["fallback"] = True
                plan_info["issues"] = [i["detail"] for i in gate["issues"]]
                plan = _plan(f"case-{slug}-{month}-{batch}-{run_index}", threshold)
            else:
                plan = candidate
                plan_info["capabilities"] = [n.capability for n in plan.nodes]
                plan_info["edges"] = len(plan.edges)
        else:
            # 草稿被闸门拒绝时如实记录，并用模板计划继续跑数据 —— 不假装 AI 成功。
            plan_info["fallback"] = True
            plan = _plan(f"case-{slug}-{month}-{batch}-{run_index}", threshold)
    else:
        plan = _plan(f"case-{slug}-{month}-{batch}-{run_index}", threshold)
    service = TopologyService(
        os.environ.get("AUDIT_NETWORK_TEST_DB", DEFAULT_DB),
        policy=PolicyEngine(allow=_ALLOW),
    )
    run = service.start_plan_run(
        plan, seed_inputs=_seed_map(plan, seed), worker_id=_WORKER_ID, staging_root=staging,
    )
    projection = service.canvas_projection(tenant_id, run["run_id"])
    return {"run": run, "projection": projection, "plan": plan_info}


def _candidates_of(projection: dict) -> dict[str, int]:
    """从 ledger 节点的产物里数候选按规则分布 —— 只读产物，不猜。"""
    counts: dict[str, int] = {}
    for node in projection.get("nodes") or []:
        refs = node.get("output_refs") or {}
        for ref in refs.values():
            uri = str(ref.get("uri") or "")
            if not uri.startswith("file://"):
                continue
            path = Path(url2pathname(uri[len("file://"):]))
            if not path.is_file():
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8-sig"))
            except (OSError, json.JSONDecodeError):
                continue
            for item in (payload.get("candidates") if isinstance(payload, dict) else None) or []:
                key = str(item.get("rule_key") or "unknown")
                counts[key] = counts.get(key, 0) + 1
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description="把审计案例数据集接进项目组网")
    parser.add_argument("--case", action="append", dest="cases", default=None,
                        help="只跑指定展示名（可重复）；缺省跑全部三份")
    parser.add_argument("--limit-months", type=int, default=None,
                        help="每份案例最多跑几个月份（调试用）")
    parser.add_argument("--threshold", type=float, default=None,
                        help="大额判定阈值（元）；缺省用声明值 contracts/capability-parameters.json")
    parser.add_argument("--batch", default=None,
                        help="批次标识，进 plan_key —— 同一份数据重复跑时必须区分，否则同名计划会被幂等键挡住")
    parser.add_argument("--ai", action="store_true",
                        help="让云端模型自己组网（缺省用确定性两节点模板计划）")
    args = parser.parse_args()

    wanted = set(args.cases or [])
    database_url = os.environ.get("AUDIT_NETWORK_TEST_DB", DEFAULT_DB)
    batch = args.batch or ("ai" if args.ai else "tmpl")
    tenant_id = _tenant_id(database_url)
    summary: list[dict] = []
    ai_catalog = _ai_catalog(database_url, tenant_id) if args.ai else None
    if args.ai:
        print(f"AI 可见能力目录：{len(ai_catalog)} 项（业务域过滤）")
    for label, dirname, slug in CASES:
        if wanted and label not in wanted:
            continue
        case_dir = CASE_ROOT / dirname
        if not case_dir.is_dir():
            print(f"[跳过] {label}: 目录不存在 {case_dir}")
            continue
        staging = OUT_ROOT / dirname / "staging"
        adapted = _adapt_case(case_dir, staging)
        months = sorted(adapted["artifacts"])
        if args.limit_months:
            months = months[-args.limit_months:]
        print(f"\n{'=' * 74}\n[{label}] {dirname}")
        print(f"  记账凭证 {adapted['total_rows']} 行 / 跳过 {adapted['skipped_rows']} 行"
              f" → 按声明映射拆成 {len(adapted['artifacts'])} 份台账，本次跑 {len(months)} 个月")
        for month in months:
            info = adapted["artifacts"][month]
            result = _run_month(slug, month, info, staging, len(summary), tenant_id,
                                batch, args.threshold, ai_catalog if args.ai else None, _GOAL)
            counts = _candidates_of(result["projection"])
            statuses = {n["node_instance_id"]: n["status"] for n in result["projection"]["nodes"]}
            plan_info = result.get("plan") or {}
            tag = (f"[{plan_info.get('backend')}] "
                   f"{plan_info.get('capabilities') or ''}" if plan_info.get("backend") != "template" else "")
            print(f"    {month}  {info['rows']:>3} 行/{info['groups']:>3} 组  "
                  f"run={str(result['run']['run_id'])[:8]}  {result['run']['status']}  "
                  f"节点={statuses}  候选={counts or '无'}  {tag}")
            if plan_info.get("fallback"):
                print(f"        ⚠ AI 草稿被闸门拒绝，已退回模板计划；缺口：{plan_info.get('issues')}")
            summary.append({
                "case": label, "period": month, "rows": info["rows"],
                "vouchers": info["groups"], "run_id": str(result["run"]["run_id"]),
                "status": result["run"]["status"], "nodes": statuses, "candidates": counts,
                "plan": plan_info,
            })

    print(f"\n{'=' * 74}\n汇总：{len(summary)} 次组网运行")
    total_candidates: dict[str, int] = {}
    for row in summary:
        for key, value in row["candidates"].items():
            total_candidates[key] = total_candidates.get(key, 0) + value
    print(f"  候选按规则：{total_candidates or '无'}")
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    (OUT_ROOT / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"  明细已写入 {OUT_ROOT / 'summary.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
