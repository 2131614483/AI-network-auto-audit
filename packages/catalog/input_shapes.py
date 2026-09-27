"""入口输入形状：按**声明**把案例资料做成任意入口能吃的工件。

**为什么需要它**：54 个外部入口里有 45 个要 JSON、5 个要 CSV、4 个其它，而每个 JSON 入口
期望的结构各不相同（`policy-input` 要顶层 `policies` 数组，别的可能是 `artifact` 或分层
命名空间）。这些形状此前**只存在于各插件的 `runtime.py` 里** —— 只能读代码或撞报错才知道
（本轮就是从 `policy-input requires policies array` 反查出来的）。为每个入口手写一份映射
脚本既不可复核也不可复用；声明在 `contracts/input-shapes.json` 之后，换入口只是换一份声明。

**不做猜测**（与 `input_mapping` 一致）：声明里写的字段必须能在源文件里取到，取不到就报错；
glob 匹配不到文件也报错 —— 静默产出一个空工件会让下游以为"数据本来就没有"。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

SHAPES_PATH = Path(__file__).resolve().parents[2] / "contracts" / "input-shapes.json"

#: item 字段的取值方式。只留最小集合，每加一个都是一条要被审阅的口径。
_VALUE_KINDS = ("stem", "text", "name")


class ShapeError(ValueError):
    """声明或源数据不满足形状要求（不可猜）。"""


def load_shapes(path: Path | None = None) -> dict[str, Any]:
    source = Path(path) if path is not None else SHAPES_PATH
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ShapeError(f"输入形状声明不可读：{source}") from exc
    shapes = raw.get("shapes")
    if not isinstance(shapes, dict) or not shapes:
        raise ShapeError(f"输入形状声明里没有 shapes：{source}")
    return shapes


def shape(name: str, path: Path | None = None) -> dict[str, Any]:
    shapes = load_shapes(path)
    if name not in shapes:
        raise ShapeError(f"未声明的入口形状：{name!r}（可选 {sorted(shapes)}）")
    value = shapes[name]
    if not isinstance(value, dict):
        raise ShapeError(f"入口形状 {name!r} 必须是一个对象")
    return value


def _value(kind: str, path: Path) -> str:
    if kind == "stem":
        return path.stem
    if kind == "name":
        return path.name
    if kind == "text":
        return path.read_text(encoding="utf-8")
    raise ShapeError(f"未支持的取值方式：{kind!r}（可选 {list(_VALUE_KINDS)}）")


def build_artifact(spec: dict[str, Any], dataset_root: Path, out_dir: Path, *, name: str) -> dict[str, Any]:
    """按声明把源文件做成一个 JSON 工件，写到 ``out_dir``。

    返回 ``{"path", "sha256", "size_bytes", "items", "media_type", "classification"}``，
    调用方据此构造种子。**只支持 JSON**（这是 45/54 个入口的形状）；CSV 走既有的
    `input_mapping`（它已经有逐分区、逐列的声明式做法）。
    """
    if str(spec.get("format")) != "json":
        raise ShapeError(f"build_artifact 只处理 json 形状，收到 {spec.get('format')!r}")
    root_key = str(spec.get("root_key") or "")
    if not root_key:
        raise ShapeError("json 形状必须声明 root_key（运行时期望的顶层键）")

    source = spec.get("items_from") or {}
    pattern = str(source.get("glob") or "")
    item_fields: dict[str, str] = {
        str(k): str(v) for k, v in (source.get("item") or {}).items() if not str(k).startswith("_")
    }
    if not pattern or not item_fields:
        raise ShapeError("json 形状必须声明 items_from.glob 与 items_from.item")
    unknown = [kind for kind in item_fields.values() if kind not in _VALUE_KINDS]
    if unknown:
        raise ShapeError(f"未支持的取值方式 {unknown}（可选 {list(_VALUE_KINDS)}）")

    files = sorted(dataset_root.glob(pattern))
    if not files:
        # 不静默产出空工件：空会让下游以为"数据本来就没有"，而其实是路径写错了。
        raise ShapeError(f"按 {pattern!r} 在 {dataset_root} 下找不到源文件")

    items = [{field: _value(kind, path) for field, kind in item_fields.items()} for path in files]
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"{name}.json"
    target.write_text(json.dumps({root_key: items}, ensure_ascii=False), encoding="utf-8")
    raw = target.read_bytes()
    return {
        "path": target, "sha256": hashlib.sha256(raw).hexdigest(), "size_bytes": len(raw),
        "items": len(items), "media_type": "application/json",
        "classification": str(spec.get("classification") or "external_input"),
    }
