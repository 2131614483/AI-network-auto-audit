"""CW2: resident log collector (independent process, GUI-independent).

The collector owns the spool's background lifecycle so API / worker / plugin
processes only append events and never compete over rotation.  A maintenance
pass reports archive coverage and health; a crashed writer that left a hot
segment un-archived is surfaced as a gap (``last_error``) — new runs must
pause admission rather than claim a complete log they did not keep
(方案 8.4 / CW2 退出条件: 日志写失败暂停准入).

Run standalone::

    python -m packages.observability.collector --root .data/isolated/logs --interval 1.0
"""

from __future__ import annotations

import argparse
import json
import logging
import signal
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from packages.observability.log_spool import SegmentedSpool

logger = logging.getLogger("observability.collector")

_HEALTHY = "healthy"
_DEGRADED = "degraded"


class CollectorService:
    """Background maintenance loop over one :class:`SegmentedSpool`."""

    def __init__(
        self,
        spool: SegmentedSpool,
        *,
        interval_seconds: float = 1.0,
    ) -> None:
        self.spool = spool
        self.interval = max(0.05, float(interval_seconds))
        self._stop = threading.Event()
        self.last_status = _HEALTHY
        self.last_run_at: str | None = None

    def stop(self) -> None:
        self._stop.set()

    def maintenance(self) -> dict[str, Any]:
        """One pass: verify archive coverage + health; report status.

        Rotation/archive of live writers happens inside the spool (seal ->
        verify -> archive -> manifest -> evict).  The collector detects the
        failure mode the writer cannot: a hot segment left behind by a crashed
        process with no manifest entry.
        """
        manifest_archived = {
            meta.segment_id
            for meta in self.spool.segments()
            if meta.archive_state == "archived"
        }
        active = self.spool.active_segment_ids()
        gaps: list[str] = []
        hot_root = self.spool.root / "hot"
        if hot_root.is_dir():
            for path in sorted(hot_root.rglob("*.jsonl")):
                if path.stem not in manifest_archived and path.stem not in active:
                    gaps.append(path.name)
        if gaps:
            self.spool.last_error = f"unarchived hot segment(s): {','.join(gaps)}"
            status = _DEGRADED
        elif not self.spool.accepting:
            status = _DEGRADED
        else:
            status = _HEALTHY
        self.last_status = status
        self.last_run_at = datetime.now(timezone.utc).isoformat()
        return {
            "status": status,
            "accepting": self.spool.accepting,
            "last_error": self.spool.last_error,
            "archived_segments": len(manifest_archived),
            "gap_segments": gaps,
        }

    def run_forever(self) -> None:
        while not self._stop.is_set():
            self.maintenance()
            self._stop.wait(self.interval)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit Network resident log collector")
    parser.add_argument("--root", required=True, help="spool root directory")
    parser.add_argument("--interval", type=float, default=1.0, help="maintenance interval seconds")
    parser.add_argument("--once", action="store_true",
                        help="run one maintenance pass and exit (scheduled-task mode)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    root = Path(args.root)
    spool = SegmentedSpool(root)
    service = CollectorService(spool, interval_seconds=args.interval)

    def _stop(_signum: int, _frame: object) -> None:
        service.stop()

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    if args.once:
        report = service.maintenance()
        print(json.dumps(report, ensure_ascii=False, sort_keys=True), flush=True)
        return 0
    service.run_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
