"""从种子出发，在目录里**确定性地搜出可达路径骨架**。

**为什么需要它**（实测依据）：让模型从 107 个能力里凭空组一条 7 步链，无论换什么变量——
更长/更短的目标、加不加理解面、有没有模板、单/多入口种子、flash 还是推理模型——它都只交
2 个节点（八次实验，纹丝不动）。唯一变化的一次是**把目录缩到 8 个能力**：它敢组了，
但 `contract_mismatch` —— 端口契约抄不对。

所以真正的约束是：**模型要在一次推理里逐字段复刻每个节点的端口契约（`schema_sha256` 一个
字符都不能错），而编译门不可通融**。任何模型面对这个都会选最小安全解。

**分工应当改**：找路是纯图计算（目录里本来就有 9 层深的链，`contract_edges` 已经有 69 条边的图），
不该让模型凭空搜；模型的强项是**选择与判断**（挑哪条路、合并哪些步、给业务理由），
而契约本来就登记在目录里，直接引用即可 —— 不存在"抄错"。

本模块只做第一半：给种子，返回**可达的路径骨架**（按长度降序）。不做执行、不做判断。
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable, Mapping

from packages.plugin_topology.ir import IRNode  # noqa: F401  (类型提示用，保持依赖显式)


def _edges_by_source(specs: Mapping[str, Any], edges: Iterable[tuple[str, str]]) -> dict[str, list[str]]:
    adjacency: dict[str, list[str]] = defaultdict(list)
    for source, target in edges:
        if source != target:
            adjacency[source].append(target)
    for targets in adjacency.values():
        targets.sort()          # 确定性：同一份目录每次搜出的顺序一致
    return adjacency


def startable(specs: Mapping[str, Any], seed_ports: set[str]) -> list[str]:
    """必填输入**全部**能由种子覆盖的插件 —— 它们才是合法的起点。

    只看必填：可选输入缺失不阻塞执行。这条与编译器的要求一致（required 未绑定会被拒）。
    """
    starts: list[str] = []
    for plugin_id, spec in specs.items():
        required = [p for p in spec.inputs if getattr(p, "required", True)]
        if all(p.port_id in seed_ports for p in required):
            starts.append(plugin_id)
    return sorted(starts)


def reachable_paths(
    specs: Mapping[str, Any],
    edges: Iterable[tuple[str, str]],
    seed_ports: set[str],
    *,
    max_depth: int = 8,
    limit: int = 20,
) -> list[list[str]]:
    """从种子可达的简单路径，**按长度降序**返回前 ``limit`` 条。

    深度上限 ``max_depth`` 是**节点的数量**上限（不是边数）。只走简单路径（不重复经过
    同一插件），因为它要作为计划骨架 —— 同一插件在一个计划里出现两次会让端口绑定含糊。
    """
    if max_depth < 1:
        raise ValueError("max_depth must be >= 1")
    adjacency = _edges_by_source(specs, edges)
    # 种子按 schema_ref 归一：调用方给的是端口名，而覆盖判断走 schema_ref。
    seed_schema_refs = {
        p.schema_ref
        for spec in specs.values()
        for p in spec.inputs
        if p.port_id in seed_ports
    }
    paths: list[list[str]] = []

    def walk(path: list[str], seen: set[str], covered: set[str]) -> None:
        paths.append(list(path))
        if len(path) >= max_depth:
            return
        previous_outputs = {p.port_id for p in specs[path[-1]].outputs}
        for target in adjacency.get(path[-1], ()):
            if target in seen:
                continue
            # **必须每个必填输入都被覆盖**（种子或路径上任一节点的产出）——
            # 只看"有一个端口能对上"是不够的：编译器会以"required input 无生产者"拒绝，
            # 而那条错误只有到执行前才暴露。这里就卡住，搜出来的才是**可执行链**。
            # 编译器的绑定口径：每个必填输入必须由**直接前驱的边**（或种子）绑定。
            # 因此可扩展条件只看前驱的产出 —— 用整条路径的累计产出是错的（本轮踩过）：
            # 那会放进"输入来自旁支节点"的骨架，而本建计划器只在相邻两步间连边。
            required = {p.port_id for p in specs[target].inputs if getattr(p, "required", True)}
            if not required <= (previous_outputs | seed_ports):
                continue
            produced = {p.port_id for p in specs[target].outputs}
            seen.add(target)
            path.append(target)
            walk(path, seen, covered | produced)
            path.pop()
            seen.discard(target)

    for origin in startable(specs, seed_ports):
        # 起点的覆盖集 = 种子端口 + 起点自身的产出
        walk([origin], {origin}, set(seed_schema_refs) | {p.schema_ref for p in specs[origin].outputs})

    # 长度降序，同长按字典序 —— 确定性，且把最深的骨架先给模型看。
    paths.sort(key=lambda item: (-len(item), item))
    return paths[:limit]


def describe_path(specs: Mapping[str, Any], path: list[str]) -> str:
    """把一条骨架渲染成给模型看的一行：`插件[能力] → 插件[能力] → …`。"""
    steps: list[str] = []
    for plugin_id in path:
        spec = specs.get(plugin_id)
        capability = getattr(spec, "capability", "") or plugin_id
        steps.append(f"{plugin_id}[{capability}]")
    return " → ".join(steps)


# -- 确定性建计划：骨架 → 可编译计划 --------------------------------------
# 契约从**目录登记值**直接取，不经模型 —— 这正是"抄不对契约"那个瓶颈的解法。


def _port_payload(port: Any) -> dict[str, Any]:
    """PortSpec → 编译器要求的端口字典。

    ``schema_sha256`` 目录清单里通常是空的（``None``），而编译门要求它必须声明 ——
    这里按 ``schema_ref`` **从契约文件现算**（`schema_sha256()` 本来就是干这个的），
    而不是留空让编译失败、也不是编一个值。
    """
    from packages.ai_planner.composer import schema_sha256

    digest = port.schema_sha256 or schema_sha256(port.schema_ref)
    return {
        "port_id": port.port_id,
        "direction": port.direction,
        "schema_ref": port.schema_ref,
        "schema_version": "1.0.0",
        "schema_sha256": digest,
        "media_type": "application/json",
        "required": bool(getattr(port, "required", True)),
        "cardinality": getattr(port, "cardinality", "one"),
        "classification": "internal",
        "transport": "artifact_ref",
    }


def plan_from_path(
    specs: Mapping[str, Any], path: list[str], seed_ports: set[str], *, plan_key: str,
    max_nodes: int | None = None,
) -> Any:
    """把一条骨架变成可编译的 ExecutionPlan（端口契约由代码填，模型不参与）。

    边只在**相邻两步之间**、且两端 port_id 相同时才连（项目口径） —— 与编译器的规则一致；
    连不上就说明这条骨架在这一步断了，直接报错而不是编一条边出来。
    第一步的必填输入由 ``seed_ports`` 注入。
    """
    from packages.plugin_topology.compiler import compile_plan

    nodes: list[dict[str, Any]] = []
    for index, plugin_id in enumerate(path):
        spec = specs[plugin_id]
        nodes.append({
            "node_instance_id": f"{plugin_id.split('.')[-1]}-{index:03d}",
            "plugin_id": plugin_id,
            "capability": spec.capability,
            "input_ports": [_port_payload(p) for p in spec.inputs],
            "output_ports": [_port_payload(p) for p in spec.outputs],
        })

    edges: list[dict[str, Any]] = []
    for index in range(len(path) - 1):
        source, target = specs[path[index]], specs[path[index + 1]]
        match = next(
            (
                (out.port_id, inp.port_id)
                for out in source.outputs
                for inp in target.inputs
                if out.port_id == inp.port_id
            ),
            None,
        )
        if match is None:
            raise ValueError(
                f"骨架在 {path[index]} → {path[index + 1]} 处断开：没有相同的 schema_ref"
            )
        edges.append({
            "edge_id": f"e{index + 1}",
            "source_instance": nodes[index]["node_instance_id"],
            "source_port": match[0],
            "target_instance": nodes[index + 1]["node_instance_id"],
            "target_port": match[1],
        })

    first = specs[path[0]]
    seeds = {
        (nodes[0]["node_instance_id"], port.port_id)
        for port in first.inputs
        if port.port_id in seed_ports
    }
    # 预算按**节点数**给，不按链长：`max_chain_length` 是为线性链设计的，DAG 一上来就
    # 超它（实测 20 节点触发 "exceeding max_chain_length budget 16"）。链长仍给一个宽松
    # 上界（DAG 里最长路径不会超过节点数），节点/边预算按实际规模给。
    effective_nodes = max(max_nodes or len(nodes), len(nodes))
    return compile_plan(
        nodes=nodes, edges=edges, plan_key=plan_key, seed_inputs=seeds,
        budget={
            "max_chain_length": max(16, effective_nodes),
            "max_candidates": max(8, len(edges)),
            "max_latency_ms": 5000,
        },
    )


# -- DAG 收敛：这才是组网的真实结构 ----------------------------------------
# 实测依据：`audit.risk.risk-matrix-build` 有 **6 个必填输入，各由 6 个不同插件产出**，
# 彼此没有先后关系。这是**扇入**，线性路径表达不了 —— 不管怎么调路径搜索，可执行的线性链
# 都只有 2 节点（只有"单输入"的能力能串成线）。组网的结构是 DAG。


def build_dag(
    specs: Mapping[str, Any],
    seed_ports: set[str],
    *,
    max_nodes: int = 32,
) -> dict[str, Any]:
    """从种子出发**反复收敛**：把所有"必填输入已被覆盖"的插件加进来，直到不动点。

    worklist 算法（不是路径枚举）。每一步都只加入**此刻已可满足**的节点，因此产出的
    每一个节点在编译器的口径下都真的能跑 —— 不存在"看起来连上了、编译时说无生产者"。

    返回 ``{"plugins": [...], "edges": [...], "covered": [...]}``；``edges`` 的每条都带
    真实的 (源插件, 源端口, 目标插件, 目标端口)，端口按 **port_id** 配对（与项目口径一致）。
    """
    # **按 port_id 连线**，与项目权威口径一致（`build_contract_index` 就是按 port_id 建索引的）。
    # 我前两版在这里用了 schema_ref，于是"六个风险输入各由不同插件产出"被抹平成同一个来源 ——
    # 那是我的 bug，不是目录的缺陷。
    covered: set[str] = set(seed_ports)
    producers: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for plugin_id, spec in specs.items():
        for port in spec.outputs:
            producers[port.port_id].append((plugin_id, port.port_id))

    chosen: list[str] = []
    chosen_set: set[str] = set()
    edges: list[dict[str, str]] = []
    while len(chosen) < max_nodes:
        progressed = False
        for plugin_id in sorted(specs):
            if plugin_id in chosen_set:
                continue
            required = {p.port_id for p in specs[plugin_id].inputs if getattr(p, "required", True)}
            if not required <= covered:
                continue
            # 每个必填输入都要能指到**具体**的生产者（种子或已选节点），否则这条边绑不出来
            bindings: list[tuple[str, str]] = []
            for port in specs[plugin_id].inputs:
                if not getattr(port, "required", True):
                    continue
                candidates = [
                    (src, src_port)
                    for src, src_port in producers.get(port.port_id, [])
                    if src in chosen_set
                ]
                if not candidates and port.port_id in seed_ports:
                    candidates = [("__seed__", port.port_id)]
                if not candidates:
                    bindings = []
                    break
                bindings.append((candidates[0][0], candidates[0][1]))
            if not bindings:
                continue
            chosen_set.add(plugin_id)
            chosen.append(plugin_id)
            for port, (src, src_port) in zip(
                [p for p in specs[plugin_id].inputs if getattr(p, "required", True)], bindings
            ):
                edges.append({
                    "source": src, "source_port": src_port,
                    "target": plugin_id, "target_port": port.port_id,
                })
            covered |= {p.port_id for p in specs[plugin_id].outputs}
            progressed = True
        if not progressed:
            break
    return {"plugins": chosen, "edges": edges, "covered": sorted(covered)}


def plan_from_dag(
    specs: Mapping[str, Any], dag: dict[str, Any], seed_ports: set[str], *, plan_key: str,
    max_nodes: int | None = None,
) -> Any:
    """把 `build_dag` 的收敛结果变成可编译计划。

    **直接用 DAG 自己算出的边**，不重新推：`build_dag` 里每一次绑定都已经确认过
    "这个必填输入由哪个已选节点（或种子）提供"，重推一遍只会引入新的口径不一致
    （本轮就因为 `schema_ref` 与 `port_id` 混用踩过三次）。
    """
    from packages.plugin_topology.compiler import compile_plan

    plugins: list[str] = list(dag["plugins"])
    node_id_of = {pid: f"{pid.split('.')[-1]}-{i:03d}" for i, pid in enumerate(plugins)}
    nodes = [
        {
            "node_instance_id": node_id_of[pid],
            "plugin_id": pid,
            "capability": specs[pid].capability,
            "input_ports": [_port_payload(p) for p in specs[pid].inputs],
            "output_ports": [_port_payload(p) for p in specs[pid].outputs],
        }
        for pid in plugins
    ]

    edges: list[dict[str, str]] = []
    seeds: set[tuple[str, str]] = set()
    for index, edge in enumerate(dag["edges"], start=1):
        if edge["source"] == "__seed__":
            seeds.add((node_id_of[edge["target"]], edge["target_port"]))
            continue
        edges.append({
            "edge_id": f"e{index}",
            "source_instance": node_id_of[edge["source"]],
            "source_port": edge["source_port"],
            "target_instance": node_id_of[edge["target"]],
            "target_port": edge["target_port"],
        })
    # 预算按**节点数**给，不按链长：`max_chain_length` 是为线性链设计的，DAG 一上来就
    # 超它（实测 20 节点触发 "exceeding max_chain_length budget 16"）。链长仍给一个宽松
    # 上界（DAG 里最长路径不会超过节点数），节点/边预算按实际规模给。
    effective_nodes = max(max_nodes or len(nodes), len(nodes))
    return compile_plan(
        nodes=nodes, edges=edges, plan_key=plan_key, seed_inputs=seeds,
        budget={
            "max_chain_length": max(16, effective_nodes),
            "max_candidates": max(8, len(edges)),
            "max_latency_ms": 5000,
        },
    )
