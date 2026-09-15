"""CW2 persistent log spool / segment / archive base.

An independent, append-only log collector that does NOT depend on the desktop
or API process staying alive.  Events are written as structured JSONL into
segments owned by ``(producer_id, producer_epoch)``; hot segments rotate in a
safe order (seal -> verify -> archive copy -> index commit -> evict hot copy),
and every segment is recorded in a manifest with ``sha256``.

Key guarantees (方案 CW2 退出条件 / 8.4 / 8.5):
  - rotation and restart continuity: ``seq`` is per producer and continues
    across epochs (a restart never resets it);
  - UTF-8 half-line safety: :class:`StreamDecoder` incrementally decodes byte
    chunks and keeps the pending half-line; the spool never splits a JSON
    line across a segment boundary;
  - duplicate delivery dedup by ``event_id`` and ``(producer, epoch, seq)``;
  - collection is process-independent: any process may append, any other may
    read (GUI closing never stops collection);
  - admission back-pressure: a spool write failure flips ``accepting`` off and
    raises :class:`SpoolWriteError` — a missing evidence run is never shown as
    a complete success;
  - completeness: a producer that sealed (last_seq + persisted watermark) is
    ``complete``; a producer without a seal is ``partial`` with
    ``unknown_tail=True``.
"""

from __future__ import annotations

import codecs
import gzip
import hashlib
import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

CODEC = "jsonl"
_ARCHIVE_SUFFIX = ".jsonl.gz"


class SpoolWriteError(IOError):
    """The spool cannot accept new events (disk full / path conflict / io)."""


@dataclass(frozen=True, slots=True)
class LogEnvelope:
    """One structured log event (方案 8.3 envelope: explicit context, no
    process-local ContextVar smuggling)."""

    event_id: str
    producer_id: str
    producer_epoch: int
    seq: int
    timestamp: str
    level: str
    source: str
    message: str
    trace_id: str | None = None
    run_id: str | None = None
    node_id: str | None = None
    attempt_id: str | None = None
    stream: str = "stdout"
    extra: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "producer_id": self.producer_id,
            "producer_epoch": self.producer_epoch,
            "seq": self.seq,
            "timestamp": self.timestamp,
            "level": self.level,
            "source": self.source,
            "message": self.message,
            "trace_id": self.trace_id,
            "run_id": self.run_id,
            "node_id": self.node_id,
            "attempt_id": self.attempt_id,
            "stream": self.stream,
            "extra": self.extra,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "LogEnvelope":
        return cls(
            event_id=str(data["event_id"]),
            producer_id=str(data["producer_id"]),
            producer_epoch=int(data["producer_epoch"]),
            seq=int(data["seq"]),
            timestamp=str(data["timestamp"]),
            level=str(data.get("level") or "info"),
            source=str(data.get("source") or "-"),
            message=str(data.get("message") or ""),
            trace_id=data.get("trace_id"),
            run_id=data.get("run_id"),
            node_id=data.get("node_id"),
            attempt_id=data.get("attempt_id"),
            stream=str(data.get("stream") or "stdout"),
            extra=data.get("extra"),
        )


@dataclass(frozen=True, slots=True)
class SegmentMeta:
    """Per-segment manifest record (方案 :270 required fields)."""

    segment_id: str
    producer_id: str
    producer_epoch: int
    first_seq: int
    last_seq: int
    bytes: int
    sha256: str
    codec: str
    created_at: str
    archive_state: str = "sealed"


class StreamDecoder:
    """Incremental UTF-8 line decoder: cross-chunk multi-byte characters are
    never garbled and a trailing half-line is retained for the next feed or
    returned by :meth:`finish` (方案 CW2 UTF-8 half-line)."""

    def __init__(self) -> None:
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self._pending = ""

    def feed(self, data: bytes) -> list[str]:
        text = self._decoder.decode(data)
        lines = (self._pending + text).split("\n")
        self._pending = lines.pop()
        return lines

    def finish(self) -> list[str]:
        text = self._decoder.decode(b"", final=True)
        combined = self._pending + text
        if not combined:
            return []
        lines = combined.split("\n")
        self._pending = lines.pop()
        out = lines
        if self._pending:
            out = [*lines, self._pending]
            self._pending = ""
        return out


