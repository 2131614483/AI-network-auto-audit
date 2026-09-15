"""Render a composed plugin network as a standalone, offline HTML page.

用法::

    .\\.venv\\Scripts\\python.exe scripts\\render-network.py --domain audit
    .\\.venv\\Scripts\\python.exe scripts\\render-network.py --domain aiops --open

Why a script and not a one-off: the picture has to be regenerable from the
artifacts, otherwise it is decoration.  It composes the domain with the same
`compose_flow` the executor consumes, and draws it with the same layout the
workbench app uses (`packages/ai_planner/render.py`) — so what you see is what
would run, and the two renderings cannot drift apart.

Read-only: it composes and writes one HTML file (``--out``), nothing else.
"""
from __future__ import annotations

import argparse
import html
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from packages.ai_planner.domain_pack import available_domains, load_pack  # noqa: E402
from packages.ai_planner.render import build_view, svg_fragment, table_rows  # noqa: E402
from packages.ai_planner.workbench import new_draft  # noqa: E402

_TEMPLATE = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>组网图 · {domain}</title>
<style>
  .viz-root {{
    color-scheme: light;
    --surface-1:#fcfcfb; --plane:#f4f3f0;
    --text-primary:#0b0b0b; --text-secondary:#52514e; --text-muted:#8a8a7f;
    --layer-base:#eb6834; --layer-biz:#2a78d6; --layer-gov:#1baf7a;
    --rule:#e6e5e1;
  }}
  @media (prefers-color-scheme: dark) {{
    :root:where(:not([data-theme="light"])) .viz-root {{
      color-scheme: dark;
      --surface-1:#1a1a19; --plane:#141413;
      --text-primary:#ffffff; --text-secondary:#c3c2b7; --text-muted:#8a8a7f;
      --layer-base:#d95926; --layer-biz:#3987e5; --layer-gov:#199e70;
      --rule:#33322f;
    }}
  }}
  :root[data-theme="dark"] .viz-root {{
    color-scheme: dark;
    --surface-1:#1a1a19; --plane:#141413;
    --text-primary:#ffffff; --text-secondary:#c3c2b7; --text-muted:#8a8a7f;
    --layer-base:#d95926; --layer-biz:#3987e5; --layer-gov:#199e70;
    --rule:#33322f;
  }}
  * {{ box-sizing:border-box }}
  body {{ margin:0; background:var(--plane); color:var(--text-primary);
         font:14px/1.5 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif }}
  .wrap {{ max-width:2120px; margin:0 auto; padding:28px 24px 64px }}
  h1 {{ font-size:20px; margin:0 0 4px; font-weight:600 }}
  .sub {{ color:var(--text-secondary); margin:0 0 22px }}
  .tiles {{ display:flex; gap:12px; flex-wrap:wrap; margin-bottom:20px }}
  .tile {{ background:var(--surface-1); border:1px solid var(--rule); border-radius:10px;
           padding:12px 16px; min-width:132px }}
  .tile .k {{ color:var(--text-secondary); font-size:12px }}
  .tile .v {{ font-size:22px; font-weight:600; margin-top:2px }}
  .card {{ background:var(--surface-1); border:1px solid var(--rule); border-radius:12px;
           padding:14px 16px; margin-bottom:18px; overflow:auto }}
  .legend {{ display:flex; gap:18px; flex-wrap:wrap; align-items:center; font-size:13px;
             color:var(--text-secondary); margin-bottom:10px }}
  .key {{ display:inline-flex; align-items:center; gap:6px }}
  .swatch {{ width:10px; height:10px; border-radius:50%; display:inline-block }}
  .l-base {{ background:var(--layer-base) }} .l-biz {{ background:var(--layer-biz) }}
  .l-gov {{ background:var(--layer-gov) }}
  .dash {{ width:22px; height:0; border-top:2px solid var(--text-muted);
           border-style:dashed; display:inline-block }}
  .solid {{ width:22px; height:0; border-top:2px solid var(--text-muted); display:inline-block }}
  svg {{ display:block }}
  .colhead {{ fill:var(--text-primary); font-size:13px; font-weight:600 }}
  .colcount {{ fill:var(--text-muted); font-weight:400 }}
  .colrule {{ stroke:var(--rule); stroke-width:1 }}
  .nlabel {{ fill:var(--text-primary); font-size:12px }}
  .nrole  {{ fill:var(--text-secondary); font-size:10px }}
  .dot-base {{ fill:var(--layer-base) }} .dot-biz {{ fill:var(--layer-biz) }}
  .dot-gov {{ fill:var(--layer-gov) }}
  .dot-island {{ fill:var(--surface-1); stroke:var(--text-muted); stroke-width:2;
                 stroke-dasharray:2 2 }}
  .hit {{ fill:transparent }}
  .edge {{ fill:none; stroke-width:1.6; opacity:.75 }}
  .edge-data {{ stroke:var(--text-muted) }}
  .edge-call {{ stroke:var(--text-muted); stroke-dasharray:4 3; opacity:.42 }}
  /* The call edges all run from a business stage to the support layer, i.e. the
     full width of the chart; drawn by default they turn the middle of an
     audit-sized graph into a hairball.  Off by default, one click away. */
  .controls {{ display:flex; gap:16px; align-items:center; margin:0 0 10px;
               font-size:13px; color:var(--text-secondary) }}
  .controls label {{ display:inline-flex; gap:6px; align-items:center; cursor:pointer }}
  .wrap:has(#showcall:not(:checked)) .edge-call {{ display:none }}
  table {{ border-collapse:collapse; width:100%; font-size:12.5px }}
  th,td {{ text-align:left; padding:6px 10px; border-bottom:1px solid var(--rule) }}
  th {{ color:var(--text-secondary); font-weight:600 }}
  details summary {{ cursor:pointer; color:var(--text-secondary); margin-bottom:10px }}
</style></head>
<body><div class="viz-root"><div class="wrap">
  <h1>组网图 · {domain_label}</h1>
  <p class="sub">目标：{goal}　·　plan_key <code>{plan_key}</code>　·　列 = 业务阶段，颜色 = 层</p>
  <div class="tiles">{tiles}</div>
  <div class="controls">
    <label><input type="checkbox" id="showcall"> 显示 call 边（能力调用，不传数据）</label>
    <span>默认只画 data 边：{callcount} 条 call 边横跨整幅图，全开会盖住主链</span>
  </div>
  <div class="card">
    <div class="legend">
      <span class="key"><i class="swatch l-biz"></i>核心业务循环层</span>
      <span class="key"><i class="swatch l-base"></i>数据支撑层</span>
      <span class="key"><i class="swatch l-gov"></i>治理优化层</span>
      <span class="key"><i class="solid"></i>data 边（传数据）</span>
      <span class="key"><i class="dash"></i>call 边（能力调用，不传数据）</span>
      <span class="key"><i class="swatch dot-island" style="border:2px dashed var(--text-muted);
        background:transparent"></i>孤岛（有角色依据）</span>
    </div>
    <svg viewBox="0 0 {width} {height}" width="{width}" height="{height}">{svg}</svg>
  </div>
  <div class="card"><details><summary>表格视图（与图同一份数据）</summary>{table}</details></div>
</div></div></body></html>
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--domain", choices=available_domains(), default="audit")
    parser.add_argument("--goal", default="全流程")
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--open", action="store_true", help="渲染后用默认浏览器打开")
    args = parser.parse_args(argv)

    draft = new_draft(args.domain, args.goal)
    view = build_view(draft, domain=args.domain)
    svg, width, height = svg_fragment(view)
    stats = view["stats"]
    tiles = "".join(
        f'<div class="tile"><div class="k">{k}</div><div class="v">{v}</div></div>'
        for k, v in (
            ("插件", stats["nodes"]), ("边", stats["edges"]),
            ("data 边", stats["data_edges"]), ("call 边", stats["call_edges"]),
            ("种子输入", stats["seeds"]), ("孤岛", stats["islands"]),
        )
    )
    rows = "\n".join(
        f"<tr><td>{html.escape(r['plugin_id'])}</td><td>{html.escape(r['stage'])}</td>"
        f"<td>{html.escape(r['role'])}</td><td>{html.escape(r['island'])}</td></tr>"
        for r in table_rows(view)
    )
    page = _TEMPLATE.format(
        domain=args.domain, domain_label=load_pack(args.domain).name_zh,
        goal=html.escape(args.goal), plan_key=html.escape(str(draft.get("plan_key", ""))),
        tiles=tiles, svg=svg, width=width, height=height, callcount=stats["call_edges"],
        table=(
            "<table><thead><tr><th>插件</th><th>阶段</th><th>角色</th><th>孤岛依据</th></tr></thead>"
            f"<tbody>{rows}</tbody></table>"
        ),
    )
    out = args.out or ROOT / ".data" / "demo" / f"network-{args.domain}.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page, encoding="utf-8", newline="\n")
    print(f"已渲染 {out}")
    print(f"  节点 {stats['nodes']}　边 {stats['edges']}　"
          f"data={stats['data_edges']} call={stats['call_edges']}　种子 {stats['seeds']}")
    if args.open:
        import webbrowser

        webbrowser.open(out.resolve().as_uri())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
