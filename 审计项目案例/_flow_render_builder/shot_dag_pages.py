# -*- coding: utf-8 -*-
"""用无头 Edge 对三份 _flow_render/index.html 逐帧截屏。

每份报告 6 帧：
  f0_init     组网初始（L0 接入处理中）
  f1_flow     数据包在 L0→L1 端口间传递
  f2_parallel 实质性程序并行处理中（含进度计数）
  f3_full     全链路导通（绿色已产出）
  f4_node     打开代表性发现节点的证据卡
  f5_concl    打开结论节点的证据卡
"""
import glob
import json
import os
import re
import subprocess
import sys

EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
CASE = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

NODE_W, HEADER, PORT_ROW, FOOT, LAYER_GAP, ROW_GAP = 196, 40, 15, 14, 150, 26


def load_dag(html_path):
    m = re.search(r"window\.__DAG = (\{.*?\});\n</script>", open(html_path, encoding="utf-8").read(), re.S)
    if not m:
        raise RuntimeError("未找到 __DAG JSON: " + html_path)
    return json.loads(m.group(1))


def estimate_dims(dag):
    edges = dag["edges"]
    in_p, out_p = {}, {}
    for n in dag["nodes"]:
        in_p[n["id"]] = set(); out_p[n["id"]] = set()
    for e in edges:
        in_p[e["to"]].add(e["label"])
        out_p[e["from"]].add(e["label"])
    def ports(side, nid):
        s = in_p[nid] if side == "in" else out_p[nid]
        if side == "in" and not s:
            return ["数据入口"]
        if side == "out" and not s:
            return ["最终输出"]
        return list(s)
    layer_ids = {i: [] for i in range(len(dag["layers"]))}
    for n in dag["nodes"]:
        layer_ids[n["layer"]].append(n["id"])
    nodes = {}
    for n in dag["nodes"]:
        h = HEADER + max(len(ports("in", n["id"])), len(ports("out", n["id"]))) * PORT_ROW + FOOT
        nodes[n["id"]] = h
    layer_h = {}
    for l, ids in layer_ids.items():
        layer_h[l] = sum(nodes[i] for i in ids) + (len(ids) - 1) * ROW_GAP
    max_h = max(layer_h.values())
    vbw = max(layer_ids.keys()) * (NODE_W + LAYER_GAP) + NODE_W + LAYER_GAP + 40
    vbh = max_h + 40
    return vbw, vbh, max_h


def shot(html_path, out_png, t, w, h, sel=None):
    url = "file:///" + html_path.replace("\\", "/")
    url += "#t=%d" % t
    if sel:
        url += "&s=" + sel
    cmd = [EDGE, "--headless=new", "--disable-gpu", "--hide-scrollbars",
           "--no-first-run", "--disable-extensions", "--force-device-scale-factor=1",
           "--user-data-dir=" + os.path.join(os.environ.get("TEMP", "."), "dshot_edge_profile"),
           "--window-size=%d,%d" % (w, h),
           "--screenshot=" + out_png, url]
    r = subprocess.run(cmd, capture_output=True, timeout=120)
    if not os.path.exists(out_png):
        print("FAIL", out_png, r.stderr.decode("utf-8", "ignore")[:500])
        return False
    return True

FRAMES = [  # (t, sel, 帧说明)
    (180, None, "f0_init"),
    (700, None, "f1_flow"),
    (1980, None, "f2_parallel"),
    (0, None, "f3_full"),   # t 用 dur 动态填
    (0, None, "f4_node"),
    (0, None, "f5_concl"),
]

NODE_TARGETS = {
    "黔岭酒业2025年度财务报表审计": ("P13", "P20"),
    "黔岭酒业2025年度财务报表审计_实验组": ("P17", "P32"),
    "黔岭酒业2025年度财务报表审计_实验组_高难度": ("P24", "P28"),
}


def main():
    htmls = glob.glob(os.path.join(CASE, "*", "_flow_render", "index.html"))
    for html_path in sorted(htmls):
        folder = os.path.basename(os.path.dirname(os.path.dirname(html_path)))
        dag = load_dag(html_path)
        dur = len(dag["layers"]) * 860 + 300
        vbw, vbh, max_h = estimate_dims(dag)
        w = vbw + 44 + 330 + 6
        h = vbh + 360
        shots_dir = os.path.join(os.path.dirname(html_path), "shots")
        os.makedirs(shots_dir, exist_ok=True)
        node_s, concl_s = NODE_TARGETS.get(folder, ("Node", "Conclude"))
        jobs = [
            ("f0_init", 180, None),
            ("f1_flow", 700, None),
            ("f2_parallel", 1980, None),
            ("f3_full", dur - 150, None),
            ("f4_node", dur - 1, node_s),
            ("f5_concl", dur - 1, concl_s),
        ]
        print("[%s] W=%d H=%d dur=%d vbw=%d vbh=%d" % (folder, w, h, dur, vbw, vbh))
        for name, t, sel in jobs:
            out_png = os.path.join(shots_dir, name + ".png")
            ok = shot(html_path, out_png, t, w, h, sel)
            print("  %s t=%5d sel=%-4s -> %s %s" % (name, t, sel or "-", os.path.basename(out_png),
                                                    "OK %dKB" % (os.path.getsize(out_png) // 1024) if ok else "FAIL"))
    print("ALL SHOTS DONE")


if __name__ == "__main__":
    sys.exit(main())