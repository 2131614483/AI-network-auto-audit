"""CW5: apply blueprint input-port contracts to an already-initialized
database (idempotent, safe to re-run).

Applies the *effective* contracts, i.e. migration 0053 as corrected by 0059.
``finding-draft-slot``'s input must be ``audit-quality-candidates`` — the name
its producer (``ledger-quality-slot``) declares, and the one the chain resolver
intersects on.  0053 originally wrote the runtime-layer alias ``candidates``,
which shares the schema but not the name, so materializing the seeded
``ledger-quality-slot -> finding-draft-slot`` edge failed with "no shared
contract item".

Run:  python scripts/apply-blueprint-input-contracts.py
"""
from __future__ import annotations

import os

import psycopg2
import psycopg2.extras

DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql://audit_app:admin@localhost:5432/audit_network"
)

_CONTRACTS = {
    "ledger-quality-slot": {"inputs": ["ledger"]},
    "finding-draft-slot": {"inputs": ["audit-quality-candidates"]},
}


def main() -> None:
    with psycopg2.connect(DATABASE_URL) as connection:
        with connection.cursor() as cur:
            cur.execute("SELECT id FROM iam.tenants WHERE slug='local-dev'")
            rows = cur.fetchall()
            assert rows, "local-dev tenant not found"
            for (tenant_id,) in rows:
                cur.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant_id),))
                cur.fetchone()
                for key, patch in _CONTRACTS.items():
                    cur.execute(
                        "SELECT capability_contract FROM topology.plugin_blueprints "
                        "WHERE key=%s AND NOT capability_contract ? 'inputs'",
                        (key,),
                    )
                    target = cur.fetchone()
                    if target is None:
                        print(f"skip {key}: already has inputs")
                        continue
                    merged = dict(target[0])
                    merged["inputs"] = patch["inputs"]
                    cur.execute(
                        "UPDATE topology.plugin_blueprints SET capability_contract=%s WHERE key=%s",
                        (psycopg2.extras.Json(merged), key),
                    )
                    print(f"updated {key} -> inputs={patch['inputs']}")
        connection.commit()
    print("done")


if __name__ == "__main__":
    main()
