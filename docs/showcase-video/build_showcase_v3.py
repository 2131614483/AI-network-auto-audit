# -*- coding: utf-8 -*-
"""Build the audit_network showcase video v3: real screenshots + Ken Burns + Chinese subtitles + TTS voiceover."""
import os
import subprocess
import sys
import json

ROOT = r"D:\pythonpro\audit_network"
SHOTS = os.path.join(ROOT, "docs", "screenshots")
OUT = os.path.join(ROOT, "docs", "showcase-video")
SEG = os.path.join(OUT, "segments")
AUD = os.path.join(OUT, "audio")
os.makedirs(SEG, exist_ok=True)

FONT_BOLD = "C\\:/Windows/Fonts/msyhbd.ttc"
FPS = 30
W, H = 1920, 1080
BG = "0x0b0e14"

# (kind, image_or_None, subtitle, voice_file_or_None)
# 分镜与文案见 storyboard-v2.md。语音已在 audio/ 目录。
CLIPS = [
    ("title", None, "审计智能中枢  audit_network", "voice_01.wav"),
    ("shot", "desktop/hub.png", "信息中枢：全站只读快照，断环与缺口如实标红", "voice_02.wav"),
    ("shot", "desktop/operations.png", "Mission → Workflow → Task → Agent  四层运行投影", "voice_03.wav"),
    ("shot", "desktop/runs.png", "126 次运行与产物库：证据包、项目锚、可复验", "voice_04.wav"),
    ("shot", "desktop/diagnose.png", "63 次失败归成 15 个错误签名簇", "voice_05.wav"),
    ("shot", "desktop/runcanvas.png", "一次真实组网：100 个节点、71 条数据边", "voice_06.wav"),
    ("shot", "desktop/runcanvas-node.png", "点开任一节点：数据从哪来、产出什么、落在哪", "voice_07.wav"),
    ("shot", "desktop/graph-tiers.png", "能力图谱：147 个节点按层级上色", "voice_08.wav"),
    ("shot", "desktop/graph-relations.png", "每条关系都有文字依据，不只是权重", "voice_09.wav"),
    ("shot", "desktop/connectivity.png", "连通性与供需闭合：谁悬空、缺什么上游", "voice_10.wav"),
    ("shot", "desktop/knowledge.png", "文档入库 / 分块 / 向量化全链路", "voice_11.wav"),
    ("shot", "desktop/library.png", "143 份文档资产索引，带关联 run 与案例", "voice_12.wav"),
    ("shot", "desktop/nebula.png", "知识星云：插件按业务阶段组成星系", "voice_13.wav"),
    ("shot", "desktop/plugins.png", "插件拓扑工作台：16 集群 / 10 蓝图 / 10 契约", "voice_14.wav"),
    ("shot", "desktop/plugin-catalog.png", "123 个插件的端口契约与生命周期一览", "voice_15.wav"),
    ("shot", "desktop/policy.png", "策略网关：209 个策略集，182 个疑似残留标红", "voice_16.wav"),
    ("shot", "desktop/approvals.png", "高风险动作先落审批，账本可查", "voice_17.wav"),
    ("shot", "desktop/audit.png", "审计工作台：50 项目 / 16 已确认 / 102 待确认", "voice_18.wav"),
    ("shot", "desktop/quant.png", "回测全部 simulated_only，指标可追溯", "voice_19.wav"),
    ("shot", "desktop/aiops.png", "AIOps：告警 → 提案 → 变更授权 → 模拟执行 → 核验", "voice_20.wav"),
    ("shot", "desktop/evidence.png", "证据链复验：重算 sha256，不落盘", "voice_21.wav"),
    ("shot", "desktop/evolution.png", "七环闭环 1→7 全部打通", "voice_22.wav"),
    ("shot", "desktop/suggestions.png", "设计外的真实协作被识别为候选关系", "voice_23.wav"),
    ("shot", "desktop/health.png", "健康与自检：每一项都标注数据来源", "voice_24.wav"),
    ("end", None, "七环全通 · 203 条图谱关系 · 1200+ 自动化测试", "voice_25.wav"),
]

CARD_SUBTITLES = {
    "title": "把 AI「会胡说 · 会越权 · 结果不可信」收敛成可授权、可追溯、可复算、可回滚",
    "end": "过程可被复核 —— 本机私有化的审计 / AIOps 智能中枢",
}

# Extra silence after each voice (seconds)
TAIL_SILENCE = 0.6


def probe_duration(path):
    r = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", path],
        capture_output=True, text=True,
    )
    info = json.loads(r.stdout)
    return float(info["format"]["duration"])


