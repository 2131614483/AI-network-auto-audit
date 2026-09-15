"""A published plugin version is pinned to the descriptor bytes it came from.

``catalog.plugin_versions`` used to record a version string and the manifest and
nothing else, and ``PluginRegistry.register`` upserted with ``DO UPDATE SET
manifest_json = EXCLUDED.manifest_json``.  So the same ``0.1.0`` could mean
different bytes, and republishing a version silently rewrote its history.

Now the first registration of a ``(plugin, version)`` stamps ``descriptor_sha256``
(the protocol descriptor's raw-byte hash — the same value
``plugin.runtime-binding.json`` pins), and any later registration of that version
with different bytes is refused.  Changing a plugin means publishing a new
version.
"""
from __future__ import annotations

import json
from uuid import UUID

import psycopg2
import pytest

from packages.catalog.registry import PluginRegistry, PluginVersionConflictError
from packages.plugin_runtime.layout import declaration_dir
from packages.plugin_runtime.registration import register_verified_builtin
from packages.plugin_runtime.runner import load_verified_binding, verified_builtin_ids

TEST_DB = "postgresql://audit_app:admin@localhost:5432/audit_network_test"
_SAMPLE = "audit.ledger-quality"


def _registry() -> PluginRegistry:
    return PluginRegistry(TEST_DB, schema_dir="contracts/jsonschema", tenant_slug="local-dev")


def _manifest(plugin_id: str) -> dict:
    return json.loads((declaration_dir(plugin_id) / "plugin.manifest.json").read_text(encoding="utf-8"))


def _stored_descriptor(plugin_id: str) -> str:
    with psycopg2.connect(TEST_DB) as connection, connection.cursor() as cur:
        cur.execute(
            "SELECT set_config('app.tenant_id',(SELECT id::text FROM iam.tenants WHERE slug='local-dev'),false)"
        )
        cur.execute(
            """SELECT v.descriptor_sha256 FROM catalog.plugin_versions v
               JOIN catalog.plugins p ON p.id = v.plugin_id
               WHERE p.key=%s AND v.version=%s""",
            (plugin_id, _manifest(plugin_id)["version"]),
        )
        row = cur.fetchone()
        assert row is not None, f"{plugin_id} is not registered"
        return str(row[0])


def test_every_verified_builtin_version_is_pinned_to_its_protocol_hash() -> None:
    """The catalog hash is the runtime binding's hash — not some other value."""
    register_verified_builtin(TEST_DB)
    for plugin_id in verified_builtin_ids():
        binding = load_verified_binding(plugin_id)
        assert _stored_descriptor(plugin_id) == binding.protocol_sha256, plugin_id


def test_re_registering_the_same_version_is_idempotent() -> None:
    registry = _registry()
    manifest = _manifest(_SAMPLE)
    descriptor = load_verified_binding(_SAMPLE).protocol_sha256
    first = registry.register(manifest, descriptor_sha256=descriptor)
    second = registry.register(manifest, descriptor_sha256=descriptor)
    assert isinstance(first, UUID) and first == second


def test_same_version_with_different_bytes_is_rejected() -> None:
    registry = _registry()
    manifest = _manifest(_SAMPLE)
    descriptor = load_verified_binding(_SAMPLE).protocol_sha256
    registry.register(manifest, descriptor_sha256=descriptor)

    with pytest.raises(PluginVersionConflictError, match="different descriptor"):
        registry.register(manifest, descriptor_sha256="b" * 64)

    # The rejection is a refusal, not a partial write: the pinned hash survives.
    assert _stored_descriptor(_SAMPLE) == descriptor


# Note: immutability is per ``(plugin, version)``, not per plugin — a *new*
# version is free to carry the same descriptor bytes.  There is deliberately no
# test asserting that a new version registers, because doing so would insert a
# row and ``catalog.plugin_versions`` is append-only for the app role (no DELETE
# privilege, by design), so the row could not be cleaned up.
