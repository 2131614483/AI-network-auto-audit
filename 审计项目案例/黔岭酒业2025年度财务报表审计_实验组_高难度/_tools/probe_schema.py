# -*- coding: utf-8 -*-
"""探查高难度实验组全部 CSV 的表头与结构（只读，不修改任何数据文件）。"""
import csv, os, sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(HERE, "01_被审计单位提供资料")
sys.stdout.reconfigure(encoding="utf-8")

for root, _dirs, files in os.walk(DATA):
    for fn in sorted(files):
        if not fn.lower().endswith(".csv"):
            continue
        path = os.path.join(root, fn)
        rel = os.path.relpath(path, DATA)
        with open(path, encoding="utf-8-sig", newline="") as fh:
            r = list(csv.reader(fh))
        ncols = max(len(x) for x in r) if r else 0
        print(f"===== {rel}  rows={len(r)-1} cols={ncols}")
        if r:
            print("  HEAD:", r[0])
        for row in r[1:4]:
            print("  ROW :", row)