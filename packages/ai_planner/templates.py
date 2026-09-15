"""CW5: reusable drafting templates (versioned, deterministic).

Templates are *suggestions*, not authority: a template that references an
unknown capability or out-of-boundary data is rejected by the same gates as a
model draft.  They exist so the model starts from known-good shapes and so
recall can include "historical successful cases" per 方案 6.1.
"""

from __future__ import annotations

import json
from typing import Any

from .catalog import PORT_CONTRACTS


def _port(port_id: str, direction: str, *, schema_ref: str) -> dict[str, Any]:
    """Build a port from the registered contract directory (single source of
    truth: the template can never drift from what the validator enforces)."""
    contract = PORT_CONTRACTS[port_id]
    assert contract["schema_ref"] == schema_ref
    return {
        "port_id": port_id,
        "direction": direction,
        **dict(contract),
    }


def _node(node_instance_id: str, capability: str, plugin_id: str, *,
          inputs: tuple[dict[str, Any], ...] = (),
          outputs: tuple[dict[str, Any], ...] = ()) -> dict[str, Any]:
    return {
        "node_instance_id": node_instance_id,
        "plugin_id": plugin_id,
        "capability": capability,
        "input_ports": list(inputs),
        "output_ports": list(outputs),
    }


def _edge(edge_id: str, source_instance: str, source_port: str,
          target_instance: str, target_port: str) -> dict[str, Any]:
    return {
        "edge_id": edge_id,
        "source_instance": source_instance,
        "source_port": source_port,
        "target_instance": target_instance,
        "target_port": target_port,
        "adapter": "candidates-to-backtest",
    }


def _ledger_backtest() -> dict[str, Any]:
    ledger_in = _port("ledger", "input", schema_ref="ledger-artifact-ref")
    ledger_out = _port("candidates", "output", schema_ref="audit-quality-candidates")
    experiment_in = _port("experiment", "input", schema_ref="backtest-report")
    evaluation_out = _port("evaluation", "output", schema_ref="experiment-evaluation")
    return {
        "name": "ledger-backtest",
        "description": "日记账质量校验 -> 候选集 -> 回测报告（fan-in 单消费）",
        "plan_key": "plan-ledger-backtest-example",
        "nodes": [
            _node("ledger-a", "audit.ledger.validate", "audit.ledger-quality",
                  inputs=(ledger_in,), outputs=(ledger_out,)),
            _node("consumer-x", "quant.experiment.evaluate", "quant.experiment-evaluator",
                  inputs=(experiment_in,), outputs=(evaluation_out,)),
        ],
        "edges": [
            _edge("e1", "ledger-a", "candidates", "consumer-x", "experiment"),
        ],
        "budget": {"max_chain_length": 8, "max_candidates": 8, "max_latency_ms": 5000},
        "seed_ports": [("ledger-a", "ledger")],
    }


TEMPLATES: dict[str, dict[str, Any]] = {
    "ledger-backtest": _ledger_backtest(),
}


def template_block(template_keys: tuple[str, ...]) -> str:
    """Prompt block: one full, compilable JSON draft per template so the model
    can copy a known-good shape (node/edge/port structure) verbatim and only
    swap capabilities/ports for its goal.  Port fields shown here are the
    registered contracts — they must be reproduced unchanged."""
    lines = ["可用模板（完整 JSON 示例，可直接参考其节点/端口/边结构，只替换能力与端口名）："]
    for key in template_keys:
        template = TEMPLATES.get(key)
        if not template:
            continue
        example: dict[str, Any] = {
            "plan_key": template.get("plan_key", f"plan-{key}-example"),
            "nodes": template["nodes"],
            "edges": template["edges"],
            "budget": template["budget"],
        }
        if template.get("seed_ports"):
            example["seed_inputs"] = [list(pair) for pair in template["seed_ports"]]
        lines.append(f"- {key}: {json.dumps(example, ensure_ascii=False, separators=(',', ':'))}")
    return "\n".join(lines) if lines else "(no templates available)"
