"""Isolated local MinerU invocation for rich-media knowledge extraction.

This adapter turns the previously ``waiting_for_local_runtime`` ``local.mineru``
seam into a real, policy-gated worker: it verifies the source artifact (SHA256,
size and declared-root boundary), runs the external MinerU CLI inside a fresh
subprocess with a dedicated working directory and a hard timeout, then parses
the produced Markdown and page-cited ``content_list.json`` into a
schema-validated ``ParsedDocument``.

The external MinerU tree (whatever ``MINERU_EXECUTABLE`` points at) is treated as
read-only reference: this module only *invokes* its console script and never
writes inside it.  All output lands under the caller-supplied output root
(``.data`` in this project).
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from jsonschema import Draft202012Validator

from packages.knowledge.local_extractors import MINERU_EXECUTABLE, MINERU_SUFFIXES

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RESULT_SCHEMA_PATH = PROJECT_ROOT / "contracts" / "jsonschema" / "rich-media-parse-result.schema.json"

# Environment keys MinerU needs to locate models and run torch/CUDA.  A curated
# allow-list (not ``os.environ.copy()``) so no credentials or unrelated secrets
# are passed into the subprocess.
_MINERU_ENV_KEYS = (
    "SYSTEMROOT", "WINDIR", "COMSPEC", "PATH", "PATHEXT",
    "HOME", "USERPROFILE", "HOMEDRIVE", "HOMEPATH",
    "TEMP", "TMP", "LOCALAPPDATA", "APPDATA",
    "CUDA_VISIBLE_DEVICES", "HF_HOME", "HF_HUB_CACHE",
    "MODELSCOPE_CACHE", "TRANSFORMERS_CACHE", "MINERU_LOG_LEVEL",
)

SUPPORTED_SUFFIXES = frozenset(MINERU_SUFFIXES)
DEFAULT_TIMEOUT_SECONDS = 900
DEFAULT_METHOD = "auto"
DEFAULT_LANG = "ch"


class RichMediaParseError(RuntimeError):
    """The local MinerU adapter rejected the request or produced no usable output."""


@dataclass(frozen=True, slots=True)
class RichMediaChunk:
    type: str
    text: str
    page_idx: int
    bbox: tuple[float, float, float, float] | None = None
    text_level: int | None = None


@dataclass(frozen=True, slots=True)
class ParsedDocument:
    adapter_key: str
    adapter_version: str
    input_sha256: str
    method: str
    markdown: str
    chunks: tuple[RichMediaChunk, ...]
    images: tuple[str, ...]
    duration_ms: int

    def as_payload(self) -> dict[str, Any]:
        return {
            "adapter_key": self.adapter_key,
            "adapter_version": self.adapter_version,
            "input_sha256": self.input_sha256,
            "method": self.method,
            "markdown": self.markdown,
            "chunks": [
                {
                    "type": chunk.type,
                    "text": chunk.text,
                    "page_idx": chunk.page_idx,
                    **({"bbox": list(chunk.bbox)} if chunk.bbox is not None else {}),
                    **({"text_level": chunk.text_level} if chunk.text_level is not None else {}),
                }
                for chunk in self.chunks
            ],
            "images": list(self.images),
            "duration_ms": self.duration_ms,
        }


def _schema() -> dict[str, Any]:
    payload = json.loads(RESULT_SCHEMA_PATH.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RichMediaParseError("rich-media result schema is invalid")
    return payload


def validate_parse_result(payload: dict[str, Any]) -> None:
    """Raise when a payload violates the rich-media result contract."""
    errors = sorted(Draft202012Validator(_schema()).iter_errors(payload), key=lambda error: list(error.path))
    if errors:
        raise RichMediaParseError(f"invalid rich-media parse result: {errors[0].message}")


def _resolve_input(uri: str, sha256: str, size_bytes: int, allowed_roots: tuple[Path, ...]) -> Path:
    if not uri:
        raise RichMediaParseError("artifact URI is missing")
    parsed = urlparse(uri)
    if parsed.scheme != "file" or parsed.netloc not in {"", "localhost"}:
        raise RichMediaParseError("artifact URI must be a local file URI")
    raw_path = unquote(parsed.path)
    if raw_path.startswith("/") and len(raw_path) >= 3 and raw_path[2] == ":":
        raw_path = raw_path[1:]
    try:
        resolved = Path(raw_path).resolve(strict=True)
    except OSError as exc:
        raise RichMediaParseError("artifact file is unavailable") from exc
    if not resolved.is_file():
        raise RichMediaParseError("artifact is not a regular file")
    try:
        next(root for root in allowed_roots if resolved.is_relative_to(root))
    except StopIteration as exc:
        raise RichMediaParseError("artifact is outside declared read roots") from exc
    if resolved.suffix.lower() not in SUPPORTED_SUFFIXES:
        raise RichMediaParseError("artifact media type is not supported by local.mineru")
    if size_bytes <= 0 or resolved.stat().st_size != size_bytes:
        raise RichMediaParseError("artifact size does not match reference")
    digest = hashlib.sha256()
    with resolved.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    actual = digest.hexdigest()
    if actual.lower() != sha256.lower():
        raise RichMediaParseError("artifact sha256 does not match reference")
    return resolved


def _parse_content_list(path: Path) -> tuple[RichMediaChunk, ...]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RichMediaParseError("MinerU content list is unreadable") from exc
    if not isinstance(payload, list):
        raise RichMediaParseError("MinerU content list must be an array")
    chunks: list[RichMediaChunk] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        block_type = item.get("type")
        if not isinstance(block_type, str):
            continue
        text = item.get("text")
        if not isinstance(text, str):
            # Image/table blocks may carry their payload under another key.
            text = item.get("img_path") or item.get("table_body") or ""
            if not isinstance(text, str):
                text = ""
        page_idx = item.get("page_idx")
        if not isinstance(page_idx, int) or page_idx < 0:
            continue
        bbox = item.get("bbox")
        normalized_bbox: tuple[float, float, float, float] | None = None
        if isinstance(bbox, list) and len(bbox) == 4 and all(isinstance(value, (int, float)) for value in bbox):
            normalized_bbox = (float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3]))
        text_level = item.get("text_level")
        normalized_level = text_level if isinstance(text_level, int) and text_level >= 0 else None
        chunks.append(
            RichMediaChunk(
                type=block_type,
                text=text.strip(),
                page_idx=page_idx,
                bbox=normalized_bbox,
                text_level=normalized_level,
            )
        )
    return tuple(chunks)


def _find_markdown(output_dir: Path) -> Path:
    if not output_dir.is_dir():
        raise RichMediaParseError("MinerU output directory does not exist")
    candidates = sorted(output_dir.rglob("*.md"))
    if not candidates:
        raise RichMediaParseError("MinerU produced no markdown output")
    return candidates[0]


def parse_mineru_output(
    output_dir: Path,
    *,
    input_sha256: str,
    method: str,
    adapter_version: str,
    duration_ms: int,
) -> ParsedDocument:
    """Parse a MinerU output directory into a schema-valid ParsedDocument."""
    markdown_path = _find_markdown(output_dir)
    markdown = markdown_path.read_text(encoding="utf-8")
    content_list_path = markdown_path.with_name(f"{markdown_path.stem}_content_list.json")
    chunks = _parse_content_list(content_list_path) if content_list_path.exists() else ()
    images_dir = markdown_path.parent / "images"
    images = tuple(sorted(path.name for path in images_dir.iterdir() if path.is_file())) if images_dir.is_dir() else ()
    result = ParsedDocument(
        adapter_key="local.mineru",
        adapter_version=adapter_version,
        input_sha256=input_sha256.lower(),
        method=method,
        markdown=markdown,
        chunks=chunks,
        images=images,
        duration_ms=duration_ms,
    )
    validate_parse_result(result.as_payload())
    return result


def _curated_environment() -> dict[str, str]:
    environment = {"PYTHONUTF8": "1"}
    for name in _MINERU_ENV_KEYS:
        if value := os.environ.get(name):
            environment[name] = value
    return environment


def mineru_version(executable: str | None = None, timeout: int = 120) -> str:
    """Read the external MinerU version without touching its source tree."""
    exe = executable or MINERU_EXECUTABLE
    try:
        completed = subprocess.run(
            [exe, "--version"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=_curated_environment(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RichMediaParseError("MinerU executable is unavailable") from exc
    version = (completed.stdout or completed.stderr or "").strip().splitlines()[0] if (completed.stdout or completed.stderr) else "unknown"
    return version or "unknown"


class MineruAdapter:
    """Run the external MinerU CLI as an isolated subprocess and parse its output."""

    def __init__(
        self,
        *,
        executable: str | None = None,
        output_root: Path,
        allowed_roots: tuple[Path, ...],
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
        method: str = DEFAULT_METHOD,
        lang: str = DEFAULT_LANG,
    ) -> None:
        self.executable = executable or MINERU_EXECUTABLE
        self.output_root = output_root.resolve()
        self.allowed_roots = tuple(root.resolve() for root in allowed_roots)
        self.timeout_seconds = timeout_seconds
        if method not in {"auto", "txt", "ocr"}:
            raise ValueError("method must be one of auto, txt, ocr")
        self.method = method
        self.lang = lang

    def extract(self, *, uri: str, sha256: str, size_bytes: int) -> ParsedDocument:
        if not self.allowed_roots:
            raise RichMediaParseError("at least one read root is required")
        input_path = _resolve_input(uri, sha256, size_bytes, self.allowed_roots)
        run_dir = self.output_root / input_path.stem / str(int(time.time() * 1000))
        run_dir.mkdir(parents=True, exist_ok=False)
        started = time.monotonic()
        command = [
            self.executable,
            "-p", str(input_path),
            "-o", str(run_dir),
            "-b", "pipeline",
            "-m", self.method,
            "-l", self.lang,
        ]
        try:
            completed = subprocess.run(
                command,
                cwd=self.output_root,
                env=_curated_environment(),
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RichMediaParseError("MinerU subprocess failed or exceeded its timeout") from exc
        if completed.returncode != 0:
            raise RichMediaParseError("MinerU subprocess exited unsuccessfully")
        duration_ms = int((time.monotonic() - started) * 1000)
        version = mineru_version(self.executable)
        # MinerU nests results under <stem>/<method>/, but a failed run may
        # leave only partial directories; parse whatever is present.
        return parse_mineru_output(
            run_dir,
            input_sha256=sha256,
            method=self.method,
            adapter_version=version,
            duration_ms=duration_ms,
        )
