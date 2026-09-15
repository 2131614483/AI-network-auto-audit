"""Register an external folder as a read-only knowledge source.

Registration records only the path and explicitly named README files.  It
never walks descendants, copies bytes, executes files, or starts ingestion.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse
from uuid import UUID

import psycopg2
from psycopg2.extras import Json, register_uuid

register_uuid()  # type: ignore[no-untyped-call]


@dataclass(frozen=True, slots=True)
class SourceRegistration:
    source_id: UUID
    root_uri: str
    readme_files: tuple[str, ...]
    read_only: bool = True


def _path_from_uri(value: str) -> Path:
    if value.lower().startswith("file://"):
        parsed = urlparse(value)
        raw = unquote(parsed.path)
        if len(raw) >= 3 and raw[0] == "/" and raw[2] == ":":
            raw = raw[1:]
        return Path(raw)
    return Path(value)


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _readme_manifest(root: Path, names: tuple[str, ...]) -> dict[str, Any]:
    """Read only the explicitly named files at the source root."""
    entries: list[dict[str, Any]] = []
    for name in names:
        candidate = (root / name).resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise ValueError(f"README path escapes source root: {name}") from exc
        if candidate.exists():
            if not candidate.is_file():
                raise ValueError(f"README path is not a file: {name}")
            entries.append({"path": candidate.relative_to(root).as_posix(), "size_bytes": candidate.stat().st_size, "sha256": _hash_file(candidate)})
    return {"readmes": entries, "recursive_scan": False, "copied": False, "executed": False}


def register_read_only_source(
    root_uri: str,
    *,
    database_url: str,
    name: str | None = None,
    tenant_slug: str = "local-dev",
    readme_names: tuple[str, ...] = ("README.md", "README.txt", "README", "README-清单.md"),
    domain_hint: str | None = None,
    graph_space_id: UUID | None = None,
) -> SourceRegistration:
    """Register an existing local folder without recursive discovery."""
    root = _path_from_uri(root_uri).resolve()
    if not root.exists() or not root.is_dir():
        raise ValueError(f"source directory does not exist: {root_uri}")
    canonical_uri = root.as_uri()
    manifest = _readme_manifest(root, readme_names)
    source_name = name or root.name or str(root)
    rules = {"allowed_suffixes": [".md", ".markdown", ".txt"], "recursive_scan": False, "explicit_readmes_only": True}
    with psycopg2.connect(database_url) as connection:
        with connection.cursor() as cur:
            cur.execute("SELECT id FROM iam.tenants WHERE slug=%s", (tenant_slug,))
            tenant = cur.fetchone()
            if tenant is None:
                raise ValueError(f"tenant not found: {tenant_slug}")
            tenant_id = tenant[0]
            cur.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),))
            cur.execute(
                """INSERT INTO knowledge.ingest_sources
                (tenant_id,name,root_uri,domain_hint,graph_space_id,watch_enabled,scan_interval_seconds,file_rules,manifest,status,read_only)
                VALUES(%s,%s,%s,%s,%s,false,0,%s,%s,'active',true)
                ON CONFLICT(tenant_id,root_uri) DO UPDATE SET name=EXCLUDED.name,
                domain_hint=EXCLUDED.domain_hint,graph_space_id=EXCLUDED.graph_space_id,
                file_rules=EXCLUDED.file_rules,manifest=EXCLUDED.manifest,status='active',
                read_only=true,updated_at=now() RETURNING id""",
                (tenant_id, source_name, canonical_uri, domain_hint, graph_space_id, Json(rules), Json(manifest)),
            )
            row = cur.fetchone()
            if row is None:
                raise RuntimeError("source registration returned no id")
            return SourceRegistration(UUID(str(row[0])), canonical_uri, tuple(entry["path"] for entry in manifest["readmes"]))


def main() -> int:
    parser = argparse.ArgumentParser(description="Register a local folder as a read-only knowledge source")
    parser.add_argument("root_uri")
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--name")
    parser.add_argument("--tenant", default="local-dev")
    args = parser.parse_args()
    result = register_read_only_source(args.root_uri, database_url=args.database_url, name=args.name, tenant_slug=args.tenant)
    print(json.dumps({"source_id": str(result.source_id), "root_uri": result.root_uri, "readme_files": result.readme_files, "read_only": result.read_only}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
