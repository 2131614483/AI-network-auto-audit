"""Code-location index: file -> line -> endpoint -> tables.

Scans ``apps/api/main.py`` for route decorators (method + path + line) and the
``schema.table`` references inside each handler body, producing a deterministic
JSON index (``docs/code-map.json`` via scripts/build-code-map.ps1).  The API
exposes it read-only at ``/api/v1/observability/code-map`` so the desktop (or
any operator) can jump from a UI feature / endpoint to the source line and the
tables it touches — the code-layer drill-down of L2.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from packages.observability.code_fingerprint import service_code_sha256

_ROUTE_RE = re.compile(r'^\s*@app\.(get|post|put|patch|delete)\(\s*"([^"]+)"', re.MULTILINE)
_TABLE_RE = re.compile(r"\b([a-z][a-z0-9_]*)\.[a-z][a-z0-9_]*\b")
_DEF_RE = re.compile(r"^\s*(async )?def ", re.MULTILINE)


def _handler_body_lines(source: str, decorator_index: int) -> list[str]:
    """Return source lines of the handler that follows a route decorator."""
    head = source[decorator_index:]
    def_match = _DEF_RE.search(head)
    if not def_match:
        return []
    body_start = decorator_index + def_match.end()
    # End of function: next top-level 'def' or '@app.' at column 0, or EOF.
    tail = source[body_start:]
    next_boundary = re.search(r"^(?:@app\.|\s*async def |\s*def )", tail, re.MULTILINE)
    if next_boundary:
        return tail[: next_boundary.start()].splitlines()
    return tail.splitlines()


def build_code_map(project_root: str | Path, *, api_file: str | Path | None = None) -> dict[str, Any]:
    root = Path(project_root)
    api_path = Path(api_file) if api_file is not None else root / "apps" / "api" / "main.py"
    source = api_path.read_text(encoding="utf-8")
    code_sha = service_code_sha256([root / "packages", root / "apps"])

    endpoints: list[dict[str, Any]] = []
    for match in _ROUTE_RE.finditer(source):
        method = match.group(1).upper()
        path = match.group(2)
        line = source.count("\n", 0, match.start()) + 1
        body_lines = _handler_body_lines(source, match.start())
        tables = sorted({m.group(0) for m in _TABLE_RE.finditer("\n".join(body_lines))})
        endpoints.append(
            {
                "method": method,
                "path": path,
                "file": str(api_path.relative_to(root)).replace("\\", "/"),
                "line": line,
                "tables": tables,
                "code_sha256": code_sha,
            }
        )

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "code_sha256": code_sha,
        "api_file": str(api_path.relative_to(root)).replace("\\", "/"),
        "endpoint_count": len(endpoints),
        "endpoints": endpoints,
    }


def load_code_map(json_path: str | Path) -> dict[str, Any] | None:
    path = Path(json_path)
    if not path.is_file():
        return None
    data: Any = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else None


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Build the code-location index (docs/code-map.json).")
    parser.add_argument("--out", default="docs/code-map.json", help="output JSON path")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    out = root / args.out
    payload = build_code_map(root)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[audit-network] code map written: {out} ({payload['endpoint_count']} endpoints)")


if __name__ == "__main__":
    main()