class _SegmentWriter:
    def __init__(self, root: Path, producer_id: str, producer_epoch: int, segment_seq: int) -> None:
        self.root = root
        self.producer_id = producer_id
        self.producer_epoch = producer_epoch
        self.segment_id = f"{producer_id}-e{producer_epoch}-s{segment_seq:04d}"
        self.dir = root / "hot" / producer_id / f"e{producer_epoch}"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / f"{self.segment_id}.jsonl"
        self.file = self.path.open("ab", buffering=0)
        self.bytes = 0
        self.sha = hashlib.sha256()
        self.first_seq: int | None = None
        self.last_seq: int | None = None
        self.created_at = datetime.now(timezone.utc).isoformat()

    def write_line(self, raw: bytes, seq: int) -> None:
        try:
            self.file.write(raw)
        except OSError as exc:
            raise SpoolWriteError(f"spool write failed: {exc}") from exc
        self.bytes += len(raw)
        self.sha.update(raw)
        if self.first_seq is None:
            self.first_seq = seq
        self.last_seq = seq

    def sync(self) -> None:
        try:
            self.file.flush()
            os.fsync(self.file.fileno())
        except OSError as exc:
            raise SpoolWriteError(f"spool fsync failed: {exc}") from exc

    def close(self) -> None:
        try:
            self.file.flush()
            os.fsync(self.file.fileno())
            self.file.close()
        except OSError:
            self.file.close()


