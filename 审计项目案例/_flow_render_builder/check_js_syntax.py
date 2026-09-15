# -*- coding: utf-8 -*-
"""语法校验：抽取三份 index.html 中的内联 <script> 块，用 Node 的 new Function 编译检查。"""
import glob
import os
import re
import subprocess

CASE = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
htmls = glob.glob(os.path.join(CASE, "*", "_flow_render", "index.html"))
fails = 0
for p in sorted(htmls):
    src = open(p, encoding="utf-8").read()
    blocks = re.findall(r"<script>(.*?)</script>", src, re.S)
    for i, b in enumerate(blocks):
        tmp = os.path.join(os.environ.get("TEMP", "."), "_chk_%d_%d.js" % (abs(hash(p)) % 100000, i))
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(b)
        r = subprocess.run(["node", "--check", tmp], capture_output=True, text=True)
        if r.returncode != 0:
            fails += 1
            print("FAIL", os.path.basename(os.path.dirname(p)), "block", i)
            print(r.stderr[:600])
        os.remove(tmp)
print("ALL SYNTAX OK" if fails == 0 else ("%d BLOCKS FAILED" % fails), "| checked", len(htmls), "pages")