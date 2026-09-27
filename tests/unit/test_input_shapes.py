"""入口输入形状：按声明把案例资料做成入口能吃的工件。

45/54 个入口要 JSON，而每个入口期望的结构不同——这些形状此前只在各插件 runtime.py 里。
本层的价值全在**不猜**：声明里写的字段取不到就报错、glob 匹配不到就报错，
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


def _dataset(root: Path) -> None:
    gov = root / "03_治理资料"
    gov.mkdir(parents=True, exist_ok=True)
    (gov / "公司章程.md").write_text("章程正文", encoding="utf-8")
    (gov / "内控自评.md").write_text("内控正文", encoding="utf-8")


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
