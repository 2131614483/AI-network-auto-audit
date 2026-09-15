"""Pin a published plugin version to the descriptor bytes it was published from.

``catalog.plugin_versions`` recorded a version string and the manifest, and
``PluginRegistry.register`` upserted with ``ON CONFLICT ... DO UPDATE SET
manifest_json = EXCLUDED.manifest_json``.  Two consequences:

* a version carried no content hash, so nothing tied the row to the protocol
  descriptor it claims to publish — the same ``0.1.0`` could silently mean
  different bytes (the repository already has a live example of exactly that
  failure mode: port ``schema_sha256`` filled with the placeholder ``"a"*64``);
* republishing an existing ``(plugin, version)`` overwrote the stored manifest
  in place, so a published version was mutable and its history unrecoverable.

``descriptor_sha256`` is the protocol descriptor's raw-byte hash — the same
value ``plugin.runtime-binding.json`` pins and ``load_verified_binding``
recomputes.  Existing rows default to ``''``, which reads as "not yet pinned":
``PluginRegistry.register`` adopts the current hash on the next registration and
rejects any later change.  That keeps already-registered tenants working without
a data migration that would have to read plugin files from inside alembic.
"""

from alembic import op

revision = "0061_plugin_version_pin"
down_revision = "0060_attempt_plugin_version"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE catalog.plugin_versions
          ADD COLUMN IF NOT EXISTS descriptor_sha256 text NOT NULL DEFAULT '';
        """
    )


def downgrade() -> None:
    raise RuntimeError("Published plugin versions are retained; archive explicitly.")