def write_textfile(name, text):
    p = os.path.join(SEG, name)
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)
    return f"segments/{name}"


def run(cmd):
    print(">>>", " ".join(str(c) for c in cmd[:4]), "...")
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        print("STDERR:", r.stderr[-2000:])
        sys.exit(1)
    return r


def build_shot(img_rel, subtitle, voice_path, idx):
    img = os.path.join(SHOTS, img_rel)
    dur = probe_duration(voice_path) + TAIL_SILENCE
    frames = int(dur * FPS)
    txt = write_textfile(f"sub_{idx}.txt", subtitle)
    out = os.path.join(SEG, f"seg_{idx:02d}.mp4")
    vf = (
        f"scale={W}:{H}:force_original_aspect_ratio=decrease:flags=lanczos,"
        f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color={BG},"
        f"drawtext=fontfile='{FONT_BOLD}':textfile='{txt}':fontcolor=white:fontsize=40:"
        f"borderw=2:bordercolor=black@0.85:x=(w-text_w)/2:y=h-120,"
        f"fade=t=in:st=0:d=0.4,fade=t=out:st={dur-0.4}:d=0.4,format=yuv420p"
    )
    cmd = [
        "ffmpeg", "-y", "-loop", "1", "-i", img,
        "-i", voice_path,
        "-vf", vf,
        "-r", str(FPS), "-frames:v", str(frames),
        "-af", f"apad=pad_dur={TAIL_SILENCE},aformat=sample_rates=44100:channel_layouts=stereo",
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-c:a", "aac", "-b:a", "128k",
        "-pix_fmt", "yuv420p", "-shortest",
        out,
    ]
    run(cmd)
    return out, dur


def build_card(kind, main, voice_path, idx, main_size=64, sub_size=36):
    dur = probe_duration(voice_path) + TAIL_SILENCE
    frames = int(dur * FPS)
    main_txt = write_textfile(f"card_main_{idx}.txt", main)
    sub_txt = write_textfile(f"card_sub_{idx}.txt", CARD_SUBTITLES[kind])
    out = os.path.join(SEG, f"seg_{idx:02d}.mp4")
    vf = (
        f"drawtext=fontfile='{FONT_BOLD}':textfile='{main_txt}':fontcolor=white:fontsize={main_size}:"
        f"borderw=2:bordercolor=black:x=(w-text_w)/2:y=(h-text_h)/2-60,"
        f"drawtext=fontfile='{FONT_BOLD}':textfile='{sub_txt}':fontcolor=0x7fb8ff:fontsize={sub_size}:"
        f"borderw=1:bordercolor=black:x=(w-text_w)/2:y=(h-text_h)/2+40,"
        f"fade=t=in:st=0:d=0.6,fade=t=out:st={dur-0.5}:d=0.5,format=yuv420p"
    )
    cmd = [
        "ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c={BG}:s={W}x{H}:d={dur}:r={FPS}",
        "-i", voice_path,
        "-vf", vf,
        "-af", f"apad=pad_dur={TAIL_SILENCE},aformat=sample_rates=44100:channel_layouts=stereo",
        "-r", str(FPS),
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-c:a", "aac", "-b:a", "128k",
        "-pix_fmt", "yuv420p", "-shortest",
        out,
    ]
    run(cmd)
    return out, dur


def main():
    os.chdir(OUT)
    seg_files = []
    total = 0.0
    for i, (kind, img, sub, voice_file) in enumerate(CLIPS):
        voice_path = os.path.join(AUD, voice_file)
        if kind == "shot":
            f, d = build_shot(img, sub, voice_path, i)
        elif kind == "title":
            f, d = build_card("title", sub, voice_path, i, main_size=60, sub_size=34)
        elif kind == "end":
            f, d = build_card("end", sub, voice_path, i, main_size=56, sub_size=34)
        seg_files.append(f)
        total += d
        print(f"  seg {i:02d}: {d:.1f}s  total={total:.1f}s")

    # concat list
    listfile = os.path.join(SEG, "concat.txt")
    with open(listfile, "w", encoding="utf-8") as f:
        for s in seg_files:
            f.write(f"file '{s.replace(chr(92), '/')}'\n")

    final = os.path.join(OUT, "audit-network-showcase-v3.mp4")
    run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", listfile,
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-c:a", "aac", "-b:a", "128k",
        "-pix_fmt", "yuv420p", "-r", str(FPS), final,
    ])
    print(f"DONE: {final}  total={total:.1f}s")


if __name__ == "__main__":
    main()
