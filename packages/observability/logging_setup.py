"""Unified rotating file logging with trace fields and retention.

Replaces the single unbounded FileHandler (L5): every service log file now
rotates by size with a bounded backup count (an effective retention window)
and every record carries ``trace_id`` + ``code_sha`` via ``TraceIdFilter``.

One handler per file — mixing TimedRotating and Rotating handlers on the same
path would fight over file renames, so size rotation is the primary mechanism
and ``cleanup_old_logs`` provides the by-day retention sweep for any legacy
per-day files (e.g. the desktop unified log).
"""

from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path

from packages.observability.trace import TraceIdFilter

DEFAULT_FORMAT = (
    "%(asctime)s %(levelname)s %(name)s trace_id=%(trace_id)s code_sha=%(code_sha)s: %(message)s"
)


def configure_rotating_file_logging(
    log_path: str | Path,
    *,
    level: int = logging.INFO,
    max_bytes: int = 10 * 1024 * 1024,
    backup_count: int = 30,
    fmt: str = DEFAULT_FORMAT,
) -> logging.Handler:
    """Install a size-rotating file handler (idempotent per log file).

    Returns the installed handler.  ``backup_count`` bounds how many rotated
    files are kept (10 MB x 30 = ~300 MB ceiling per log), which is the
    retention policy for 24x7 unattended operation.
    """
    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter(fmt)
    handler = logging.handlers.RotatingFileHandler(
        path, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8"
    )
    handler.setFormatter(formatter)
    handler.setLevel(level)
    handler.addFilter(TraceIdFilter())

    root = logging.getLogger()
    if root.getEffectiveLevel() > level:
        root.setLevel(level)
    if not any(isinstance(h, logging.handlers.RotatingFileHandler) and h.baseFilename == str(path) for h in root.handlers):
        root.addHandler(handler)
    return handler


def attach_spool_logging(
    spool_root: str | Path,
    *,
    producer_id: str = "service",
    level: int = logging.INFO,
    max_spool_bytes: int | None = None,
) -> logging.Handler:
    """CW2 wiring: persist every root-logger record into the log spool.

    One handler per (producer, spool root) per process — idempotent across
    repeated app creation (tests / restarts) — so API and worker processes
    keep their existing ``logging`` calls and still emit structured envelopes
    (with ambient ``trace_id``) that the trace-locate endpoint can query.
    ``max_spool_bytes`` enables CW6 watermark back-pressure (drop + alert)
    when the spool exceeds the configured ceiling.
    """
    from packages.observability.log_spool import SegmentedSpool
    from packages.observability.spool_logging import SpoolLogHandler

    root = Path(spool_root)
    root.mkdir(parents=True, exist_ok=True)
    handler = SpoolLogHandler(
        SegmentedSpool(root), producer_id=producer_id, max_spool_bytes=max_spool_bytes,
    )
    handler.setLevel(level)
    handler.addFilter(TraceIdFilter())
    root_logger = logging.getLogger()
    if root_logger.getEffectiveLevel() > level:
        root_logger.setLevel(level)
    for existing in root_logger.handlers:
        if (
            isinstance(existing, SpoolLogHandler)
            and existing.producer_id == producer_id
            and str(existing.spool.root) == str(root)
        ):
            return existing
    root_logger.addHandler(handler)
    return handler


def cleanup_old_logs(log_dir: str | Path, pattern: str, *, retention_days: int = 30) -> int:
    """Delete log files matching ``pattern`` older than ``retention_days``.

    Returns the number of files removed.  Used by the operator script for any
    per-day log files that are not covered by size rotation.
    """
    directory = Path(log_dir)
    if not directory.is_dir():
        return 0
    import time

    cutoff_ts = time.time() - retention_days * 86400
    removed = 0
    for candidate in directory.glob(pattern):
        try:
            if candidate.is_file() and candidate.stat().st_mtime < cutoff_ts:
                candidate.unlink()
                removed += 1
        except OSError:
            continue
    return removed
