from __future__ import annotations

import os

from packages.catalog.registry import PluginRegistry

DB = os.getenv(
    "AUDIT_NETWORK_TEST_DATABASE_URL",
    "postgresql://audit_app:admin@localhost:5432/audit_network_test",
)


def manifest() -> dict[str, object]:
    return {
        "id": "audit.demo-plugin",
        "version": "1.0.0",
        "runtime": "python",
        "entrypoint": "plugin:run",
        "capabilities": ["audit.demo.read"],
        "side_effects": "none",
        "idempotency": "input_snapshot_hash",
        "permissions": {"data_read": ["audit.demo"], "data_write": [], "network": "none"},
        "resources": {"cpu": 1, "memory_mb": 128, "gpu": False, "timeout_seconds": 30},
        "ui": {
            "schema_version": "1.0.0",
            "navigation": [{"slot": "workspace.tools", "title_key": "demo.title", "route": "/plugins/demo"}],
            "views": [{"id": "overview", "renderer": "data-table", "data_source": "audit.demo.list"}],
            "actions": [],
            "i18n": ["zh-CN"],
        },
    }


def test_manifest_validation_and_registration() -> None:
    registry = PluginRegistry(DB)
    plugin_version_id = registry.register(manifest(), descriptor_sha256="c" * 64)
    assert str(plugin_version_id)
