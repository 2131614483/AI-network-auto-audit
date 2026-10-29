#!/usr/bin/env python
"""Read-only schema revision gate for `docker compose`.

`docker compose up` must not upgrade the database: this project requires
migrations to be explicit.  This script only reads `alembic_version` and
compares it with the migration head, so the compose gate can refuse to start
`api` / `worker` against a database that is behind, and print the command that
actually migrates it.

Exit codes: 0 at head, 1 behind or empty, 2 the version cannot be read.
"""

from __future__ import annotations

import os
import sys

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine

MIGRATE_HINT = "docker compose --profile migrate run --rm migrate"


def main() -> int:
    url = os.getenv("DATABASE_URL")
    if not url:
        print("[check] DATABASE_URL is not set", file=sys.stderr)
        return 2
    heads = set(ScriptDirectory.from_config(Config("alembic.ini")).get_heads())
    try:
        engine = create_engine(url)
        with engine.connect() as connection:
            current = set(MigrationContext.configure(connection).get_current_heads())
        engine.dispose()
    except Exception as exc:
        print(f"[check] cannot read the schema version: {exc}", file=sys.stderr)
        return 2
    if not current:
        print("[check] database has no migration version yet (empty database)", file=sys.stderr)
        print(f"[check] migrate explicitly first: {MIGRATE_HINT}", file=sys.stderr)
        return 1
    if current != heads:
        print(f"[check] schema behind: current={sorted(current)} head={sorted(heads)}",
              file=sys.stderr)
        print(f"[check] migrate explicitly first: {MIGRATE_HINT}", file=sys.stderr)
        return 1
    print(f"[check] schema at head: {sorted(current)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
