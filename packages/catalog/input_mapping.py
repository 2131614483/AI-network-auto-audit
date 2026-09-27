"""输入适配层：按**声明的映射**把外部数据文件变成插件端口契约工件。

**为什么要有这一层**：真实资料是客户自己的表，列名各异、口径各异；而端口契约是固定的。
中间那层映射承载着**判断** —— 哪一列是业务主键、按什么分组、空白填什么、哪一列是金额。
这些判断散落在一次性脚本里，就既不可复核也不可复用；声明在 `contracts/input-mappings.json`
之后，换一个数据集只需换一份声明，代码不动。

**不做猜测**（两条硬约束）：
* 声明里写了、源文件里没有的列 → 直接报错，绝不按位置或模糊匹配凑；
* 取不到分区键的行 → 跳过并**计数**，不静默丢弃、也不编一个月份出来。

只读源数据；派生工件写到调用方给的输出目录，不碰源目录。
"""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
from typing import Any

#: 映射声明文件（与 `capability-parameters.json` 同处 `contracts/`，同为"人工审阅的声明"）。
MAPPINGS_PATH = Path(__file__).resolve().parents[2] / "contracts" / "input-mappings.json"

#: 支持的分区变换。刻意只留最小的集合 —— 每加一个都是一条需要被审阅的口径。
_TRANSFORMS = ("year-month",)


class MappingError(ValueError):
    """声明或源数据不满足映射条件（不可猜、不可推断）。"""


def load_mappings(path: Path | None = None) -> dict[str, Any]:
    source = Path(path) if path is not None else MAPPINGS_PATH
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MappingError(f"输入映射声明不可读：{source}") from exc
    mappings = raw.get("mappings")
    if not isinstance(mappings, dict) or not mappings:
        raise MappingError(f"输入映射声明里没有 mappings：{source}")
    return mappings


def mapping(name: str, path: Path | None = None) -> dict[str, Any]:
    mappings = load_mappings(path)
    if name not in mappings:
        raise MappingError(f"未声明的输入映射：{name!r}（可选 {sorted(mappings)}）")
    spec = mappings[name]
    if not isinstance(spec, dict):
        raise MappingError(f"输入映射 {name!r} 必须是一个对象")
    return spec


def _apply_transform(value: str, transform: str) -> str:
    if transform == "year-month":
        return value[:7]
    raise MappingError(f"未支持的分区变换：{transform!r}（可选 {list(_TRANSFORMS)}）")


def _partition(row: dict[str, str], spec: dict[str, Any], group_column: str) -> str:
    """按声明算出这条记录属于哪个分区；算不出来就返回空串（由调用方跳过并计数）。"""
    transform = str(spec.get("transform") or "")
    return _apply_transform(str(row.get(group_column) or "").strip(), transform)


def find_sources(spec: dict[str, Any], dataset_root: Path) -> list[Path]:
    pattern = str((spec.get("source") or {}).get("glob") or "")
    if not pattern:
        raise MappingError("映射声明缺少 source.glob")
    matches = sorted(dataset_root.glob(pattern))
    if not matches:
        raise MappingError(f"按 {pattern!r} 在 {dataset_root} 下找不到源文件")
    return matches


def apply_mapping(
    spec: dict[str, Any], dataset_root: Path, out_dir: Path, *, source: Path | None = None
) -> dict[str, Any]:
    """把 `dataset_root` 下的源文件按声明映射成逐分区工件，写到 `out_dir`。

    返回结构化结果：每个分区的路径/sha256/行数/分组数，以及被跳过的行数 ——
    「跳过了多少」必须能看见，否则缺失会被当成"数据本来就这样"。
    """
    src = source or find_sources(spec, dataset_root)[0]
    encoding = str((spec.get("source") or {}).get("encoding") or "utf-8-sig")
    with src.open(encoding=encoding, newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise MappingError(f"源文件没有数据行：{src}")

    columns: dict[str, str] = {
        field: str(column)
        for field, column in (spec.get("columns") or {}).items()
        if not field.startswith("_")
    }
    amounts = spec.get("amount_columns") or {}
    blank_value = str(amounts.get("blank_value") or "")
    amount_columns = {
        str(field): str(column)
        for field, column in amounts.items()
        # `blank_value` 是设置项、不是列名 —— 早先把它当列检查过，会误报"缺列 0.00"。
        if not str(field).startswith("_") and field != "blank_value"
    }

    missing = [column for column in list(columns.values()) + list(amount_columns.values())
               if column not in rows[0]]
    if missing:
        # 不猜：缺列就是缺列，按位置或近似名去凑只会把错误埋进数据里。
        raise MappingError(f"{src.name} 缺少映射声明的列 {missing}")

    partition_spec = spec.get("partition_by") or {}
    group_column = str(partition_spec.get("from") or "")
    if group_column and group_column not in rows[0]:
        raise MappingError(f"{src.name} 缺少分区列 {group_column!r}")

    header = list(columns) + [field for field in amount_columns if field not in columns]
    buckets: dict[str, list[list[str]]] = {}
    groups: dict[str, set[str]] = {}
    skipped = 0
    for row in rows:
        partition = _partition(row, partition_spec, group_column) if group_column else "all"
        if not partition:
            skipped += 1
            continue
        record = [str(row.get(columns[field]) or "").strip() for field in columns]
        for field, column in amount_columns.items():
            if field in columns:
                continue
            value = str(row.get(column) or "").strip() or blank_value
            record.append(value)
        buckets.setdefault(partition, []).append(record)
        if columns.get("entry_id"):
            groups.setdefault(partition, set()).add(
                str(row.get(columns["entry_id"]) or "").strip()
            )

    out_dir.mkdir(parents=True, exist_ok=True)
    name = str((spec.get("artifact") or {}).get("name") or "artifact")
    artifacts: dict[str, dict[str, Any]] = {}
    for partition, data in sorted(buckets.items()):
        target = out_dir / f"{name}-{partition}.csv"
        with target.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(header)
            writer.writerows(data)
        raw = target.read_bytes()
        artifacts[partition] = {
            "path": target, "period": partition, "rows": len(data),
            "groups": len(groups.get(partition, ())),
            "sha256": hashlib.sha256(raw).hexdigest(), "size_bytes": len(raw),
            "media_type": str((spec.get("artifact") or {}).get("media_type") or "text/csv"),
            "classification": str((spec.get("artifact") or {}).get("classification") or "internal"),
        }
    return {
        "source": src, "target_contract": spec.get("target_contract"),
        "total_rows": len(rows), "skipped_rows": skipped, "artifacts": artifacts,
    }
