"""计划质量门：组出来的网在执行之前先过一道"对这份数据够不够"的检查。

**为什么需要它**（实测依据）：案例数据里有一个只有 6 行的月份，模型给它组了 2 个节点，
下游评估器因输入不足而 failed —— 5 次有效 AI 组网里 1 次是这个原因。计划本身能编译、
能过闸门，问题出在"**这份数据配不配得上这张网**"。执行前拦下来，比让它跑一半再失败好。

**判据分两类，界线要清楚**：

* **硬检查（确定性事实）**：必填输入既没有边也没有种子注入点 → 这张网根本喂不饱，直接拒。
  种子为空、行数为 0 同理。
* **声明式策略（人工定的口径，不是定律）**：节点数上下限、单节点至少要吃到多少行数据。
  这些写在 `contracts/networking-quality-gate.json` 里并注明依据 —— 把口径埋进代码等于
  把策略藏起来，改口径应该就是改那一行。

门只做判断、不改计划：不合格时把 `issues` 交回调用方（AI 路径据此重规划或退回模板计划）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

POLICY_PATH = Path(__file__).resolve().parents[2] / "contracts" / "networking-quality-gate.json"

#: 策略缺省值：声明文件缺失时用，保证门不会因为读不到声明就"静默放行"。
DEFAULT_POLICY: dict[str, Any] = {
    "min_seed_rows": 1,
    "min_planned_nodes": 1,
    "max_planned_nodes": 16,
    "min_rows_per_extra_node": 0,
}


class GateError(ValueError):
    """策略声明不合法。"""


def load_policy(path: Path | None = None) -> dict[str, Any]:
    source = Path(path) if path is not None else POLICY_PATH
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return dict(DEFAULT_POLICY)
    policy = raw.get("policy")
    if not isinstance(policy, dict):
        raise GateError(f"质量门策略声明里没有 policy：{source}")
    merged = dict(DEFAULT_POLICY)
    merged.update({k: v for k, v in policy.items() if not str(k).startswith("_")})
    return merged


def assess_plan(
    plan: Any, *, seed_rows: int, policy: dict[str, Any] | None = None
) -> dict[str, Any]:
    """检查"这张网配不配得上这份数据"。纯函数，不改计划。

    返回 ``{"ok": bool, "issues": [...], "checked": {...}}``；``issues`` 每条都带
    ``code`` 与 ``detail``，调用方据此重规划或降级，而不是只看一个 bool。
    """
    rules = dict(policy or load_policy())
    issues: list[dict[str, str]] = []
    nodes = list(getattr(plan, "nodes", ()) or ())
    seed_inputs: Any = getattr(plan, "seed_inputs", None) or set()

    # -- 硬检查：喂不饱的网不该执行 --------------------------------------
    edge_targets = {(edge.target_instance, edge.target_port) for edge in getattr(plan, "edges", ()) or ()}
    for node in nodes:
        for port in getattr(node, "input_ports", ()) or ():
            if not getattr(port, "required", False):
                continue
            key = (node.node_instance_id, port.port_id)
            if key in edge_targets or key in seed_inputs:
                continue
            issues.append({
                "code": "unbound_required_input",
                "detail": f"节点 {node.node_instance_id} 的必填输入 {port.port_id!r} 既无上游边也无种子注入点",
            })
    if seed_rows <= 0:
        issues.append({"code": "empty_seed", "detail": "种子数据为空，组网没有可处理的输入"})

    # -- 声明式策略：口径在 contracts/networking-quality-gate.json ----------
    min_rows = int(rules["min_seed_rows"])
    if 0 < seed_rows < min_rows:
        issues.append({
            "code": "seed_below_minimum",
            "detail": f"种子 {seed_rows} 行低于声明下限 {min_rows} 行（数据太少，不值得组网）",
        })
    min_nodes, max_nodes = int(rules["min_planned_nodes"]), int(rules["max_planned_nodes"])
    if nodes and not min_nodes <= len(nodes) <= max_nodes:
        issues.append({
            "code": "node_count_out_of_range",
            "detail": f"计划 {len(nodes)} 个节点，超出声明范围 {min_nodes}..{max_nodes}",
        })
    # 每多一个节点，数据规模应当撑得住 —— 这正是"6 行数据配 2 个节点"那种失配。
    per_node = int(rules["min_rows_per_extra_node"])
    if per_node > 0 and len(nodes) > 1 and seed_rows < per_node * (len(nodes) - 1):
        issues.append({
            "code": "rows_too_few_for_nodes",
            "detail": f"{seed_rows} 行数据配 {len(nodes)} 个节点：按每节点至少 {per_node} 行，"
                      f"规模不足以撑起这张网",
        })

    return {
        "ok": not issues,
        "issues": issues,
        "checked": {"seed_rows": seed_rows, "nodes": len(nodes), "policy": rules},
    }
