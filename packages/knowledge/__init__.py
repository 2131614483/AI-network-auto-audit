"""Knowledge factory services."""

from typing import TYPE_CHECKING, Any

from .ingest import IngestResult, ingest_directory
from .lifecycle import ChangeOperation, KnowledgeLifecycleService, ValidationResult

if TYPE_CHECKING:
    from .source import SourceRegistration

__all__ = [
    "ChangeOperation",
    "IngestResult",
    "KnowledgeLifecycleService",
    "ValidationResult",
    "ingest_directory",
    "SourceRegistration",
    "register_read_only_source",
]


def __getattr__(name: str) -> Any:
    if name in {"SourceRegistration", "register_read_only_source"}:
        from .source import SourceRegistration, register_read_only_source

        return {"SourceRegistration": SourceRegistration, "register_read_only_source": register_read_only_source}[name]
    raise AttributeError(name)
