"""入口输入形状：按**声明**把案例资料做成任意入口能吃的工件。

**为什么需要它**：54 个外部入口里绝大多数要 JSON，而每个入口期望的结构各不相同
（`policy-input` 要顶层 `policies` 数组，`inventory-input` 要顶层 `items`）—— 这些形状此前
**只存在于各插件的 `runtime.py` 里** —— 只能读代码或撞报错才知道（本轮就是从
`policy-input requires policies array` 反查出来的）。为每个入口手写一份映射脚本既不可复核也不
可复用；声明在 `contracts/input-shapes.json` 之后，换入口只是换一份声明。

**两种取值粒度**（案例资料里 markdown 与 CSV 各占一半，两者不是一回事）：
* `items_from.item` —— **每文件一项**（markdown 类资料：一份制度、一份底稿各算一项）；
* `items_from.columns` —— **每行一项**（CSV 类资料：一行科目、一行盘点记录各算一项）。
  二选一，不允许同时出现：一份声明同时给两种粒度，没人能说清产出的是几项。

**不做猜测**（两条硬约束，与 `input_mapping` 一致）：
* 声明里写的字段/列必须能在源文件里取到，取不到就报错；
* glob 匹配不到文件也报错 —— 静默产出一个空工件会让下游以为"数据本来就没有"。

数值列另有两条口径，都是**声明出来的**、不是代码里猜的：
* ``numbers``：案例表把数量写成 ``1,286,400 件``，`float()` 会直接拒绝。只剥声明里列出的分隔符与
  单位后缀，其余字符一律不剥 —— 剥不干净就让运行时拒绝，不在这里猜。
* ``skip_rows``：数值列写着"没盘到"（案例表用 ``—``）的行**跳过并计数**（返回 ``skipped``），
  不填 0 —— 按 0 算会把"没数"变成"账实不符"，凭空刷出差异项。
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
from pathlib import Path
from typing import Any

SHAPES_PATH = Path(__file__).resolve().parents[2] / "contracts" / "input-shapes.json"

#: 每文件一项时的取值方式。只留最小集合，每加一个都是一条要被审阅的口径。
_VALUE_KINDS = ("stem", "text", "name")

#: 每行一项时，``columns`` 用这个值表示"原样保留全部列"。
_ALL_COLUMNS = "*"

#: 案例 CSV 多为带 BOM 的 UTF-8，且 Excel 另存的表常见 utf-8-sig。
_DEFAULT_ENCODING = "utf-8-sig"


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


def _declared(mapping: Any) -> dict[str, str]:
    """取声明里的有效条目 —— ``_`` 开头的是给人看的依据（``_why``），不是字段。"""
    return {
        str(k): str(v)
        for k, v in (mapping or {}).items()
        if not str(k).startswith("_")
    }


def _read_rows(path: Path, encoding: str) -> tuple[list[str], list[dict[str, str]]]:
    try:
        text = path.read_text(encoding=encoding)
    except (OSError, UnicodeDecodeError) as exc:
        raise ShapeError(f"源文件不是 {encoding} 文本：{path}") from exc
    reader = csv.DictReader(io.StringIO(text))
    header = [str(name).strip() for name in (reader.fieldnames or [])]
    if not header:
        raise ShapeError(f"源文件没有表头，无法按列取值：{path}")
    rows = [
        {str(k).strip(): (v if isinstance(v, str) else "") for k, v in row.items() if k is not None}
        for row in reader
    ]
    return header, rows


def _normalize_number(raw: str, *, field: str, path: Path, rule: dict[str, Any]) -> int | float:
    """按声明剥掉千分位分隔符与单位后缀，然后转数。

    只剥声明里列出的字符：``1,286,400 件`` 剥成 ``1286400``；多一个声明里没有的字符就报错，
    而不是把它当成"大概也是单位"抹掉 —— 那样会把 ``-54 件(待查)`` 这种带注记的数量悄悄读成数。
    """
    text = raw.strip()
    for suffix in rule.get("suffixes") or []:
        suffix = str(suffix)
        if suffix and text.endswith(suffix):
            text = text[: -len(suffix)].strip()
            break
    for separator in rule.get("separators") or []:
        text = text.replace(str(separator), "")
    try:
        value = float(text)
    except ValueError as exc:
        raise ShapeError(
            f"{path.name} 的字段 {field} 不是数字：{raw!r}"
            f"（只剥声明里列出的分隔符 {rule.get('separators') or []} 与后缀 "
            f"{rule.get('suffixes') or []}，其余一律不剥）"
        ) from exc
    return int(value) if value.is_integer() else value


def _items_per_file(files: list[Path], item_fields: dict[str, str]) -> list[dict[str, Any]]:
    unknown = [kind for kind in item_fields.values() if kind not in _VALUE_KINDS]
    if unknown:
        raise ShapeError(f"未支持的取值方式 {unknown}（可选 {list(_VALUE_KINDS)}）")
    return [
        {field: _value(kind, path) for field, kind in item_fields.items()}
        for path in files
    ]


def _items_per_row(
    files: list[Path], columns: Any, numbers: Any, encoding: str, skip_rows: Any
) -> tuple[list[dict[str, Any]], int]:
    keep_all = columns == _ALL_COLUMNS
    field_of = {} if keep_all else _declared(columns)
    if not keep_all and not field_of:
        raise ShapeError("items_from.columns 不能为空（要么给字段←列名，要么给 '*'）")

    rule = numbers or {}
    numeric_fields = [str(field) for field in (rule.get("fields") or [])]
    if numeric_fields and keep_all:
        # 原样保留全部列时"哪些是数值列"没有意义：列名没被声明过，转谁不转谁都说不清。
        raise ShapeError("columns:'*' 与 numbers 不能同时声明（列名未声明，无法指认数值列）")
    unknown = [field for field in numeric_fields if field not in field_of]
    if unknown:
        raise ShapeError(f"numbers.fields 里有未声明的字段：{unknown}（已声明 {sorted(field_of)}）")

    #: 「本行没有这个数」的记号（案例表里是 '—'）。这类行**跳过并计数**，不填默认值 ——
    #: 把没盘到的行按 0 算，会让插件刷出"账实不符"的假差异。
    no_data = {str(token) for token in ((skip_rows or {}).get("tokens") or [])}

    items: list[dict[str, Any]] = []
    skipped = 0
    for path in files:
        header, rows = _read_rows(path, encoding)
        if not keep_all:
            missing = [column for column in field_of.values() if column not in header]
            if missing:
                raise ShapeError(
                    f"{path.name} 缺少声明里的列 {missing}（该文件实际列：{header}）"
                )
        for line_no, row in enumerate(rows, start=2):
            item: dict[str, Any] = dict(row) if keep_all else {
                field: row[column] for field, column in field_of.items()
            }
            if not keep_all:
                unmeasured = {
                    field for field, value in item.items()
                    if str(value).strip() in no_data or not str(value).strip()
                }
                if unmeasured & set(numeric_fields):
                    # 数值列记着"没盘到"（案例表写 '—'）→ 整行跳过并计数。按 0 算会把
                    # "没数"变成"账实不符"，凭空刷出差异项。
                    skipped += 1
                    continue
                if unmeasured:
                    # 标识列空着不猜、不填默认：没有引用键的项进下游只会变成无法追溯的行。
                    raise ShapeError(
                        f"{path.name} 第 {line_no} 行的 {sorted(unmeasured)} 是空值（不猜、不填默认）"
                    )
            for field in numeric_fields:
                item[field] = _normalize_number(
                    str(item.get(field, "")), field=field, path=path, rule=rule
                )
            items.append(item)
    if not items:
        raise ShapeError(
            f"{files[0].name} 有表头但没有可用数据行"
            + (f"（{skipped} 行按声明里的无数据记号跳过）" if skipped else "")
        )
    return items, skipped


def build_artifact(spec: dict[str, Any], dataset_root: Path, out_dir: Path, *, name: str) -> dict[str, Any]:
    """按声明把源文件做成一个 JSON 工件，写到 ``out_dir``。

    返回 ``{"path", "sha256", "size_bytes", "items", "media_type", "classification"}``，
    调用方据此构造种子。**只支持 JSON**（这是 45/54 个入口的形状）；CSV 工件走既有的
    `input_mapping`（它已经有逐分区、逐列的声明式做法）。
    """
    if str(spec.get("format")) != "json":
        raise ShapeError(f"build_artifact 只处理 json 形状，收到 {spec.get('format')!r}")
    root_key = str(spec.get("root_key") or "")
    if not root_key:
        raise ShapeError("json 形状必须声明 root_key（运行时期望的顶层键）")

    source = spec.get("items_from") or {}
    pattern = str(source.get("glob") or "")
    if not pattern:
        raise ShapeError("形状必须声明 items_from.glob")
    item_fields = _declared(source.get("item"))
    columns = source.get("columns")
    if bool(item_fields) == (columns is not None):
        raise ShapeError(
            "形状必须二选一：items_from.item（每文件一项）或 items_from.columns（每行一项）"
        )

    files = sorted(dataset_root.glob(pattern))
    if not files:
        # 不静默产出空工件：空会让下游以为"数据本来就没有"，而其实是路径写错了。
        raise ShapeError(f"按 {pattern!r} 在 {dataset_root} 下找不到源文件")

    if item_fields:
        items = _items_per_file(files, item_fields)
        skipped = 0
    else:
        items, skipped = _items_per_row(
            files, columns, source.get("numbers"),
            str(source.get("encoding") or _DEFAULT_ENCODING), source.get("skip_rows"),
        )

    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"{name}.json"
    target.write_text(json.dumps({root_key: items}, ensure_ascii=False), encoding="utf-8")
    raw = target.read_bytes()
    return {
        "path": target, "sha256": hashlib.sha256(raw).hexdigest(), "size_bytes": len(raw),
        "items": len(items), "skipped": skipped, "media_type": "application/json",
        "classification": str(spec.get("classification") or "external_input"),
    }
