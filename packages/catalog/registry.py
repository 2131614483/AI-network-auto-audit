from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import UUID

import psycopg2
from jsonschema import Draft202012Validator
from psycopg2.extras import Json, register_uuid
from referencing import Registry, Resource

register_uuid()  # type: ignore[no-untyped-call]


class PluginVersionConflictError(ValueError):
    """A published ``(plugin, version)`` already exists with different bytes.

    A version is immutable: changing a plugin means publishing a *new* version.
    Silently overwriting the row would make a published version mutable and its
    stored manifest unrecoverable.
    """


class PluginRegistry:
    def __init__(self, database_url: str, schema_dir: str | Path = "contracts/jsonschema", tenant_slug: str = "local-dev") -> None:
        self.database_url = database_url
        self.schema_dir = Path(schema_dir)
        self.tenant_slug = tenant_slug

    def validate_manifest(self, manifest: dict[str, Any]) -> None:
        schema = json.loads((self.schema_dir / "plugin-manifest.schema.json").read_text(encoding="utf-8"))
        ui_schema = json.loads((self.schema_dir / "ui-contribution.schema.json").read_text(encoding="utf-8"))
        registry = Registry()
        for name in ("ui-contribution.schema.json", "ui-action.schema.json", "ui-query.schema.json", "ui-slot.schema.json", "renderer-descriptor.schema.json"):
            registry = registry.with_resource(
                f"https://audit.local/contracts/{name}",
                Resource.from_contents(json.loads((self.schema_dir / name).read_text(encoding="utf-8"))),
            )
        validator = Draft202012Validator(schema, registry=registry)
        errors = sorted(validator.iter_errors(manifest), key=lambda error: list(error.path))
        if errors:
            raise ValueError("invalid plugin manifest: " + "; ".join(error.message for error in errors[:3]))
        if manifest.get("ui"):
            ui_errors = list(Draft202012Validator(ui_schema, registry=registry).iter_errors(manifest["ui"]))
            if ui_errors:
                raise ValueError("invalid plugin UI contribution: " + ui_errors[0].message)

    def register(self, manifest: dict[str, Any], *, descriptor_sha256: str) -> UUID:
        """Publish one plugin version; ``(plugin, version)`` is immutable once pinned.

        ``descriptor_sha256`` is the raw-byte hash of the plugin's protocol
        descriptor — the same value ``plugin.runtime-binding.json`` pins.  The
        first registration of a version stamps it; a later attempt to publish
        the same version with *different* bytes raises
        :class:`PluginVersionConflictError` rather than overwriting the stored
        manifest.  Registering the identical version again is idempotent.

        Rows published before this column existed carry ``''`` ("not yet
        pinned") and adopt the supplied hash on their next registration.
        """
        self.validate_manifest(manifest)
        with psycopg2.connect(self.database_url) as connection:
            with connection.cursor() as cur:
                cur.execute("SELECT id FROM iam.tenants WHERE slug=%s", (self.tenant_slug,))
                tenant = cur.fetchone()
                if tenant is None:
                    raise ValueError(f"tenant not found: {self.tenant_slug}")
                tenant_id = tenant[0]
                cur.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),))
                plugin_key = manifest["id"]
                cur.execute(
                    """INSERT INTO catalog.plugins(tenant_id,key,name,category,description) VALUES(%s,%s,%s,%s,%s)
                    ON CONFLICT(tenant_id,key) DO UPDATE SET name=EXCLUDED.name, description=EXCLUDED.description RETURNING id""",
                    (tenant_id, plugin_key, manifest.get("name", plugin_key), "plugin", manifest.get("description")),
                )
                plugin_row = cur.fetchone()
                if plugin_row is None:
                    raise RuntimeError("plugin upsert returned no id")
                version = manifest["version"]
                cur.execute(
                    """SELECT id, descriptor_sha256 FROM catalog.plugin_versions
                       WHERE tenant_id=%s AND plugin_id=%s AND version=%s""",
                    (tenant_id, plugin_row[0], version),
                )
                existing = cur.fetchone()
                if existing is not None:
                    version_id, pinned = existing[0], str(existing[1] or "")
                    if pinned and pinned != descriptor_sha256:
                        raise PluginVersionConflictError(
                            f"{plugin_key}@{version} is already published with a different "
                            "descriptor; publish a new version instead of overwriting"
                        )
                    if not pinned:
                        # Published before versions were pinned: adopt the current
                        # descriptor as the baseline, once.
                        cur.execute(
                            "UPDATE catalog.plugin_versions SET descriptor_sha256=%s WHERE id=%s",
                            (descriptor_sha256, version_id),
                        )
                    return UUID(str(version_id))
                cur.execute(
                    """INSERT INTO catalog.plugin_versions(tenant_id,plugin_id,version,manifest_json,runtime,entrypoint,side_effect_class,status,published_at,descriptor_sha256)
                    VALUES(%s,%s,%s,%s,%s,%s,%s,'published',now(),%s) RETURNING id""",
                    (
                        tenant_id, plugin_row[0], version, Json(manifest),
                        manifest["runtime"], manifest["entrypoint"], manifest["side_effects"],
                        descriptor_sha256,
                    ),
                )
                version_row = cur.fetchone()
                if version_row is None:
                    raise RuntimeError("plugin version insert returned no id")
                ui = manifest.get("ui")
                if ui:
                    for index, view in enumerate(ui.get("views", [])):
                        cur.execute(
                            """INSERT INTO catalog.ui_contributions(tenant_id,plugin_version_id,contribution_key,schema_version,contribution_type,slot,renderer,config_json)
                            VALUES(%s,%s,%s,%s,'view','workspace.tools',%s,%s)
                            ON CONFLICT(tenant_id,plugin_version_id,contribution_key) DO UPDATE SET config_json=EXCLUDED.config_json""",
                            (tenant_id, version_row[0], view.get("id", f"view-{index}"), ui["schema_version"], view.get("renderer"), Json(view)),
                        )
                return UUID(str(version_row[0]))
