# -*- coding: utf-8 -*-
"""Build the audit_network showcase video: real screenshots + Ken Burns + Chinese subtitles."""
import subprocess
import os
import sys

ROOT = r"D:\pythonpro\audit_network"
SHOTS = os.path.join(ROOT, "docs", "screenshots")
OUT = os.path.join(ROOT, "docs", "showcase-video")
SEG = os.path.join(OUT, "segments")
os.makedirs(SEG, exist_ok=True)

FONT_BOLD = "C\\:/Windows/Fonts/msyhbd.ttc"
FPS = 30
W, H = 1920, 1080
BG = "0x0b0e14"

# (kind, image_or_None, subtitle, duration_s)
# kind: "shot" = screenshot w/ ken burns ; "title" / "end" = text cards
CLIPS = [
    ("title", None, "审计智能中枢  audit_network", 6),
    ("shot", "desktop/overview.png", "租户隔离 · 策略优先的桌面驾驶舱，控制平面已连接", 7),
    ("shot", "desktop/operations.png", "Mission → Workflow → Task → Agent  四层持久运行投影", 7),
    ("shot", "desktop/ops-detail-4level.png", "逐层下钻到节点、能力、尝试次数与 Token 消耗", 7),
    ("shot", "flow-canvas-frame-1.png", "一次 AI 组网：全员待命，链路未导通", 3.5),
    ("shot", "flow-canvas-frame-2.png", "凭证穿透：金色数据包沿端口飞行", 3.5),
    ("shot", "flow-canvas-frame-3.png", "凭证穿透完成，财务清洗并行推进", 3.5),
    ("shot", "flow-canvas-frame-4.png", "底稿编制汇聚两路输入", 3.5),
    ("shot", "flow-canvas-frame-5.png", "证据索引关联，链路逐段导通", 3.5),
    ("shot", "flow-canvas-frame-6.png", "问题金额核算，接近全通", 3.5),
    ("shot", "flow-canvas-frame-7.png", "全链路导通，每个节点产物带 sha256", 4.5),
    ("shot", "desktop/knowledge.png", "文档入库、分块、向量化全链路；历史只回收不硬删", 7),
    ("shot", "desktop/graph.png", "六图分治，claim–evidence 承载主张与证据", 7),
    ("shot", "desktop/plugins.png", "100 个已验收插件，按三层组织", 7),
    ("shot", "desktop/plugins-shadow-run.png", "影子模拟：不启动子进程、不触达外部系统", 7),
    ("shot", "desktop/policy-result.png", "策略网关 deny-first，未裁决即拒绝（fail-closed）", 7),
    ("shot", "desktop/approvals.png", "高风险动作先落审批，请求方收到 409", 7),
    ("shot", "desktop/audit-lineage.png", "证据链血缘：确认带账户 Trace，锁 sha256 可离线复算", 8),
    ("shot", "desktop/quant-lineage.png", "量化回测 simulated_only，快照与代码哈希共同背书", 7),
    ("shot", "desktop/aiops-detail.png", "AIOps：告警→提案→变更授权→模拟执行→人工核验", 7),
    ("end", None, "1200+ 测试 · 100 插件 · 16 schema · 113 表", 6),
]


def write_textfile(name, text):
    # relative, forward-slash path so ffmpeg filtergraph parses it cleanly
    p = os.path.join(SEG, name)
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)
    return f"segments/{name}"


def run(cmd):
    print(">>>", " ".join(cmd[:3]), "...")
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        print("STDERR:", r.stderr[-2000:])
        sys.exit(1)
    return r


def build_shot(img_rel, subtitle, dur, idx):
    img = os.path.join(SHOTS, img_rel)
    txt = write_textfile(f"sub_{idx}.txt", subtitle)
    frames = int(dur * FPS)
    out = os.path.join(SEG, f"seg_{idx:02d}.mp4")
    vf = (
        f"scale={W}:{H}:force_original_aspect_ratio=decrease:flags=lanczos,"
        f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color={BG},"
        f"zoompan=z='min(zoom+0.0006,1.08)':d={frames}:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={W}x{H}:fps={FPS},"
        f"drawtext=fontfile='{FONT_BOLD}':textfile='{txt}':fontcolor=white:fontsize=40:"
        f"borderw=2:bordercolor=black@0.85:x=(w-text_w)/2:y=h-120,"
        f"fade=t=in:st=0:d=0.4,fade=t=out:st={dur-0.4}:d=0.4,format=yuv420p"
    )
    cmd = [
        "ffmpeg", "-y", "-loop", "1", "-t", f"{dur}", "-i", img,
        "-vf", vf, "-r", str(FPS),
        "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
        out,
    ]
    run(cmd)
    return out


def build_card(main, sub, dur, idx, main_size=64, sub_size=36):
    # main title card / end card: dark bg + big centered title + subtitle line
    main_txt = write_textfile(f"card_main_{idx}.txt", main)
    sub_txt = write_textfile(f"card_sub_{idx}.txt", sub)
    out = os.path.join(SEG, f"seg_{idx:02d}.mp4")
    if idx == 0:
        sub = "把 AI「会胡说 · 会越权 · 结果不可信」收敛成可授权、可追溯、可复算、可回滚"
        sub_txt = write_textfile(f"card_sub_{idx}.txt", sub)
    else:
        sub = "过程可被复核 —— 本机私有化的审计 / 量化 / AIOps 智能中枢"
        sub_txt = write_textfile(f"card_sub_{idx}.txt", sub)
    vf = (
        f"drawtext=fontfile='{FONT_BOLD}':textfile='{main_txt}':fontcolor=white:fontsize={main_size}:"
        f"borderw=2:bordercolor=black:x=(w-text_w)/2:y=(h-text_h)/2-60,"
        f"drawtext=fontfile='{FONT_BOLD}':textfile='{sub_txt}':fontcolor=0x7fb8ff:fontsize={sub_size}:"
        f"borderw=1:bordercolor=black:x=(w-text_w)/2:y=(h-text_h)/2+40,"
        f"fade=t=in:st=0:d=0.6,fade=t=out:st={dur-0.5}:d=0.5,format=yuv420p"
    )
    cmd = [
        "ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c={BG}:s={W}x{H}:d={dur}:r={FPS}",
        "-vf", vf, "-r", str(FPS),
        "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
        out,
    ]
    run(cmd)
    return out


def main():
    os.chdir(OUT)
    seg_files = []
    for i, (kind, img, sub, dur) in enumerate(CLIPS):
        if kind == "shot":
            seg_files.append(build_shot(img, sub, dur, i))
        elif kind == "title":
            seg_files.append(build_card(sub, "", dur, i, main_size=60, sub_size=34))
        elif kind == "end":
            seg_files.append(build_card(sub, "", dur, i, main_size=56, sub_size=34))

    # concat list
    listfile = os.path.join(SEG, "concat.txt")
    with open(listfile, "w", encoding="utf-8") as f:
        for s in seg_files:
            f.write(f"file '{s.replace(chr(92), '/')}'\n")

    final = os.path.join(OUT, "audit-network-showcase.mp4")
    run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", listfile,
        "-c", "copy", final,
    ])
    print("DONE:", final)


if __name__ == "__main__":
    main()
