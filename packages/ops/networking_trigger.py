"""组网触发层：让"到点自己组一次网"成为可能（默认关）。

**为什么需要它**：此前全仓**没有任何代码引用规划或 `start_plan_run`** —— 每一次组网都由人
手写脚本触发（本项目今天的 37 次运行全是这么来的）。没有触发层，"自动组网"无从谈起。

**取舍**：

* **默认走确定性计划，不调云端模型。** 定时任务不该依赖外部模型可用；AI 组网是需要人
  在场判断的事（今天的实测：同一份数据，模型有时组 1 个节点、有时组 2 个，还会因输入不足
  让下游失败）。所以这里只做"按声明把数据接进来 + 跑固定两节点计划"，AI 路径仍由
  `scripts/run-case-network.py --ai` 手动触发。
* **默认关**（`AUDIT_NETWORK_AUTO_NETWORKING=1` 才开），照抄仓库既有的
  `AUDIT_NETWORK_DEMO_GENERATE` 那套：显式开启、有节流。
* **失败不扩散**：单次触发失败只记日志，绝不影响 worker 的 outbox 分发与任务执行。

触发条件与数据集写在 `contracts/networking-triggers.json`（与 `input-mappings.json` 同处，
同为人工审阅的声明）；上次运行时间记在 `.data/networking-triggers-state.json`。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from packages.ai_planner.composer import schema_sha256
from packages.paths import data_root
from packages.plugin_runtime.runner import ArtifactInput
from packages.plugin_topology import port_adapters  # noqa: F401  注册真实适配器
from packages.plugin_topology.compiler import compile_plan
from packages.plugin_topology.service import TopologyService
from packages.policy.engine import PolicyEngine

logger = logging.getLogger(__name__)

TRIGGERS_PATH = Path(__file__).resolve().parents[2] / "contracts" / "networking-triggers.json"
STATE_PATH = data_root() / "networking-triggers-state.json"
DEFAULT_DATABASE_URL = "postgresql://audit_app:admin@localhost:5432/audit_network"

_ALLOW = [
    "topology.chain.execute", "topology.chain.execute.isolated",
    "audit.ledger.validate", "quant.experiment.evaluate",
]


class TriggerError(ValueError):
    """声明不满足触发条件（不可猜）。"""


# -- 声明 ------------------------------------------------------------------


def load_triggers(path: Path | None = None) -> list[dict[str, Any]]:
    source = Path(path) if path is not None else TRIGGERS_PATH
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TriggerError(f"触发声明不可读：{source}") from exc
    triggers = raw.get("triggers")
    if not isinstance(triggers, list) or not triggers:
        raise TriggerError(f"触发声明里没有 triggers：{source}")
    for entry in triggers:
        for field in ("name", "dataset", "mapping", "every_minutes"):
            if field not in entry:
                raise TriggerError(f"触发声明缺少字段 {field!r}：{entry}")
        if float(entry["every_minutes"]) <= 0:
            raise TriggerError(f"every_minutes 必须为正：{entry['name']}")
    return triggers


def due_triggers(
    triggers: list[dict[str, Any]], last_run_at: dict[str, str], now: datetime
) -> list[dict[str, Any]]:
    """哪些触发器到期。**纯函数** —— 时间判断最容易写错，所以独立出来单测。

    * 从未跑过 → 到期；
    * 距上次运行已达 ``every_minutes`` → 到期；
    * 上次时间戳无法解析（人为改坏/半截写入）→ **按到期处理**：宁可多跑一次并留下记录，
      也不因为一条坏记录让触发永久停摆。
    """
    due: list[dict[str, Any]] = []
    for trigger in triggers:
        name = str(trigger["name"])
        previous = last_run_at.get(name)
        if not previous:
            due.append(trigger)
            continue
        try:
            last = datetime.fromisoformat(str(previous))
        except ValueError:
            logger.warning("trigger %s 的上次运行时间无法解析，按到期处理", name)
            due.append(trigger)
            continue
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        if now - last >= timedelta(minutes=float(trigger["every_minutes"])):
            due.append(trigger)
    return due


# -- 计划（脚本与触发器共用，避免两套实现）--------------------------------


def _port(port_id: str, direction: str, *, schema_ref: str = "artifact-ref") -> dict[str, Any]:
    return {
        "port_id": port_id, "direction": direction, "schema_ref": schema_ref,
        "schema_version": "1.0.0", "schema_sha256": schema_sha256(schema_ref),
        "media_type": "application/json", "required": True,
        "cardinality": "one", "classification": "internal", "transport": "artifact_ref",
    }


def _node(node_instance_id: str, capability: str, plugin_id: str, *,
          inputs: tuple[dict[str, Any], ...] = (), outputs: tuple[dict[str, Any], ...] = (),
          parameters: dict[str, Any] | None = None) -> dict[str, Any]:
    node: dict[str, Any] = {
        "node_instance_id": node_instance_id, "plugin_id": plugin_id,
        "capability": capability, "input_ports": list(inputs), "output_ports": list(outputs),
    }
    if parameters:
        # 进计划哈希 → 覆盖可审计
        node["parameters"] = parameters
    return node


def ledger_plan(plan_key: str, threshold: float | None = None) -> Any:
    """两节点台账计划：audit.ledger.validate → quant.experiment.evaluate。"""
    parameters = {"amount_threshold": threshold} if threshold is not None else None
    nodes = [
        _node("ledger-a", "audit.ledger.validate", "audit.ledger-quality",
              parameters=parameters,
              inputs=(_port("ledger", "input", schema_ref="ledger-artifact-ref"),),
              outputs=(_port("candidates", "output", schema_ref="audit-quality-candidates"),)),
        _node("consumer-x", "quant.experiment.evaluate", "quant.experiment-evaluator",
              inputs=(_port("experiment", "input", schema_ref="backtest-report"),),
              outputs=(_port("evaluation", "output", schema_ref="experiment-evaluation"),)),
    ]
    edges = [{
        "edge_id": "e1", "source_instance": "ledger-a", "source_port": "candidates",
        "target_instance": "consumer-x", "target_port": "experiment",
        "adapter": "candidates-to-backtest",
    }]
    return compile_plan(nodes=nodes, edges=edges, plan_key=plan_key,
                        seed_inputs={("ledger-a", "ledger")})


def _seed_map(plan: Any, seed: ArtifactInput) -> dict[Any, ArtifactInput]:
    keys = plan.seed_keys("ledger")
    if not keys:
        raise TriggerError(
            f"plan {plan.plan_key} 未声明 port 'ledger' 的种子注入点 —— 拒绝运行吃不进输入的计划"
        )
    return {key: seed for key in keys}


# -- 执行 ------------------------------------------------------------------


def run_trigger_once(trigger: dict[str, Any], *, database_url: str, project_root: Path,
                     now: datetime | None = None) -> dict[str, Any]:
    """跑一个触发器：声明映射 → 逐分区组网 → 汇总。**不调云端模型**（见模块 docstring）。"""
    from packages.catalog.input_mapping import apply_mapping, mapping

    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%d%H%M%S")
    dataset_root = project_root / str(trigger["dataset"])
    if not dataset_root.is_dir():
        raise TriggerError(f"数据集目录不存在：{dataset_root}")
    staging = data_root() / "case-network" / f"trigger-{trigger['name']}" / "staging"
    adapted = apply_mapping(mapping(str(trigger["mapping"])), dataset_root, staging / "inputs")
    # 租户由 start_plan_run 自己解析，这里不重复取。
    service = TopologyService(database_url, policy=PolicyEngine(allow=_ALLOW))

    runs: list[dict[str, Any]] = []
    for index, (period, info) in enumerate(sorted(adapted["artifacts"].items())):
        seed = ArtifactInput(
            artifact_id=uuid4(), tenant_id=uuid4(), uri=info["path"].resolve().as_uri(),
            media_type=info["media_type"], sha256=info["sha256"], size_bytes=info["size_bytes"],
            classification=info["classification"], period=period,
        )
        plan = ledger_plan(
            f"trigger-{trigger['name']}-{period.replace('-', '')}-{stamp}-{index}",
            trigger.get("amount_threshold"),
        )
        run = service.start_plan_run(
            plan, seed_inputs=_seed_map(plan, seed),
            worker_id=f"trigger-{trigger['name']}", staging_root=staging,
        )
        runs.append({"period": period, "run_id": str(run["run_id"]), "status": run["status"],
                     "rows": info["rows"]})
        logger.info("trigger %s: %s %s run=%s", trigger["name"], period, run["status"],
                    str(run["run_id"])[:8])
    return {"name": trigger["name"], "dataset": trigger["dataset"],
            "source_rows": adapted["total_rows"], "skipped_rows": adapted["skipped_rows"],
            "runs": runs}


def _load_state(path: Path) -> dict[str, str]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return {str(k): str(v) for k, v in raw.items()} if isinstance(raw, dict) else {}


def run_due_networking_once(*, database_url: str | None = None,
                            project_root: Path | None = None,
                            now: datetime | None = None,
                            triggers_path: Path | None = None,
                            state_path: Path | None = None) -> list[dict[str, Any]]:
    """跑一次"到期的触发器"。**失败不扩散** —— 单个触发器抛错只记日志，返回其余结果。

    这是 worker 每轮调用的入口；它绝不会因为组网失败而影响 outbox 分发与任务执行。
    """
    root = project_root or Path(__file__).resolve().parents[2]
    url = database_url or DEFAULT_DATABASE_URL
    moment = now or datetime.now(timezone.utc)
    state_file = state_path or STATE_PATH
    state = _load_state(state_file)
    results: list[dict[str, Any]] = []
    for trigger in due_triggers(load_triggers(triggers_path), state, moment):
        try:
            results.append(run_trigger_once(trigger, database_url=url, project_root=root, now=moment))
        except Exception:  # noqa: BLE001 - 自动行为失败不得打断 worker 的主职责
            logger.exception("trigger %s 执行失败（已隔离，不影响 outbox 与任务执行）", trigger.get("name"))
            continue
        state[str(trigger["name"])] = moment.isoformat()
    if results:
        try:
            state_file.parent.mkdir(parents=True, exist_ok=True)
            state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n",
                                  encoding="utf-8")
        except OSError:
            logger.warning("触发状态写入失败（下次会重复触发，不影响正确性）", exc_info=True)
    return results
