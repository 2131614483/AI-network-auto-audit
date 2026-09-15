"""Seed a local development tenant without touching existing tenants."""
from alembic import op

revision = "0008_seed_local"
down_revision = "0007_app_grants"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    INSERT INTO iam.tenants (slug, name)
    VALUES ('local-dev', 'Local Development')
    ON CONFLICT (slug) DO NOTHING
    """)
    op.execute("""
    SELECT set_config('app.tenant_id', id::text, true) FROM iam.tenants WHERE slug = 'local-dev'
    """)
    op.execute("""
    INSERT INTO iam.projects (tenant_id, slug, name)
    SELECT id, 'default', 'Default Project' FROM iam.tenants WHERE slug = 'local-dev'
    ON CONFLICT (tenant_id, slug) DO NOTHING
    """)


def downgrade() -> None:
    # Seed data is retained to avoid deleting user-created references.
    pass
