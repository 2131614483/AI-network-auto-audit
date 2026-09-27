"""组网触发层：到期判断与声明校验。

触发层的风险不在"跑不起来"，而在两种**静默**错误：该触发时没触发（自动组网形同虚设、
且没人会发现），以及不该触发时反复触发（无人值守地刷运行）。所以到期逻辑是纯函数、
单独钉住。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from packages.ops.networking_trigger import TriggerError, due_triggers, load_triggers

NOW = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)
TRIGGER = {"name": "t1", "dataset": "d", "mapping": "m", "every_minutes": 60}


def test_never_run_is_due() -> None:
    assert due_triggers([TRIGGER], {}, NOW) == [TRIGGER]


def test_not_due_before_the_cadence() -> None:
    last = {"t1": (NOW - timedelta(minutes=59)).isoformat()}
    assert due_triggers([TRIGGER], last, NOW) == []


def test_due_exactly_at_the_cadence() -> None:
    last = {"t1": (NOW - timedelta(minutes=60)).isoformat()}
    assert due_triggers([TRIGGER], last, NOW) == [TRIGGER]


def test_unparseable_timestamp_is_treated_as_due() -> None:
    """坏记录不能让触发永久停摆 —— 宁可多跑一次并留下记录。"""
    assert due_triggers([TRIGGER], {"t1": "not-a-time"}, NOW) == [TRIGGER]


def test_naive_timestamp_is_read_as_utc() -> None:
    """写状态时若丢了时区，不能因为"无法比较"就崩 —— 按 UTC 解读。"""
    last = {"t1": (NOW - timedelta(minutes=1)).replace(tzinfo=None).isoformat()}
    assert due_triggers([TRIGGER], last, NOW) == []


def test_only_the_due_ones_are_returned() -> None:
    other = {**TRIGGER, "name": "t2"}
    last = {"t1": NOW.isoformat(), "t2": (NOW - timedelta(hours=2)).isoformat()}
    assert [t["name"] for t in due_triggers([TRIGGER, other], last, NOW)] == ["t2"]


def test_declaration_validation(tmp_path: Path) -> None:
    good = tmp_path / "good.json"
    good.write_text(json.dumps({"triggers": [TRIGGER]}, ensure_ascii=False), encoding="utf-8")
    assert load_triggers(good) == [TRIGGER]

    missing = tmp_path / "missing.json"
    missing.write_text(json.dumps({"triggers": [{"name": "x"}]}), encoding="utf-8")
    with pytest.raises(TriggerError, match="缺少字段"):
        load_triggers(missing)

    zero = tmp_path / "zero.json"
    zero.write_text(json.dumps({"triggers": [{**TRIGGER, "every_minutes": 0}]}), encoding="utf-8")
    with pytest.raises(TriggerError, match="必须为正"):
        load_triggers(zero)


def test_shipped_declaration_is_usable() -> None:
    """随包发布的声明必须真的能加载 —— 否则触发层开着也永远不会动。"""
    triggers = load_triggers()
    assert triggers, "至少应有一个触发器声明"
    for entry in triggers:
        assert Path(entry["dataset"]).name            # 数据集路径非空
        assert entry["every_minutes"] > 0
