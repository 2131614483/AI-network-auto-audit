"""CW2: bridge stdlib ``logging`` records into the persistent log spool.

A handler so API / worker processes keep their existing ``logging`` calls and
still get every record persisted as a structured :class:`LogEnvelope` with the
ambient ``trace_id`` (from :class:`TraceIdFilter` attributes) plus optional
``run_id`` / ``node_id`` / ``attempt_id`` attributes set by the task context.
The spool is process-independent: the GUI closing never stops collection.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from uuid import uuid4

from packages.observability.log_spool import LogEnvelope, SegmentedSpool, SpoolWriteError

_DEFAULT_PRODUCER = "service"


class SpoolLogHandler(logging.Handler):
    """Emit every log record into a shared :class:`SegmentedSpool`.

    The handler never raises inside ``logging``'s emit path: a spool failure
    flips ``accepting`` off (admission back-pressure) and is logged once, so a
    missing log must be visible instead of silently dropped.

    CW6 watermark back-pressure: when ``max_spool_bytes`` is set and the spool
    exceeds it, the handler drops new frames (counted in ``dropped_frames``)
    and records one visible warning frame — a run that lost log evidence must
    never be displayed as fully successful (the completeness report will show
    the gap).
    """

    _WATERMARK_CHECK_EVERY = 32

    def __init__(
        self, spool: SegmentedSpool, *, producer_id: str = _DEFAULT_PRODUCER,
        max_spool_bytes: int | None = None,
    ) -> None:
        super().__init__()
        self.spool = spool
        self.producer_id = producer_id
        self.max_spool_bytes = int(max_spool_bytes) if max_spool_bytes else None
        self.dropped_frames = 0
        self._seq_lock = threading.Lock()
        self._seq = 0
        self._warned = False
        self._frames_since_check = 0
        self._watermark_warned = False

    def emit(self, record: logging.LogRecord) -> None:
        try:
            if not self.spool.accepting:
                self._warn_once("spool admission paused")
                return
            if self.max_spool_bytes is not None:
                self._frames_since_check += 1
                if self._frames_since_check >= self._WATERMARK_CHECK_EVERY:
                    self._frames_since_check = 0
                    self._check_watermark()
                # once the watermark is breached, further frames are dropped
                # (bounded, counted, visible) until the process restarts
                if self._watermark_warned:
                    self.dropped_frames += 1
                    return
            with self._seq_lock:
                self._seq += 1
                seq = self._seq
            trace_id = getattr(record, "trace_id", None)
            envelope = LogEnvelope(
                event_id=uuid4().hex,
                producer_id=self.producer_id,
                producer_epoch=1,
                seq=seq,
                timestamp=datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
                level=record.levelname.lower(),
                source=record.name,
                message=self.format(record),
                trace_id=str(trace_id) if trace_id and str(trace_id) != "-" else None,
                run_id=getattr(record, "run_id", None),
                node_id=getattr(record, "node_id", None),
                attempt_id=getattr(record, "attempt_id", None),
                stream="system",
            )
            self.spool.append(self.producer_id, 1, [envelope])
        except SpoolWriteError as exc:
            self._warn_once(f"spool write failed: {exc}")
        except Exception:  # logging must never crash the caller
            self._warn_once("spool handler failed")

    def _over_watermark(self) -> bool:
        from packages.observability.spool_ops import spool_health

        if self.max_spool_bytes is None:
            return False
        try:
            health = spool_health(self.spool.root)
        except OSError:
            return False
        return health.total_bytes > self.max_spool_bytes

    def _check_watermark(self) -> None:
        if self.max_spool_bytes is None or self._watermark_warned:
            return
        if not self._over_watermark():
            return
        self._watermark_warned = True
        # one visible warning frame: the spill is recorded, later frames drop
        try:
            with self._seq_lock:
                self._seq += 1
                seq = self._seq
            envelope = LogEnvelope(
                event_id=uuid4().hex,
                producer_id=self.producer_id,
                producer_epoch=1,
                seq=seq,
                timestamp=datetime.now(timezone.utc).isoformat(),
                level="warning",
                source="spool-log-handler",
                message=f"spool high watermark ({self.max_spool_bytes} bytes); log frames are being dropped",
                trace_id=None,
                stream="system",
            )
            self.spool.append(self.producer_id, 1, [envelope])
        except SpoolWriteError:
            self._warn_once("cannot record watermark warning")

    def _warn_once(self, message: str) -> None:
        if self._warned:
            return
        self._warned = True
        try:
            print(f"[spool-log-handler] {message}", flush=True)
        except Exception:
            pass
