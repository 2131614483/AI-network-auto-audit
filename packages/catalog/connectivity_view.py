"""连通性与供需闭合的只读视图：脚本与 API 共用同一份口径。

**为什么要有这一层**：这组指标此前只活在 ``scripts/report-connectivity.py`` 里，
于是界面只能让用户去命令行跑脚本（页面上写着"端点未接线"）。若再照抄一份到端点里，
就成了两套实现 —— 而"连通性"恰恰是最不该有两套口径的东西：一处改了另一处没改，
看板和 CI 闸门会给出相反的结论。所以口径收在这里一处，脚本与
``GET /api/v1/connectivity/report`` 都调它。

**只读**：不执行插件、不改任何计划、不写任何产物；纯文件系统投影（插件目录 +
评审过的语义目录），不读租户数据。（端点上仍然要过策略网关：读也是一次访问。）
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from packages.ai_planner.composer import discover_plugins
from packages.ai_planner.connectivity import (
    ISLAND_CATEGORIES,
    ISLAND_NAMES_ZH,
    classify_islands,
    connectivity_metrics,
    contract_edges,
    invokes_edges,
    island_summary,
)
from packages.ai_planner.semantics import load_semantics

#: 允许的 lifecycle 取值。``None`` = 全量（含 contract_only）。
LIFECYCLES: tuple[str, ...] = ("verified", "contract_only")


def validate_scope(lifecycle: str | None, include_invokes: bool) -> str:
    """把口径渲染成一句人话，供界面直接显示在图例上。"""
    scope = lifecycle or "全部"
    graph = "contract+invokes" if include_invokes else "contract"
    return f"lifecycle={scope} · 图口径={graph}"


def connectivity_report(
    *,
    lifecycle: str | None = None,
    include_invokes: bool = False,
    root: Path | None = None,
) -> dict[str, Any]:
    """算一次连通性报告：指标 + 孤岛分类 + 口径标注。

    ``lifecycle`` / ``include_invokes`` 是**口径开关**，不是过滤器：不同口径下的数字
    不可比，所以口径随结果一起返回（``scope`` 与 ``metrics.edge_set``），界面必须显示它。
    """
    if lifecycle is not None and lifecycle not in LIFECYCLES:
        raise ValueError(f"unsupported lifecycle: {lifecycle!r} (expected one of {', '.join(LIFECYCLES)})")

    specs = discover_plugins(root, lifecycle=lifecycle)
    edges = contract_edges(specs)
    if include_invokes:
        edges = edges | invokes_edges(specs)
    metrics = connectivity_metrics(specs, edges=edges, include_invokes=include_invokes)

    # 孤岛分类用**含 invokes** 的边：跨层调用也是真实存在的连接，判"悬空"时不能装作没看见。
    islands = classify_islands(
        specs,
        edges=contract_edges(specs) | (invokes_edges(specs) if include_invokes else set()),
        catalog=load_semantics(),
    )
    summary = island_summary(islands)

    return {
        "scope": {
            "lifecycle": lifecycle,
            "include_invokes": include_invokes,
            "label": validate_scope(lifecycle, include_invokes),
        },
        "metrics": metrics.as_dict(),
        "islands": {
            "total": len(islands),
            "summary": summary,
            "present": [
                {"category": category, "label": ISLAND_NAMES_ZH[category], "count": summary[category]}
                for category in ISLAND_CATEGORIES
                if summary.get(category)
            ],
            "items": [island.as_dict() for island in islands],
        },
    }
