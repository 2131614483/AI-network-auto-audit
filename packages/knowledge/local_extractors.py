"""Side-effect-free routing for local rich-file extraction."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

MINERU_SUFFIXES = {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".webp", ".bmp"}
MEDIA_SUFFIXES = {".mp3", ".wav", ".m4a", ".flac", ".aac", ".mp4", ".mov", ".mkv", ".avi", ".webm"}

# Console script inside the read-only MinerU checkout's own virtualenv.
# There is deliberately no default: it used to be the author's own
# `D:\MinerU-master\.venv\Scripts\mineru.exe`, which no other machine has.  With
# MINERU_EXECUTABLE unset the capability reports itself as waiting for a local
# runtime (see local_adapter_catalog) instead of claiming to be available.
MINERU_EXECUTABLE = os.getenv("MINERU_EXECUTABLE", "")


def mineru_available() -> bool:
    """Report whether the local MinerU runtime is present, without invoking it."""
    if not MINERU_EXECUTABLE:
        return False
    return Path(MINERU_EXECUTABLE).is_file()


@dataclass(frozen=True, slots=True)
class DeferredExtraction:
    extractor_key: str
    reason: str


@dataclass(frozen=True, slots=True)
class LocalAdapter:
    """Declarative local extraction seam, intentionally without an entry point."""

    key: str
    label: str
    supported_suffixes: tuple[str, ...]
    status: str
    execution_mode: str
    next_step: str


def local_adapter_catalog() -> tuple[LocalAdapter, ...]:
    """Return UI-safe local adapter declarations without probing or starting tools."""

    mineru_ready = mineru_available()
    return (
        LocalAdapter(
            key="local.mineru",
            label="MinerU 文档与图像解析接口",
            supported_suffixes=tuple(sorted(MINERU_SUFFIXES)),
            status="available" if mineru_ready else "waiting_for_local_runtime",
            execution_mode="isolated_subprocess" if mineru_ready else "not_configured",
            next_step=(
                "已检测到本地 MinerU 运行时，可提交受策略控制的解析任务。"
                if mineru_ready
                else "配置并单独验收本地 MinerU 后，才可提交受策略控制的解析任务。"
            ),
        ),
        LocalAdapter(
            key="local.media",
            label="本地音频与视频转写接口",
            supported_suffixes=tuple(sorted(MEDIA_SUFFIXES)),
            status="waiting_for_local_runtime",
            execution_mode="not_configured",
            next_step="配置并单独验收本地转写运行时后，才可提交受策略控制的转写任务。",
        ),
    )


def deferred_extraction_for(path: Path) -> DeferredExtraction | None:
    """Return the local worker seam; this never invokes an executable."""
    suffix = path.suffix.lower()
    if suffix in MINERU_SUFFIXES:
        return DeferredExtraction("local.mineru", "waiting for local MinerU extraction")
    if suffix in MEDIA_SUFFIXES:
        return DeferredExtraction("local.media", "waiting for local audio/video extraction")
    return None
