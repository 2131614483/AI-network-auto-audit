"""Observability package (L6): trace context, structured rotating logs,
service code fingerprint, and the code-location index.

R3 milestone — the log layer becomes traceable (trace_id in every record) and
the code layer becomes indexable (file -> line -> endpoint -> tables), closing
the drill-down loop  data row -> log line -> source code.
"""

from __future__ import annotations

from packages.observability.code_fingerprint import service_code_sha256
from packages.observability.code_map import build_code_map, load_code_map
from packages.observability.logging_setup import (
    attach_spool_logging,
    cleanup_old_logs,
    configure_rotating_file_logging,
)
from packages.observability.trace import (
    TraceIdFilter,
    current_trace_id,
    new_trace_id,
    set_trace_id,
    trace_context,
)

__all__ = [
    "TraceIdFilter",
    "attach_spool_logging",
    "build_code_map",
    "cleanup_old_logs",
    "configure_rotating_file_logging",
    "current_trace_id",
    "load_code_map",
    "new_trace_id",
    "service_code_sha256",
    "set_trace_id",
    "trace_context",
]
