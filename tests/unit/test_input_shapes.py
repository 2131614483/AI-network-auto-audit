"""入口输入形状：按声明把案例资料做成入口能吃的工件。

外部入口里绝大多数要 JSON，而每个入口期望的结构不同——这些形状此前只在各插件 runtime.py 里。
本层的价值全在**不猜**：声明里写的字段/列取不到就报错、glob 匹配不到就报错，
绝不静默产出一个空工件（那会让下游以为"数据本来就没有"）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from packages.catalog.input_shapes import ShapeError, build_artifact, load_shapes, shape

SPEC = {
    "format": "json",
    "root_key": "policies",
    "items_from": {
        "glob": "03_治理资料/*.md",
        "item": {"policy_id": "stem", "title": "stem", "content": "text"},
    },
}

ROWS_SPEC = {
    "format": "json",
    "root_key": "items",
    "items_from": {
        "glob": "02_业务资料/存货盘点表.csv",
        "columns": {"sku": "存货类别", "book_qty": "账面数量", "count_qty": "实盘数量"},
        "numbers": {"fields": ["book_qty", "count_qty"], "separators": [","], "suffixes": ["件"]},
    },
}


def _dataset(root: Path) -> None:
    gov = root / "03_治理资料"
    gov.mkdir(parents=True, exist_ok=True)
    (gov / "公司章程.md").write_text("章程正文", encoding="utf-8")
    (gov / "内控自评.md").write_text("内控正文", encoding="utf-8")


def _csv_dataset(root: Path, body: str) -> None:
    biz = root / "02_业务资料"
    biz.mkdir(parents=True, exist_ok=True)
    (biz / "存货盘点表.csv").write_text(body, encoding="utf-8-sig")


def test_builds_the_declared_root_key_and_item_fields(tmp_path: Path) -> None:
    _dataset(tmp_path)
    result = build_artifact(SPEC, tmp_path, tmp_path / "out", name="policy-input")
    payload = json.loads(Path(result["path"]).read_text(encoding="utf-8"))
    assert list(payload) == ["policies"]                 # 运行时读的就是这个顶层键
    assert result["items"] == 2
    assert payload["policies"][0] == {
        "policy_id": "公司章程", "title": "公司章程", "content": "章程正文",
    }


def test_missing_source_files_is_an_error_not_an_empty_artifact(tmp_path: Path) -> None:
    with pytest.raises(ShapeError, match="找不到源文件"):
        build_artifact(SPEC, tmp_path, tmp_path / "out", name="p")


def test_unknown_value_kind_is_rejected(tmp_path: Path) -> None:
    _dataset(tmp_path)
    bad = {**SPEC, "items_from": {**SPEC["items_from"], "item": {"content": "first-paragraph"}}}
    with pytest.raises(ShapeError, match="未支持的取值方式"):
        build_artifact(bad, tmp_path, tmp_path / "out", name="p")


def test_non_json_format_is_rejected_here(tmp_path: Path) -> None:
    """CSV 走 input_mapping（它已有逐分区、逐列的声明式做法），这里只处理 JSON。"""
    with pytest.raises(ShapeError, match="只处理 json"):
        build_artifact({**SPEC, "format": "csv"}, tmp_path, tmp_path / "out", name="p")


def test_missing_root_key_is_an_error(tmp_path: Path) -> None:
    _dataset(tmp_path)
    with pytest.raises(ShapeError, match="root_key"):
        build_artifact({**SPEC, "root_key": ""}, tmp_path, tmp_path / "out", name="p")


def test_unknown_shape_name_is_rejected() -> None:
    with pytest.raises(ShapeError, match="未声明的入口形状"):
        shape("no-such-shape")


def test_shipped_policy_input_shape_documents_its_basis() -> None:
    """随包声明必须写明依据 —— 形状是从运行时报错反查出来的，不写下来没人能复核。"""
    spec = shape("policy-input")
    assert spec["root_key"] == "policies"
    assert spec["items_from"]["item"] == {"policy_id": "stem", "title": "stem", "content": "text"}
    assert "_why" in spec


def test_shipped_shapes_are_loadable() -> None:
    assert load_shapes()


# --- 每行一项（CSV 源）：案例资料里 CSV 占一半，markdown 的"每文件一项"覆盖不了它 --------


def test_rows_mode_yields_one_item_per_row_not_per_file(tmp_path: Path) -> None:
    _csv_dataset(
        tmp_path,
        "存货类别,账面数量,实盘数量\n库存商品-天酿,\"1,286,400 件\",\"1,286,346 件\"\n",
    )
    result = build_artifact(ROWS_SPEC, tmp_path, tmp_path / "out", name="inventory-input")
    payload = json.loads(Path(result["path"]).read_text(encoding="utf-8"))
    assert result["items"] == 1                          # 一份文件两行 → 两项，不是一项
    assert payload["items"][0] == {
        "sku": "库存商品-天酿", "book_qty": 1286400, "count_qty": 1286346,
    }


def test_missing_declared_column_is_an_error(tmp_path: Path) -> None:
    _csv_dataset(tmp_path, "存货类别,数量\n天酿,1\n")
    with pytest.raises(ShapeError, match="缺少声明里的列"):
        build_artifact(ROWS_SPEC, tmp_path, tmp_path / "out", name="p")


def test_unlisted_separator_or_unit_is_not_stripped(tmp_path: Path) -> None:
    """只剥声明里列出的字符：'1.286.400 箱' 必须报错，而不是被当成"大概也是千分位"抹掉。"""
    _csv_dataset(tmp_path, '存货类别,账面数量,实盘数量\n天酿,"1.286.400 箱",1\n')
    with pytest.raises(ShapeError, match="不是数字"):
        build_artifact(ROWS_SPEC, tmp_path, tmp_path / "out", name="p")


def test_declared_no_data_rows_are_skipped_and_counted(tmp_path: Path) -> None:
    """'—' 表示没盘到：整行跳过并计数，绝不按 0 算 —— 按 0 算会刷出假差异。"""
    _csv_dataset(
        tmp_path,
        "存货类别,账面数量,实盘数量\n在产品,—,—\n库存商品-天酿,100,90\n",
    )
    spec = {
        **ROWS_SPEC,
        "items_from": {**ROWS_SPEC["items_from"], "skip_rows": {"tokens": ["—"]}},
    }
    result = build_artifact(spec, tmp_path, tmp_path / "out", name="p")
    payload = json.loads(Path(result["path"]).read_text(encoding="utf-8"))
    assert result["items"] == 1 and result["skipped"] == 1
    assert payload["items"][0]["count_qty"] == 90


def test_blank_identifier_is_an_error_not_a_skip(tmp_path: Path) -> None:
    """数值列没数可以跳过；标识列空着只能报错（跳过的行/填默认的行都无法追溯）。"""
    _csv_dataset(tmp_path, "存货类别,账面数量,实盘数量\n,100,90\n")
    spec = {
        **ROWS_SPEC,
        "items_from": {**ROWS_SPEC["items_from"], "skip_rows": {"tokens": []}},
    }
    with pytest.raises(ShapeError, match="是空值"):
        build_artifact(spec, tmp_path, tmp_path / "out", name="p")


def test_item_and_columns_are_mutually_exclusive(tmp_path: Path) -> None:
    _csv_dataset(tmp_path, "存货类别,账面数量,实盘数量\n天酿,1,1\n")
    both = {**ROWS_SPEC, "items_from": {**ROWS_SPEC["items_from"], "item": {"sku": "stem"}}}
    with pytest.raises(ShapeError, match="二选一"):
        build_artifact(both, tmp_path, tmp_path / "out", name="p")
    neither = {**ROWS_SPEC, "items_from": {"glob": ROWS_SPEC["items_from"]["glob"]}}
    with pytest.raises(ShapeError, match="二选一"):
        build_artifact(neither, tmp_path, tmp_path / "out", name="p")


def test_keep_all_columns_cannot_also_declare_numbers(tmp_path: Path) -> None:
    """列名没被声明过时"哪些是数值列"说不清，所以 columns:'*' 与 numbers 不能并存。"""
    _csv_dataset(tmp_path, "存货类别,账面数量\n天酿,1\n")
    spec = {
        **ROWS_SPEC,
        "items_from": {
            "glob": ROWS_SPEC["items_from"]["glob"], "columns": "*",
            "numbers": {"fields": ["账面数量"]},
        },
    }
    with pytest.raises(ShapeError, match="不能同时声明"):
        build_artifact(spec, tmp_path, tmp_path / "out", name="p")


# --- 声明与实际入口的对应关系：不许有"没人管"的入口 -----------------------------------


def _external_entries() -> set[str]:
    produced: set[str] = set()
    consumed: set[str] = set()
    for protocol in (Path(__file__).resolve().parents[2] / "plugins" / "builtin").glob(
        "*/plugin.protocol.json"
    ):
        data = json.loads(protocol.read_text(encoding="utf-8"))
        for capability in data.get("capabilities", []):
            for port in capability.get("inputs", []) or []:
                consumed.add(port["contract_id"])
            for port in capability.get("outputs", []) or []:
                produced.add(port["contract_id"])
    return consumed - produced


def test_every_external_entry_is_either_declared_or_recorded_as_unmet() -> None:
    """新入口要么有声明、要么进 unmet —— 否则它会静静躺在"没人管"里，等下一次运行才炸。"""
    raw = json.loads(
        (Path(__file__).resolve().parents[2] / "contracts" / "input-shapes.json").read_text(
            encoding="utf-8"
        )
    )
    declared, unmet = set(raw["shapes"]), set(raw["unmet"])
    assert not declared & unmet
    assert _external_entries() - declared - unmet == set()


def test_unmet_entries_say_what_is_missing_and_stay_undeclared() -> None:
    """unmet 不是"待补的空白声明"：它必须写明卡在哪，且 shape() 对它照旧报错。"""
    raw = json.loads(
        (Path(__file__).resolve().parents[2] / "contracts" / "input-shapes.json").read_text(
            encoding="utf-8"
        )
    )
    assert raw["unmet"]
    for name, entry in raw["unmet"].items():
        assert {"blocker", "need", "_why"} <= set(entry), name
        with pytest.raises(ShapeError, match="未声明的入口形状"):
            shape(name)


def test_shipped_shapes_declare_exactly_one_granularity() -> None:
    for name, spec in load_shapes().items():
        source = spec["items_from"]
        assert ("item" in source) != ("columns" in source), name
        assert "_why" in spec and "_source" in spec, name
