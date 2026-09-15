"""CW2 contract tests: persistent log spool / segment / archive base.

Acceptance (方案 CW2 退出条件, :434):
  - rotation and restart continuity;
  - UTF-8 half-line handling (incremental decoding, no mojibake);
  - duplicate delivery dedup (event_id / producer seq);
  - collection continues after the GUI closes (independent collector);
  - spool write failure pauses admission (back-pressure, fail visible).

Also covers 方案 8.4/8.5 essentials: stdout/stderr split, seal + persisted
acknowledgement watermark, completeness (complete/partial/unknown_tail),
hot-rotation order (seal -> verify -> archive -> index -> evict), and
query with producer/level/keyword/run filters + pagination cursor.
"""

from __future__ import annotations

import gzip
import hashlib
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest

from packages.observability.log_spool import (
    LogEnvelope,
    SegmentedSpool,
    SpoolWriteError,
    StreamDecoder,
)


def _env(producer: str, seq: int, message: str, *, level: str = "info",
         epoch: int = 1, stream: str = "stdout", run_id: str | None = None) -> LogEnvelope:
    return LogEnvelope(
        event_id=f"evt-{producer}-{epoch}-{seq}-{stream}",
        producer_id=producer,
        producer_epoch=epoch,
        seq=seq,
        timestamp=f"2026-09-09T00:00:{seq:02d}+00:00",
        level=level,
        source="test",
        message=message,
        run_id=run_id,
        stream=stream,
    )


def test_segment_rotation_preserves_all_events(tmp_path: Path) -> None:
    spool = SegmentedSpool(tmp_path / "spool", max_segment_bytes=300)
    written: list[str] = []
    for i in range(1, 21):
        msg = f"事件行-{i}-" + "x" * 40
        spool.append("p1", 1, [_env("p1", i, msg)])
        written.append(msg)
    spool.close()

    segments = spool.segments()
    assert len(segments) > 1  # rotation actually happened
    # first/last seq are contiguous across segments
    seqs = sorted((s.first_seq, s.last_seq) for s in segments)
    for (prev_last, _), (next_first, _) in zip(seqs, seqs[1:]):
        assert next_first == prev_last + 1

    read_back = spool.query(producer_id="p1", limit=1000)["events"]
    assert [e["message"] for e in read_back] == written  # 原文与序号一致
    # byte boundary: every event survives verbatim
    for ev in read_back:
        assert ev["message"].startswith("事件行-")


def test_utf8_split_across_chunks_no_mojibake() -> None:
    decoder = StreamDecoder()
    data = "中文日志跨块拆分不产生乱码；英文ASCII稳定。\n".encode("utf-8")
    mid = len(data) // 2
    lines = decoder.feed(data[:mid])
    assert lines == []  # no complete line yet (half-line pending)
    lines = decoder.feed(data[mid:])
    assert lines == ["中文日志跨块拆分不产生乱码；英文ASCII稳定。"]
    # the multi-byte char split across the chunk boundary decoded intact
    assert "乱码" in lines[0]


def test_half_line_finish_returns_remainder() -> None:
    decoder = StreamDecoder()
    out = decoder.feed("第一行\n第二行无换行".encode("utf-8"))
    assert out == ["第一行"]
    tail = decoder.finish()
    assert tail == ["第二行无换行"]  # half-line remainder is not lost


def test_restart_epoch_seq_continuity(tmp_path: Path) -> None:
    root = tmp_path / "spool"
    s1 = SegmentedSpool(root)
    for i in range(1, 4):
        s1.append("svc", 1, [_env("svc", i, f"before-{i}")])
    s1.seal("svc", 1, last_seq=3, bytes_count=None)
    s1.close()

    # restart: new epoch, seq continues (never resets)
    s2 = SegmentedSpool(root)
    for i in range(4, 7):
        s2.append("svc", 2, [_env("svc", i, f"after-{i}", epoch=2)])
    s2.close()

    events = s2.query(producer_id="svc", limit=1000)["events"]
    assert [e["seq"] for e in events] == [1, 2, 3, 4, 5, 6]
    assert [e["message"] for e in events] == ["before-1", "before-2", "before-3", "after-4", "after-5", "after-6"]


def test_duplicate_event_id_dedup(tmp_path: Path) -> None:
    spool = SegmentedSpool(tmp_path / "spool")
    ev = _env("dup", 1, "only-once")
    spool.append("dup", 1, [ev])
    spool.append("dup", 1, [ev])  # duplicate delivery (same event_id + seq)
    spool.append("dup", 1, [_env("dup", 2, "second")])
    spool.close()
    events = spool.query(producer_id="dup", limit=100)["events"]
    assert [e["seq"] for e in events] == [1, 2]
    assert events[0]["message"] == "only-once"