class SegmentedSpool:
    """Append-only segmented JSONL spool with rotation, archive, dedup and
    query.  Not thread-safe by design: one writer per producer per process."""

    def __init__(
        self,
        root: str | Path,
        *,
        max_segment_bytes: int = 4 * 1024 * 1024,
        fsync: bool = True,
    ) -> None:
        self.root = Path(root)
        self.max_segment_bytes = int(max_segment_bytes)
        self.fsync = fsync
        self.accepting = True
        self.last_error: str | None = None
        self._writers: dict[tuple[str, int], _SegmentWriter] = {}
        self._next_segment_seq: dict[tuple[str, int], int] = {}
        self._seen_keys: set[tuple[str, int, int, str]] = set()
        self._seen_event_ids: set[str] = set()
        self._sealed: set[tuple[str, int]] = set()
        self._closed = False

    # -- write path ------------------------------------------------------------

    def append(self, producer_id: str, producer_epoch: int, events: list[LogEnvelope]) -> None:
        if self._closed:
            raise SpoolWriteError("spool is closed")
        if not self.accepting:
            raise SpoolWriteError(f"spool admission paused: {self.last_error or 'unknown'}")
        key = (producer_id, producer_epoch)
        writer = self._writers.get(key)
        if writer is None:
            writer = self._new_writer(key)
        for event in events:
            dedup_key = (producer_id, producer_epoch, event.seq, event.stream)
            if dedup_key in self._seen_keys or event.event_id in self._seen_event_ids:
                continue  # duplicate delivery is dropped deterministically
            line = json.dumps(event.as_dict(), ensure_ascii=False, sort_keys=True) + "\n"
            raw = line.encode("utf-8")
            if writer.bytes and writer.bytes + len(raw) > self.max_segment_bytes:
                self._rotate(writer, key)
                writer = self._new_writer(key)
            try:
                writer.write_line(raw, event.seq)
            except SpoolWriteError as exc:
                self._fail(exc)
                raise
            self._seen_keys.add(dedup_key)
            self._seen_event_ids.add(event.event_id)
        if self.fsync:
            try:
                writer.sync()
            except SpoolWriteError as exc:
                self._fail(exc)
                raise

    def seal(
        self,
        producer_id: str,
        producer_epoch: int,
        *,
        last_seq: int | None = None,
        bytes_count: int | None = None,
    ) -> None:
        key = (producer_id, producer_epoch)
        writer = self._writers.get(key)
        if writer is not None:
            writer.sync()
        seal_dir = self.root / "seals" / producer_id
        seal_dir.mkdir(parents=True, exist_ok=True)
        actual_last = last_seq if last_seq is not None else (int(writer.last_seq or 0) if writer else 0)
        actual_bytes = bytes_count if bytes_count is not None else (writer.bytes if writer else 0)
        record = {
            "producer_id": producer_id,
            "producer_epoch": producer_epoch,
            "last_seq": int(actual_last),
            "bytes_count": int(actual_bytes),
            "persisted_seq": int(actual_last),
            "sealed_at": datetime.now(timezone.utc).isoformat(),
        }
        (seal_dir / f"e{producer_epoch}.json").write_text(
            json.dumps(record, ensure_ascii=False, sort_keys=True), encoding="utf-8"
        )
        self._sealed.add(key)

    def close(self) -> None:
        # rotate any un-rotated hot segment (seal -> verify -> archive ->
        # manifest -> evict) so every accepted event survives a reader that
        # opens the spool later from another process (GUI-independent).
        for key in list(self._writers):
            writer = self._writers[key]
            if writer.bytes:
                self._rotate(writer, key)
            else:
                writer.close()
                del self._writers[key]
        self._closed = True

    def _new_writer(self, key: tuple[str, int]) -> _SegmentWriter:
        seq = self._next_segment_seq.get(key, 1)
        self._next_segment_seq[key] = seq + 1
        try:
            writer = _SegmentWriter(self.root, key[0], key[1], seq)
        except OSError as exc:
            self._fail(SpoolWriteError(f"spool cannot create segment: {exc}"))
            raise SpoolWriteError(f"spool cannot create segment: {exc}") from exc
        self._writers[key] = writer
        return writer

    def _rotate(self, writer: _SegmentWriter, key: tuple[str, int]) -> None:
        # 1) seal the hot segment: flush + fsync
        writer.sync()
        # 2) verify: content sha must match what we accumulated while writing
        disk_sha = hashlib.sha256(writer.path.read_bytes()).hexdigest()
        if disk_sha != writer.sha.hexdigest():
            raise SpoolWriteError(f"segment {writer.segment_id} failed content verification")
        # 3) archive copy (gzip) before any eviction
        archive_dir = self.root / "archive" / key[0] / f"e{key[1]}"
        archive_dir.mkdir(parents=True, exist_ok=True)
        archive_path = archive_dir / f"{writer.segment_id}{_ARCHIVE_SUFFIX}"
        with writer.path.open("rb") as src, gzip.open(archive_path, "wb") as dst:
            dst.write(src.read())
        # 4) index commit: append the manifest record
        meta = SegmentMeta(
            segment_id=writer.segment_id,
            producer_id=key[0],
            producer_epoch=key[1],
            first_seq=int(writer.first_seq or 0),
            last_seq=int(writer.last_seq or 0),
            bytes=writer.bytes,
            sha256=writer.sha.hexdigest(),
            codec=CODEC,
            created_at=writer.created_at,
            archive_state="archived",
        )
        manifest = self.root / "manifest.jsonl"
        manifest.parent.mkdir(parents=True, exist_ok=True)
        with manifest.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(asdict(meta), ensure_ascii=False, sort_keys=True) + "\n")
        # 5) only now evict the hot copy
        writer.close()
        try:
            writer.path.unlink()
        except OSError:
            pass
        del self._writers[key]

    def _fail(self, exc: SpoolWriteError) -> None:
        self.accepting = False
        self.last_error = str(exc)

    # -- read path -------------------------------------------------------------

    def segments(self) -> list[SegmentMeta]:
        manifest = self.root / "manifest.jsonl"
        metas: list[SegmentMeta] = []
        if manifest.is_file():
            for line in manifest.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    metas.append(SegmentMeta(**json.loads(line)))
                except (json.JSONDecodeError, TypeError):
                    continue
        for writer in self._writers.values():
            metas.append(
                SegmentMeta(
                    segment_id=writer.segment_id,
                    producer_id=writer.producer_id,
                    producer_epoch=writer.producer_epoch,
                    first_seq=int(writer.first_seq or 0),
                    last_seq=int(writer.last_seq or 0),
                    bytes=writer.bytes,
                    sha256=writer.sha.hexdigest(),
                    codec=CODEC,
                    created_at=writer.created_at,
                    archive_state="sealed" if (writer.producer_id, writer.producer_epoch) in self._sealed else "open",
                )
            )
        # Cross-process visibility: an open hot segment written by another
        # process (API/worker with an attached spool handler) is not in the
        # manifest yet, so a fresh reader would miss it.  Scan hot dirs so
        # trace-locate can query events still being collected (24x7 wiring).
        known = {meta.segment_id for meta in metas}
        try:
            hot_files = sorted((self.root / "hot").glob("*/*/*.jsonl"))
        except OSError:
            hot_files = []
        for path in hot_files:
            if path.stem in known:
                continue
            try:
                epoch = int(path.parent.name[1:])
            except ValueError:
                continue
            try:
                created_at = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()
                size_bytes = path.stat().st_size
            except OSError:
                continue
            metas.append(
                SegmentMeta(
                    segment_id=path.stem,
                    producer_id=path.parent.parent.name,
                    producer_epoch=epoch,
                    first_seq=0,
                    last_seq=0,
                    bytes=size_bytes,
                    sha256="",
                    codec=CODEC,
                    created_at=created_at,
                    archive_state="open",
                )
            )
        metas.sort(key=lambda m: (m.producer_id, m.producer_epoch, m.first_seq, m.segment_id))
        return metas

    def active_segment_ids(self) -> set[str]:
        """Segment ids of currently open writers (not yet closed/rotated)."""
        return {writer.segment_id for writer in self._writers.values()}

    def query(
        self,
        *,
        producer_id: str | None = None,
        epoch: int | None = None,
        level: str | None = None,
        stream: str | None = None,
        run_id: str | None = None,
        node_id: str | None = None,
        trace_id: str | None = None,
        keyword: str | None = None,
        since: str | None = None,
        until: str | None = None,
        limit: int = 100,
        after_seq: int | None = None,
    ) -> dict[str, Any]:
        events: list[dict[str, Any]] = []
        seen: set[tuple[str, int, int, str]] = set()
        for path, meta in self._iter_segments(producer_id, epoch):
            for record in self._read_segment(path):
                env = LogEnvelope.from_dict(record)
                if not self._matches(env, producer_id, epoch, level, stream, run_id, node_id, trace_id, keyword, since, until):
                    continue
                key = (env.producer_id, env.producer_epoch, env.seq, env.stream)
                if key in seen:
                    continue
                seen.add(key)
                if after_seq is not None and env.seq <= after_seq:
                    continue
                events.append(env.as_dict())
        events.sort(key=lambda e: (e["producer_id"], e["producer_epoch"], e["seq"]))
        if limit and len(events) > limit:
            events = events[:limit]
        return self._query_result(events, producer_id, epoch)

    def _iter_segments(self, producer_id: str | None, epoch: int | None) -> Iterator[tuple[Path, SegmentMeta]]:
        for meta in self.segments():
            if producer_id is not None and meta.producer_id != producer_id:
                continue
            if epoch is not None and meta.producer_epoch != epoch:
                continue
            if meta.archive_state == "archived":
                path = self.root / "archive" / meta.producer_id / f"e{meta.producer_epoch}" / f"{meta.segment_id}{_ARCHIVE_SUFFIX}"
                yield path, meta
            else:
                path = self.root / "hot" / meta.producer_id / f"e{meta.producer_epoch}" / f"{meta.segment_id}.jsonl"
                yield path, meta

    @staticmethod
    def _read_segment(path: Path) -> list[dict[str, Any]]:
        if str(path).endswith(_ARCHIVE_SUFFIX):
            with gzip.open(path, "rt", encoding="utf-8") as fh:
                text = fh.read()
        else:
            try:
                text = path.read_text(encoding="utf-8")
            except FileNotFoundError:
                return []
        records: list[dict[str, Any]] = []
        for line in text.splitlines():
            if not line.strip():
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # crash tail: skip the partial line, never fabricate
        return records

    @staticmethod
    def _matches(
        env: LogEnvelope,
        producer_id: str | None,
        epoch: int | None,
        level: str | None,
        stream: str | None,
        run_id: str | None,
        node_id: str | None,
        trace_id: str | None,
        keyword: str | None,
        since: str | None,
        until: str | None,
    ) -> bool:
        if producer_id is not None and env.producer_id != producer_id:
            return False
        if epoch is not None and env.producer_epoch != epoch:
            return False
        if level is not None and env.level != level:
            return False
        if stream is not None and env.stream != stream:
            return False
        if run_id is not None and env.run_id != run_id:
            return False
        if node_id is not None and env.node_id != node_id:
            return False
        if trace_id is not None and env.trace_id != trace_id:
            return False
        if keyword is not None and keyword not in env.message:
            return False
        if since is not None and env.timestamp < since:
            return False
        if until is not None and env.timestamp > until:
            return False
        return True

    def _query_result(
        self, events: list[dict[str, Any]], producer_id: str | None, epoch: int | None
    ) -> dict[str, Any]:
        producers = {e["producer_id"] for e in events}
        if producer_id is not None:
            producers = {producer_id}
        completeness = "complete"
        unknown_tail = False
        persisted_seq: int | None = None
        for pid in producers:
            meta_list = [m for m in self.segments() if m.producer_id == pid]
            if epoch is not None:
                meta_list = [m for m in meta_list if m.producer_epoch == epoch]
            if not meta_list:
                continue
            latest = max(meta_list, key=lambda m: (m.producer_epoch, m.last_seq))
            seal = self._read_seal(pid, latest.producer_epoch)
            max_seen = max((m.last_seq for m in meta_list), default=0)
            if seal is None or int(seal.get("last_seq", 0)) < max_seen:
                completeness = "partial"
                unknown_tail = True
            else:
                persisted_seq = max(persisted_seq or 0, int(seal.get("persisted_seq", 0)))
        if producers and persisted_seq is None:
            persisted_seq = 0
        return {
            "events": events,
            "next_cursor": events[-1]["seq"] if events else None,
            "completeness": completeness,
            "persisted_seq": persisted_seq,
            "unknown_tail": unknown_tail,
            "archive_state": "archived",
            "missing_sources": [],
        }

    def _read_seal(self, producer_id: str, epoch: int) -> dict[str, Any] | None:
        path = self.root / "seals" / producer_id / f"e{epoch}.json"
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else None
        except json.JSONDecodeError:
            return None
