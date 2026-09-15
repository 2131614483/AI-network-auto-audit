"""CW6: spool health, watermark alerts and run-completeness integrity.

Long-running (24x7) operation needs a deterministic answer to two questions:
  - is collection healthy (bytes / segments / oldest un-archived hot age)?
  - is a producer's log evidence complete (seal present, watermark known)?

A sealed producer with a known persisted watermark is ``complete``; a missing
seal means the tail is unknown (``partial`` + ``unknown_tail``) — the caller
must never display an un-evidenced run as fully successful.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

_ARCHIVE_SUFFIX = ".gz"


@dataclass(frozen=True, slots=True)
class SpoolHealth:
    root: str
    total_bytes: int = 0
    segment_count: int = 0
    hot_segment_count: int = 0
    open_hot_bytes: int = 0
    oldest_hot_age_seconds: float = 0.0
    sealed_producer_count: int = 0
    scanned_at: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "root": self.root,
            "total_bytes": self.total_bytes,
            "segment_count": self.segment_count,
            "hot_segment_count": self.hot_segment_count,
            "open_hot_bytes": self.open_hot_bytes,
            "oldest_hot_age_seconds": round(self.oldest_hot_age_seconds, 3),
            "sealed_producer_count": self.sealed_producer_count,
            "scanned_at": self.scanned_at,
        }


def spool_health(root: str | Path) -> SpoolHealth:
    """Scan one spool root deterministically (tolerates concurrent writers)."""
    base = Path(root)
    total_bytes = 0
    segment_count = 0
    hot_segment_count = 0
    open_hot_bytes = 0
    oldest_mtime: float | None = None
    now = datetime.now(timezone.utc).timestamp()

    manifest = base / "manifest.jsonl"
    if manifest.is_file():
        try:
            total_bytes += manifest.stat().st_size
        except OSError:
            pass
        try:
            for line in manifest.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    meta = json.loads(line)
                except json.JSONDecodeError:
                    continue
                segment_count += 1
                total_bytes += int(meta.get("bytes") or 0)
        except OSError:
            pass

    hot_root = base / "hot"
    if hot_root.is_dir():
        try:
            for path in sorted(hot_root.rglob("*.jsonl")):
                try:
                    stat = path.stat()
                except OSError:
                    continue
                hot_segment_count += 1
                open_hot_bytes += stat.st_size
                total_bytes += stat.st_size
                if oldest_mtime is None or stat.st_mtime < oldest_mtime:
                    oldest_mtime = stat.st_mtime
        except OSError:
            pass

    archive_root = base / "archive"
    if archive_root.is_dir():
        try:
            for path in sorted(archive_root.rglob(f"*{_ARCHIVE_SUFFIX}")):
                try:
                    total_bytes += path.stat().st_size
                except OSError:
                    continue
        except OSError:
            pass

    seals_root = base / "seals"
    sealed_producers = set()
    if seals_root.is_dir():
        try:
            for seal_path in seals_root.glob("*/e*.json"):
                sealed_producers.add(seal_path.parent.name)
                try:
                    total_bytes += seal_path.stat().st_size
                except OSError:
                    continue
        except OSError:
            pass

    return SpoolHealth(
        root=str(base.resolve()),
        total_bytes=total_bytes,
        segment_count=segment_count,
        hot_segment_count=hot_segment_count,
        open_hot_bytes=open_hot_bytes,
        oldest_hot_age_seconds=max(0.0, now - oldest_mtime) if oldest_mtime is not None else 0.0,
        sealed_producer_count=len(sealed_producers),
        scanned_at=datetime.now(timezone.utc).isoformat(),
    )


@dataclass(frozen=True, slots=True)
class WatermarkAlert:
    code: str
    message: str
    current: float
    limit: float

    def as_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "message": self.message,
            "current": round(self.current, 3),
            "limit": round(self.limit, 3),
        }


def watermark_alerts(
    health: SpoolHealth,
    *,
    max_bytes: int | None = None,
    max_segments: int | None = None,
    max_hot_age_seconds: float | None = None,
    max_hot_bytes: int | None = None,
) -> list[WatermarkAlert]:
    """Deterministic threshold checks; every breach is an explicit alert."""
    alerts: list[WatermarkAlert] = []
    if max_bytes is not None and health.total_bytes > max_bytes:
        alerts.append(WatermarkAlert(
            "high_watermark",
            f"spool total {health.total_bytes} bytes exceeds limit {max_bytes}",
            float(health.total_bytes), float(max_bytes),
        ))
    if max_segments is not None and health.segment_count > max_segments:
        alerts.append(WatermarkAlert(
            "max_segments",
            f"spool has {health.segment_count} segments exceeding limit {max_segments}",
            float(health.segment_count), float(max_segments),
        ))
    if max_hot_age_seconds is not None and health.oldest_hot_age_seconds > max_hot_age_seconds:
        alerts.append(WatermarkAlert(
            "max_hot_age",
            f"oldest hot segment {health.oldest_hot_age_seconds:.1f}s exceeds limit {max_hot_age_seconds:.1f}s",
            health.oldest_hot_age_seconds, float(max_hot_age_seconds),
        ))
    if max_hot_bytes is not None and health.open_hot_bytes > max_hot_bytes:
        alerts.append(WatermarkAlert(
            "max_hot_bytes",
            f"open hot segments {health.open_hot_bytes} bytes exceed limit {max_hot_bytes}",
            float(health.open_hot_bytes), float(max_hot_bytes),
        ))
    return alerts


@dataclass(frozen=True, slots=True)
class ProducerIntegrity:
    producer_id: str
    status: str  # complete | partial | unavailable
    last_seq: int | None = None
    persisted_seq: int | None = None
    bytes_count: int | None = None
    sealed_at: str | None = None
    unknown_tail: bool = False

    def as_dict(self) -> dict[str, object]:
        return {
            "producer_id": self.producer_id,
            "status": self.status,
            "last_seq": self.last_seq,
            "persisted_seq": self.persisted_seq,
            "bytes_count": self.bytes_count,
            "sealed_at": self.sealed_at,
            "unknown_tail": self.unknown_tail,
        }


@dataclass(frozen=True, slots=True)
class SpoolIntegrityReport:
    producers: tuple[ProducerIntegrity, ...] = ()
    complete_count: int = 0
    partial_count: int = 0
    unavailable_count: int = 0
    gaps: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {
            "producers": [p.as_dict() for p in self.producers],
            "complete_count": self.complete_count,
            "partial_count": self.partial_count,
            "unavailable_count": self.unavailable_count,
            "gaps": list(self.gaps),
        }


def spool_integrity(root: str | Path, expected_producers: list[str]) -> SpoolIntegrityReport:
    """Check every expected producer has a seal with a known persisted watermark.

    ``complete`` = seal present with persisted_seq known; ``partial`` = seal
    missing (unknown_tail=True) — the run's log evidence is not fully closed;
    ``unavailable`` = the spool itself is missing.
    """
    base = Path(root)
    if not base.is_dir():
        return SpoolIntegrityReport(
            producers=tuple(
                ProducerIntegrity(producer_id=pid, status="unavailable")
                for pid in expected_producers
            ),
            unavailable_count=len(expected_producers),
            gaps=tuple(f"{pid}:spool-missing" for pid in expected_producers),
        )

    producers: list[ProducerIntegrity] = []
    gaps: list[str] = []
    complete = partial = unavailable = 0
    for producer_id in expected_producers:
        seal_dir = base / "seals" / producer_id
        seal_files = sorted(seal_dir.glob("e*.json")) if seal_dir.is_dir() else []
        if not seal_files:
            producers.append(ProducerIntegrity(producer_id=producer_id, status="partial", unknown_tail=True))
            partial += 1
            gaps.append(f"{producer_id}:missing-seal")
            continue
        latest = seal_files[-1]
        try:
            record = json.loads(latest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            producers.append(ProducerIntegrity(producer_id=producer_id, status="partial", unknown_tail=True))
            partial += 1
            gaps.append(f"{producer_id}:unreadable-seal")
            continue
        persisted = record.get("persisted_seq")
        producers.append(ProducerIntegrity(
            producer_id=producer_id,
            status="complete" if persisted is not None else "partial",
            last_seq=record.get("last_seq"),
            persisted_seq=persisted,
            bytes_count=record.get("bytes_count"),
            sealed_at=record.get("sealed_at"),
            unknown_tail=persisted is None,
        ))
        if persisted is None:
            partial += 1
            gaps.append(f"{producer_id}:seal-without-watermark")
        else:
            complete += 1

    return SpoolIntegrityReport(
        producers=tuple(producers),
        complete_count=complete,
        partial_count=partial,
        unavailable_count=unavailable,
        gaps=tuple(gaps),
    )