def test_gui_closed_collection_continues(tmp_path: Path) -> None:
    """An independent process keeps writing the spool; the reader (GUI side)
    only consumes — closing the GUI never stops collection."""
    root = tmp_path / "spool"
    script = (
        "import sys; sys.path.insert(0, r'%s'); "
        "from packages.observability.log_spool import SegmentedSpool, LogEnvelope; "
        "from uuid import uuid4; "
        "s = SegmentedSpool(r'%s'); "
        "s.append('worker', 1, [LogEnvelope(event_id='evt-%s', producer_id='worker', "
        "producer_epoch=1, seq=1, timestamp='2026-09-09T00:00:01+00:00', level='info', "
        "source='worker', message='gui-independent')]); s.close()"
    ) % (str(PROJECT_ROOT).replace("'", "\\'"), str(root).replace("'", "\\'"), uuid4().hex[:8])
    subprocess.run([sys.executable, "-c", script], check=True, capture_output=True, timeout=120)

    reader = SegmentedSpool(root)
    events = reader.query(producer_id="worker", limit=10)["events"]
    assert len(events) == 1
    assert events[0]["message"] == "gui-independent"


def test_spool_write_failure_pauses_acceptance(tmp_path: Path) -> None:
    root = tmp_path / "spool"
    hot_dir = root / "hot" / "p1" / "e1"
    hot_dir.parent.mkdir(parents=True, exist_ok=True)
    hot_dir.write_text("i-am-a-file", encoding="utf-8")  # occupy the directory slot
    spool = SegmentedSpool(root)
    with pytest.raises(SpoolWriteError):
        spool.append("p1", 1, [_env("p1", 1, "boom")])
    assert spool.accepting is False
    assert spool.last_error  # failure is visible, never swallowed


def test_archive_flow_seal_verify_manifest_then_evict(tmp_path: Path) -> None:
    root = tmp_path / "spool"
    spool = SegmentedSpool(root, max_segment_bytes=200)
    for i in range(1, 6):
        spool.append("arc", 1, [_env("arc", i, f"row-{i}")])
    spool.seal("arc", 1, last_seq=5, bytes_count=None)
    spool.close()

    manifest = spool.segments()
    assert manifest, "manifest must record every segment"
    for meta in manifest:
        assert meta.archive_state == "archived"
        assert len(meta.sha256) == 64
        archive = root / "archive" / meta.producer_id / f"e{meta.producer_epoch}" / f"{meta.segment_id}.jsonl.gz"
        assert archive.is_file(), "archive copy must exist before hot eviction"
        with gzip.open(archive, "rb") as fh:
            content = fh.read()
        assert hashlib.sha256(content).hexdigest() == meta.sha256  # 校验
        hot = root / "hot" / meta.producer_id / f"e{meta.producer_epoch}" / f"{meta.segment_id}.jsonl"
        assert not hot.exists(), "hot copy evicted only after archive + index committed"
    events = spool.query(producer_id="arc", limit=100)["events"]
    assert [e["seq"] for e in events] == [1, 2, 3, 4, 5]


def test_query_filter_and_pagination(tmp_path: Path) -> None:
    spool = SegmentedSpool(tmp_path / "spool")
    for i in range(1, 8):
        spool.append("q", 1, [
            _env("q", i * 2 - 1, f"info-{i}", level="info", run_id=f"run-{i % 2}"),
            _env("q", i * 2, f"warn-{i}", level="warning", run_id=f"run-{i % 2}"),
        ])
    spool.close()

    info = spool.query(producer_id="q", level="info", limit=100)["events"]
    assert [e["message"] for e in info] == [f"info-{i}" for i in range(1, 8)]

    run1 = spool.query(run_id="run-1", limit=100)["events"]
    assert run1 and all(e["run_id"] == "run-1" for e in run1)

    kw = spool.query(producer_id="q", keyword="warn-3", limit=100)["events"]
    assert len(kw) == 1 and kw[0]["message"] == "warn-3"

    page1 = spool.query(producer_id="q", limit=3)["events"]
    assert len(page1) == 3
    page2 = spool.query(producer_id="q", limit=3, after_seq=page1[-1]["seq"])["events"]
    assert page2 and page2[0]["seq"] == 4


def test_completeness_seal_and_unknown_tail(tmp_path: Path) -> None:
    root = tmp_path / "spool"
    sealed = SegmentedSpool(root)
    for i in range(1, 4):
        sealed.append("good", 1, [_env("good", i, f"g-{i}")])
    sealed.seal("good", 1, last_seq=3, bytes_count=None)
    sealed.close()
    result = sealed.query(producer_id="good", limit=100)
    assert result["completeness"] == "complete"
    assert result["persisted_seq"] == 3
    assert result["unknown_tail"] is False

    # crashed producer: no seal -> partial + unknown_tail
    crashed = SegmentedSpool(root)
    for i in range(1, 3):
        crashed.append("crash", 1, [_env("crash", i, f"c-{i}")])
    crashed.close()  # closed without seal, simulating a crash
    result = crashed.query(producer_id="crash", limit=100)
    assert result["completeness"] == "partial"
    assert result["unknown_tail"] is True


def test_stderr_events_persist_and_query(tmp_path: Path) -> None:
    """stderr is a first-class structured stream, never dropped."""
    spool = SegmentedSpool(tmp_path / "spool")
    for i in range(1, 4):
        spool.append("svc", 1, [
            _env("svc", i, f"stdout-{i}", stream="stdout"),
            _env("svc", i, f"stderr-{i}", stream="stderr", level="warning"),
        ])
    spool.close()
    errs = spool.query(stream="stderr", limit=100)["events"]
    assert [e["message"] for e in errs] == ["stderr-1", "stderr-2", "stderr-3"]
    assert all(e["stream"] == "stderr" for e in errs)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
