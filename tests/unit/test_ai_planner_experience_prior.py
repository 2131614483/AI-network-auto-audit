"""环 7（回流生效）读路径单测：经验先验只许改**顺序**，不许改**成员**。

这是本模块最重要的一条不变量。设计 §6.3 规定环 7 的回流「仅改概率分布」——影响只能
是候选排序 / 连线先验 / 提示上下文，**不得新增能力或端口**。`recall_snapshot` 是全量
渲染（无截断），所以只要断言「开/关先验得到的能力集合逐项相同」，就把这条约束钉死了：
任何未来的"顺手排序 + 截断"改动都会让这里的断言变红。

无数据库、无网络：DB 读取全部通过注入或坏 URL 触发降级路径。
"""

from __future__ import annotations

import os
from typing import Any

import pytest

from packages.ai_planner.catalog import CapabilityEntry, recall_snapshot
from packages.ai_planner.experience_prior import (
    ENV_FLAG,
    capability_prior_from_overlay,
    prior_enabled,
    resolve_capability_prior,
)


def _catalog() -> dict[str, CapabilityEntry]:
    def entry(capability: str, plugin_id: str, inputs: tuple[str, ...], outputs: tuple[str, ...]) -> CapabilityEntry:
        return CapabilityEntry(capability=capability, plugin_id=plugin_id, inputs=inputs, outputs=outputs)

    return {
        "audit.a.one": entry("audit.a.one", "plugin-a", ("in-a",), ("out-a",)),
        "audit.b.two": entry("audit.b.two", "plugin-b", ("in-b",), ("out-b",)),
        "audit.c.three": entry("audit.c.three", "plugin-c", ("in-c",), ("out-c",)),
    }


def _capability_lines(text: str) -> list[str]:
    """只取能力行（`- capability (plugin=...)`），忽略端口/契约行。"""
    return [line for line in text.splitlines() if line.startswith("- ")]


def _ordered_capabilities(text: str) -> list[str]:
    return [line[2:].split(" ", 1)[0] for line in _capability_lines(text)]


# -- 开关 ------------------------------------------------------------------


def test_flag_is_off_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENV_FLAG, raising=False)
    assert prior_enabled() is False
    assert resolve_capability_prior("postgresql://unused/db", "00000000-0000-0000-0000-000000000000") is None


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "On"])
def test_flag_accepts_common_truthy_spellings(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv(ENV_FLAG, value)
    assert prior_enabled() is True


@pytest.mark.parametrize("value", ["", "0", "false", "no", "off", "  "])
def test_flag_rejects_everything_else(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv(ENV_FLAG, value)
    assert prior_enabled() is False


# -- 权重折算 --------------------------------------------------------------


def test_prior_takes_the_max_across_capabilities() -> None:
    """同一插件多个能力：取最大值，不被能力条数放大。"""
    overlay: dict[str, Any] = {
        "node_stats": [
            {"plugin_id": "plugin-a", "capability": "x", "weight": 0.4},
            {"plugin_id": "plugin-a", "capability": "y", "weight": 1.7},
            {"plugin_id": "plugin-b", "capability": "z", "weight": 0.9},
        ]
    }
    assert capability_prior_from_overlay(overlay) == {"plugin-a": 1.7, "plugin-b": 0.9}


def test_prior_ignores_rows_without_plugin_id_and_empty_overlay() -> None:
    assert capability_prior_from_overlay({"node_stats": [{"plugin_id": "", "weight": 3.0}]}) == {}
    assert capability_prior_from_overlay({}) == {}
    assert capability_prior_from_overlay({"node_stats": None}) == {}


def test_prior_survives_missing_weight() -> None:
    assert capability_prior_from_overlay({"node_stats": [{"plugin_id": "p"}]}) == {"p": 0.0}


# -- 核心不变量：只改顺序，不改成员 ---------------------------------------


def test_prior_changes_order_but_not_membership() -> None:
    catalog = _catalog()
    plain = recall_snapshot(catalog)
    weighted = recall_snapshot(catalog, prior={"plugin-c": 5.0, "plugin-a": 0.1})

    assert set(_ordered_capabilities(plain)) == set(_ordered_capabilities(weighted))
    assert len(_capability_lines(plain)) == len(_capability_lines(weighted))
    assert _ordered_capabilities(plain) != _ordered_capabilities(weighted)
    # 权重降序（c 5.0 > a 0.1 > b 未出现=0.0），同权重才回落到 capability 字典序。
    assert _ordered_capabilities(weighted) == ["audit.c.three", "audit.a.one", "audit.b.two"]


def test_membership_is_identical_line_for_line_as_a_set() -> None:
    """逐行比较（排序后）：开关先验不新增、不删除、不改写任何一行。"""
    catalog = _catalog()
    plain = sorted(_capability_lines(recall_snapshot(catalog)))
    weighted = sorted(_capability_lines(recall_snapshot(catalog, prior={"plugin-b": 9.0})))
    assert plain == weighted


def test_no_prior_is_byte_identical_to_legacy_behaviour() -> None:
    """``prior=None`` 与不传先验必须逐字节一致——未开开关的部署行为不变。"""
    catalog = _catalog()
    assert recall_snapshot(catalog) == recall_snapshot(catalog, prior=None)
    assert recall_snapshot(catalog) == recall_snapshot(catalog, prior={})
    assert _ordered_capabilities(recall_snapshot(catalog)) == sorted(catalog)


def test_unknown_plugin_in_prior_is_harmless() -> None:
    catalog = _catalog()
    assert _ordered_capabilities(recall_snapshot(catalog, prior={"nobody": 9.0})) == sorted(catalog)


def test_ties_fall_back_to_capability_name_for_determinism() -> None:
    """等权重时必须仍然确定：否则同一意图两次规划会拿到不同提示词。"""
    catalog = _catalog()
    prior = {"plugin-a": 1.0, "plugin-b": 1.0, "plugin-c": 1.0}
    assert _ordered_capabilities(recall_snapshot(catalog, prior=prior)) == sorted(catalog)


# -- 降级：读路径绝不成为规划的新故障点 -----------------------------------


def test_unreachable_database_degrades_to_empty_prior(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_FLAG, "1")
    # 端口 1 上没有服务：必须静默降级为空表，而不是把异常抛给规划。
    assert resolve_capability_prior("postgresql://audit_app@127.0.0.1:1/none", "00000000-0000-0000-0000-000000000000") == {}


def test_invalid_tenant_id_degrades_to_empty_prior(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_FLAG, "1")
    assert resolve_capability_prior(os.getenv("DATABASE_URL", "postgresql://audit_app@127.0.0.1:1/none"), "not-a-uuid") == {}
