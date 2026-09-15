"""Curate node roles with a model, behind deterministic gates.

用法::

    # 先看模型会给什么，不写任何文件（推荐先跑这个）
    .\\.venv\\Scripts\\python.exe scripts\\curate-roles.py --domain audit --dry-run

    # 只跑前 10 个待判定插件
    .\\.venv\\Scripts\\python.exe scripts\\curate-roles.py --domain audit --limit 10 --dry-run

    # 采纳通过闸门的提议，写入 contracts/semantics/role-map.json
    .\\.venv\\Scripts\\python.exe scripts\\curate-roles.py --domain audit --write

为什么不是"让模型直接决定"：判定结果是**提议**。每个提议都要过确定性闸门 ——
角色必须在受控词表内、必须给出依据、置信度不得低于阈值；任何一条不满足就回落
`review` 并**留在 rejected 里**，绝不为了让统计好看而当作已判定。

模型走项目统一的 AI 通道（`AI_*` 配置，调用时解析；不可用则 fail-closed）。
退出码: 0 = 正常；1 = 模型不可用或没有任何提议通过（不伪造成功）。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from packages.ai_planner.composer import discover_plugins  # noqa: E402
from packages.ai_planner.domain_pack import available_domains, load_pack  # noqa: E402
from packages.ai_planner.role_curator import (  # noqa: E402
    DEFAULT_MIN_CONFIDENCE,
    DEFAULT_ROLE_MAP_PATH,
    curate_roles,
    write_role_map,
)
from packages.ai_planner.roles import (  # noqa: E402
    ROLE_NAMES_ZH,
    ROLE_REVIEW,
    resolve_all,
)
from packages.ai_planner.semantics import load_semantics  # noqa: E402


def _llm() -> object:
    from packages.ai import get_chat_client

    return get_chat_client()


def _force_utf8_stdout() -> None:
    """A Windows console defaults to GBK, which cannot encode ✔/✘.

    Without this the run crashes *after* the model calls have been made — the
    proposals are computed and then lost to a print, which is the worst possible
    place to fail.
    """
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is None:
        return
    try:
        reconfigure(encoding="utf-8", errors="replace")
    except (ValueError, OSError):  # pragma: no cover - non-reconfigurable stream
        return


def main(argv: list[str] | None = None) -> int:
    _force_utf8_stdout()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--domain", choices=available_domains(), default="audit")
    parser.add_argument("--limit", type=int, default=None, help="最多询问多少个插件")
    parser.add_argument("--min-confidence", type=float, default=DEFAULT_MIN_CONFIDENCE)
    parser.add_argument("--all", action="store_true", help="对全部插件问一遍（默认只问仍未判定的）")
    parser.add_argument("--write", action="store_true", help="写入角色表")
    parser.add_argument("--out", type=Path, default=None, help="角色表输出路径（默认 contracts/semantics/role-map.json）")
    parser.add_argument("--dry-run", action="store_true", help="只打印，不写文件（--write 的反面，默认行为）")
    args = parser.parse_args(argv)

    pack = load_pack(args.domain)
    specs = discover_plugins(globs=pack.globs)
    catalog = load_semantics()
    assignments = resolve_all(specs, catalog=catalog)
    pending = [p for p in sorted(specs) if assignments[p].role == ROLE_REVIEW]
    scope = len(specs) if args.all else len(pending)
    print(f"# 角色策展 — 领域={args.domain}  插件={len(specs)}  本次询问={min(scope, args.limit or scope)}")

    try:
        proposals = curate_roles(
            specs, pack, _llm(), catalog=catalog,
            only_review=not args.all, min_confidence=args.min_confidence, limit=args.limit,
        )
    except Exception as exc:  # noqa: BLE001 - surfaced, never swallowed
        print(f"模型不可用，已 fail-closed：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    accepted = [p for p in proposals if p.accepted]
    rejected = [p for p in proposals if not p.accepted]
    for proposal in accepted:
        print(f"  ✔ {proposal.plugin_id:<44} {ROLE_NAMES_ZH.get(proposal.role, proposal.role):<8} "
              f"{proposal.confidence:.2f}  {proposal.reason[:48]}")
    for proposal in rejected:
        print(f"  ✘ {proposal.plugin_id:<44} {proposal.rejection}")
    print(f"\n通过 {len(accepted)} / 拒绝 {len(rejected)}（拒绝项会留在 rejected 里供人工复核）")

    if args.write:
        previous = None
        target = args.out or DEFAULT_ROLE_MAP_PATH
        if target.exists():
            import json

            previous = json.loads(target.read_text(encoding="utf-8"))
        payload = write_role_map(target, proposals, domain=args.domain, previous=previous)
        print(f"已写入 {target}（accepted={len(payload['roles'])} rejected={len(payload['rejected'])}）")
        print("注意：reviewed_by=ai-assisted —— 条目仍未经人工签署，理由与置信度已随文件保存，可逐条审计。")
    else:
        print("（dry-run：未写任何文件；加 --write 才写入）")

    if not accepted:
        print("没有任何提议通过闸门 —— 未改动任何分类。", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
