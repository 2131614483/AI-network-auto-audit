"""经验先验：把经验层的实测权重读成「召回排序的平局裁决量」（环 7 读路径）。

设计约束（`docs/知识库管理界面-统一信息中枢设计-20260915.md` §6.3）：环 7 的回流**只
改概率分布** —— 影响的只能是候选排序 / 连线先验 / 提示上下文，不得新增能力或端口、
不得放宽数据边界、不得绕过闸门与策略网关。所以这里只产出「plugin_id → 权重」这一张
平局裁决表，交给 :func:`~packages.ai_planner.catalog.recall_snapshot` 调整**顺序**；
它不筛选、不裁剪、不改变清单成员，也不参与任何策略判定。

取不到经验（开关未开 / 库不可达 / 租户无数据 / 经验层为空）一律返回空表：规划必须在
没有经验的机器上照常工作，读路径不能成为新的故障点。
"""

from __future__ import annotations

import logging
import os
from typing import Any, Mapping
from uuid import UUID

logger = logging.getLogger(__name__)

#: 开关，**默认关**。环 7 是行为改变，未经人工对照评审前不默认生效；
#: 也正因为有开关，才能做「开/关同一意图，读到的召回清单成员完全一致」的对照验收。
ENV_FLAG = "AI_PLANNER_EXPERIENCE_PRIOR"


def prior_enabled() -> bool:
    """环境开关是否打开。"""
    return os.getenv(ENV_FLAG, "").strip().lower() in {"1", "true", "yes", "on"}


def capability_prior_from_overlay(overlay: Mapping[str, Any]) -> dict[str, float]:
    """经验叠加层 → ``plugin_id -> 权重``。同一插件多个能力时取**最大值**。

    取最大值而非求和：一个插件在多个能力上出现过，并不说明它在*任一*能力上更可信；
    排序先验要回答的是「这个插件历史上靠不靠得住」，最大值不会被能力条数放大。

    ``overlay`` 来自 ``ExperienceProjector.read_overlay()``，其中 ``node_stats[].weight``
    已是「使用次数 × 置信度」并叠加了 90 天半衰期衰减的时间无关基值。
    """
    prior: dict[str, float] = {}
    for row in overlay.get("node_stats") or ():
        plugin_id = str(row.get("plugin_id") or "")
        if not plugin_id:
            continue
        weight = float(row.get("weight") or 0.0)
        if plugin_id not in prior or weight > prior[plugin_id]:
            prior[plugin_id] = weight
    return prior


def load_capability_prior(database_url: str, tenant_id: UUID | str) -> dict[str, float]:
    """读经验层叠加层并折算成权重表；**任何失败都降级为空表**，绝不抛给调用方。

    ``packages.experience`` 在函数内导入：开关关闭时规划路径完全不碰经验层，
    也不会给 ``ai_planner`` 增加一个常驻的运行时依赖。
    """
    try:
        from packages.experience import ExperienceProjector

        overlay = ExperienceProjector(database_url).read_overlay(tenant_id=UUID(str(tenant_id)))
    except Exception:  # noqa: BLE001 - 读路径不得成为规划的新故障点
        logger.warning("experience prior unavailable; planning without it", exc_info=True)
        return {}
    return capability_prior_from_overlay(overlay)


def resolve_capability_prior(database_url: str, tenant_id: UUID | str) -> dict[str, float] | None:
    """给规划入口用的解析入口：开关关时返回 ``None``（即"与从前逐字节一致"）。"""
    if not prior_enabled():
        return None
    return load_capability_prior(database_url, tenant_id)
