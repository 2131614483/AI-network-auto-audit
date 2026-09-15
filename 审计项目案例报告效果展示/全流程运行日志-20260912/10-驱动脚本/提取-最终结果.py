"""Extract the business outcome of one composed run into 05-最终结果/.

"Final result" is derived, not guessed: a node is *terminal* when no other
node's input bindings reference it as a source.  Those nodes' artifacts are the
flow's leaves — the audit conclusions the main chain actually produces.
"""
from __future__ import annotations

import json
import pathlib

import psycopg2
from psycopg2.extras import RealDictCursor

REPO = pathlib.Path(r"D:\pythonpro\audit_network")
ARCHIVE = REPO / "docs" / "全流程运行日志-20260912"
STAGING = ARCHIVE / "12-本次运行产物"
OUT = ARCHIVE / "05-最终结果"
RUN_ID = "416b0efc-2f18-4c9a-841f-0a1f4370c7c2"
DB = "postgresql://audit_app:admin@localhost:5432/audit_network"


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    conn = psycopg2.connect(DB)
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("select set_config('app.tenant_id',(select id::text from iam.tenants where slug='local-dev'),false)")
    cur.execute("select * from control.node_attempts where run_id=%s order by node_instance_id", (RUN_ID,))
    attempts = [dict(r) for r in cur.fetchall()]
    conn.close()
    plan_key = attempts[0]["plan_key"]

    consumed: set[str] = set()
    for a in attempts:
        for items in (a.get("input_bindings") or {}).values():
            for it in (items if isinstance(items, list) else [items]):
                if isinstance(it, dict) and it.get("source_instance"):
                    consumed.add(str(it["source_instance"]))
    terminal = [a for a in attempts if a["node_instance_id"] not in consumed]

    lines = [
        "# 最终结果（本次全流程运行的业务产出）",
        "",
        f"- run_id: `{RUN_ID}`",
        f"- plan_key: `{plan_key}`",
        f"- 节点总数: {len(attempts)}（成功 {sum(1 for a in attempts if a['status']=='succeeded')}）",
        f"- 终端节点: {len(terminal)} 个（无下游消费者 → 即本流程的最终产出）",
        "",
        "> 判定方式：若某节点从未出现在任何其他节点的 input_bindings.source_instance 中，",
        "> 则它是叶子节点。这不是人工挑选，而是从本次运行的落库数据推导出来的。",
        "",
    ]

    for i, a in enumerate(sorted(terminal, key=lambda x: x["node_instance_id"]), 1):
        node = a["node_instance_id"]
        node_dir = STAGING / "chains" / plan_key / node
        lines.append(f"## {i}. {node}")
        lines.append("")
        lines.append(f"- 能力: `{a['capability']}`")
        lines.append(f"- 插件: `{a['plugin_id']}`")
        lines.append(f"- 状态: {a['status']}    结束: {a['finished_at']}")
        outdir = OUT / f"{i:02d}-{node}"
        outdir.mkdir(parents=True, exist_ok=True)
        for p in sorted(node_dir.glob("output-*.json")):
            try:
                payload = json.loads(p.read_text(encoding="utf-8-sig"))
            except Exception as exc:
                lines.append(f"- 产物 {p.name}: 读取失败 {exc}")
                continue
            (outdir / p.name).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            size = len(p.read_bytes())
            lines.append(f"- 产物 `{p.name}`  {size}B")
            if isinstance(payload, dict):
                for key in ("summary", "status", "recommendation", "promotion", "conclusion"):
                    if key in payload:
                        snippet = json.dumps(payload[key], ensure_ascii=False)
                        lines.append(f"    - {key}: {snippet[:400]}")
        lines.append("")

    (OUT / "最终结果.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"终端节点 {len(terminal)} 个，已写入 {OUT / '最终结果.md'}")
    for a in terminal:
        print(f"  - {a['node_instance_id']:32s} {a['capability']}")


if __name__ == "__main__":
    main()
