"""Durable AI settings storage: safe read/modify/write of the project ``.env``.

The AI settings page persists what the operator types into ``.env`` — the
project's established configuration surface, already gitignored.  Three rules
govern this module:

1. **Never destroy user content.**  Every unrelated line — comments, blank
   lines, ``DATABASE_URL``, ordering — is preserved byte for byte.  Only the
   keys named in an update are ever added, replaced or removed.
2. **Never leak a secret.**  Writes accept plaintext; reads return plaintext
   too, so callers must mask before returning anything to a client (see
   :func:`packages.ai.config.mask_secret`).
3. **Never corrupt on failure.**  The write is atomic (temp file in the same
   directory + ``os.replace``) with a ``.env.bak`` snapshot, so a crash mid-save
   cannot leave a half-written configuration behind.

Values that contain a newline are rejected outright: a newline in a value would
let a caller inject arbitrary additional assignments into the file.
"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

_KEY_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_ASSIGNMENT = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$")
_UNSAFE_CHARS = re.compile(r"[\s#\"'$`\\]")

#: Section marker written above keys this module appends.  Existing files
#: rarely have it; it exists so a human can see which lines are tool-managed.
MANAGED_HEADER = "# --- AI settings (managed by the desktop AI settings page) ---"


class EnvWriteError(RuntimeError):
    """A settings write was rejected before touching the file."""


def project_root() -> Path:
    """Repository root, derived from this module's location."""
    return Path(__file__).resolve().parents[2]


def env_file_path() -> Path:
    """The project ``.env`` this module owns."""
    return project_root() / ".env"


def backup_file_path(path: Path | None = None) -> Path:
    target = path or env_file_path()
    return target.with_name(f"{target.name}.bak")


def parse_env_text(text: str) -> dict[str, str]:
    """Parse ``KEY=value`` lines; ignores comments and unparseable lines."""
    values: dict[str, str] = {}
    for line in text.splitlines():
        match = _ASSIGNMENT.match(line)
        if match is None:
            continue
        values[match.group(1)] = _unquote(match.group(2))
    return values


def _unquote(raw: str) -> str:
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] == '"':
        inner = value[1:-1]
        return re.sub(r"\\(.)", r"\1", inner)
    if len(value) >= 2 and value[0] == value[-1] == "'":
        return value[1:-1]
    # Bare value: drop a trailing comment only when clearly separated from the
    # value, so a legitimate ``#`` inside a token survives.
    match = re.match(r"^(.*?)\s+#.*$", value)
    if match is not None:
        value = match.group(1).strip()
    return value


def _format_value(value: str) -> str:
    """Quote only when needed, so simple values stay readable."""
    if value == "" or _UNSAFE_CHARS.search(value):
        escaped = (
            value.replace("\\", "\\\\")
            .replace('"', '\\"')
            .replace("$", "\\$")
            .replace("`", "\\`")
        )
        return f'"{escaped}"'
    return value


def _validate(key: str, value: str) -> None:
    if not _KEY_PATTERN.match(key):
        raise EnvWriteError(f"refusing to write invalid environment key {key!r}")
    if "\n" in value or "\r" in value:
        raise EnvWriteError(f"refusing to write a newline into {key} (would inject extra assignments)")


def read_env_file(path: Path | None = None) -> dict[str, str]:
    """Current ``.env`` values, or an empty mapping when the file is absent."""
    target = path or env_file_path()
    if not target.exists():
        return {}
    return parse_env_text(target.read_text(encoding="utf-8"))


def skip_dotenv() -> bool:
    """True when this process must ignore ``.env`` entirely.

    Set ``AUDIT_NETWORK_SKIP_DOTENV=1`` to make configuration resolution depend
    only on the real process environment.  The test suite sets it, so a
    developer's ``.env`` can never change what a test observes.
    """
    return os.getenv("AUDIT_NETWORK_SKIP_DOTENV", "").strip().lower() in {"1", "true", "yes"}


def load_env_file(path: Path | None = None, *, override: bool = False) -> list[str]:
    """Load ``.env`` into ``os.environ``; returns the keys actually applied.

    ``override=False`` by default, matching the long-standing behaviour of the
    project's PowerShell launchers: an explicitly exported process variable
    (``DATABASE_URL`` from the start script, a user-level ``OPENAI_COMPAT_API_KEY``)
    always wins over the file.  The file is a durable *default*, not a hijack.
    """
    if skip_dotenv():
        return []
    applied: list[str] = []
    for key, value in read_env_file(path).items():
        if not override and os.environ.get(key):
            continue
        os.environ[key] = value
        applied.append(key)
    return applied


_LOADED_ONCE = False


def load_env_file_once() -> list[str]:
    """Apply ``.env`` as defaults exactly once per process.

    Called from configuration resolution so **every** entry point sees the same
    configured channel — the API did this explicitly at import, but scripts
    (``scripts/demo-grouping-business.py`` and friends) did not, so a key saved
    through the settings page was invisible to them and they fell back as if no
    channel were configured at all.  Idempotent and cheap; a no-op when
    :func:`skip_dotenv` is set or the file is absent.
    """
    global _LOADED_ONCE
    if _LOADED_ONCE:
        return []
    _LOADED_ONCE = True
    return load_env_file()


def apply_to_environ(values: dict[str, str | None]) -> None:
    """Make freshly saved settings effective in this process immediately.

    ``None`` or an empty string clears the variable so resolution falls back to
    the next source in the chain (legacy variable, then built-in default).
    """
    for key, value in values.items():
        if value:
            os.environ[key] = value
        else:
            os.environ.pop(key, None)


def update_env_file(
    updates: dict[str, str | None],
    *,
    path: Path | None = None,
    allowed_keys: tuple[str, ...] | None = None,
) -> Path:
    """Apply ``updates`` to ``.env`` atomically and return the file path.

    A ``None`` or empty value removes the assignment.  ``allowed_keys`` is a
    defence-in-depth whitelist: even if a caller is tricked into passing an
    unexpected key, only the AI settings variables can be written.
    """
    target = path or env_file_path()
    for key, value in updates.items():
        if allowed_keys is not None and key not in allowed_keys:
            raise EnvWriteError(f"{key} is not a managed AI setting")
        _validate(key, value or "")

    existing = target.read_text(encoding="utf-8") if target.exists() else ""
    lines = existing.splitlines()

    pending = dict(updates)
    output: list[str] = []
    for line in lines:
        match = _ASSIGNMENT.match(line)
        if match is None or match.group(1) not in pending:
            output.append(line)
            continue
        key = match.group(1)
        value = pending.pop(key)
        if value:
            output.append(f"{key}={_format_value(value)}")
        # A removed key drops its line entirely.

    additions = [(key, value) for key, value in pending.items() if value]
    if additions:
        if output and output[-1].strip():
            output.append("")
        if MANAGED_HEADER not in output:
            output.append(MANAGED_HEADER)
        output.extend(f"{key}={_format_value(value)}" for key, value in additions)

    text = "\n".join(output)
    if text and not text.endswith("\n"):
        text += "\n"

    if target.exists():
        shutil.copy2(target, backup_file_path(target))
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(f"{target.name}.tmp")
    temp.write_text(text, encoding="utf-8")
    os.replace(temp, target)
    return target
