"""Trace id context propagation into the Python logging layer.

``trace_var`` is a ContextVar so the same worker thread can carry a different
trace per request/task.  ``TraceIdFilter`` attaches ``trace_id`` and
``code_sha`` to every record that passes through a configured handler; records
that already carry the attributes (e.g. a task explicitly sets them) win over
the ambient context.
"""

from __future__ import annotations

import contextlib
import contextvars
import logging
import uuid
from pathlib import Path
from typing import Iterator

from packages.observability.code_fingerprint import service_code_sha256

trace_var: contextvars.ContextVar[str] = contextvars.ContextVar("audit_trace_id", default="-")
code_sha_var: contextvars.ContextVar[str] = contextvars.ContextVar("audit_code_sha", default="-")

_CODE_SHA_CACHE: str | None = None


def ambient_code_sha() -> str:
    """Lazily compute the service fingerprint once per process.

    Every log record falls back to this value so the log layer always carries
    "which version of the code produced this line" (L2) even when the calling
    context did not set it explicitly.
    """
    global _CODE_SHA_CACHE
    if _CODE_SHA_CACHE is None:
        try:
            root = Path(__file__).resolve().parents[2]
            _CODE_SHA_CACHE = service_code_sha256([root / "packages", root / "apps"])
        except Exception:  # noqa: BLE001 - fingerprint must never break logging
            _CODE_SHA_CACHE = "-"
    return _CODE_SHA_CACHE


def set_trace_id(trace_id: str) -> None:
    """Set the ambient trace id for the current context (no reset token)."""
    trace_var.set(trace_id)


def current_trace_id() -> str:
    return trace_var.get()


def new_trace_id() -> str:
    return uuid.uuid4().hex


@contextlib.contextmanager
def trace_context(trace_id: str | None = None, *, code_sha: str | None = None) -> Iterator[str]:
    """Run a block under a trace context; restores the previous values after.

    The yielded value is the effective trace id.  Used by the API middleware
    (per request) and the task executor (per claimed task, using the task's own
    trace_id so data rows and log lines share one id).
    """
    token_trace = trace_var.set(trace_id or new_trace_id())
    token_sha = code_sha_var.set(code_sha or ambient_code_sha())
    try:
        yield trace_var.get()
    finally:
        trace_var.reset(token_trace)
        code_sha_var.reset(token_sha)


class TraceIdFilter(logging.Filter):
    """Attach ambient trace_id / code_sha to every handled log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "trace_id"):
            record.trace_id = trace_var.get()
        if not hasattr(record, "code_sha"):
            record.code_sha = code_sha_var.get()
        if getattr(record, "code_sha", "-") == "-":
            record.code_sha = ambient_code_sha()
        return True
