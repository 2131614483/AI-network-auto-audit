"""Anchor a finished run to the business project it was performed for.

The experience layer already records *what* ran (``node_observations`` /
``edge_observations``), but nothing recorded *who it was run for*, so "which
projects has this plugin been proven in?" — the question the knowledge graph is
supposed to answer — had no answer.

``experience.archive_links`` is that anchor: an append-only, tenant-scoped
``(run, project)`` pair.  It is an observation, not a property of the plugin:
attributing a plugin to a project is something that *happened* in a run, and
later edits must never rewrite what an archived run meant.

The link lives in ``experience`` (not ``topology``) because it is accumulated
evidence from real runs, beside the observations it is joined against.

Also carries ``plugin_version`` onto ``node_observations`` — the follow-through
of ``0060``, which gave ``control.node_attempts`` the version.  Without it the
project anchor could only say "this plugin", not "this plugin version", which is
the whole point of the pinning done in ``0061``.
"""

from alembic import op

revision = "0062_archive_link"
down_revision = "0061_plugin_version_pin"
branch_labels = None
depends_on = None

_TABLE = "experience.archive_links"
#: Chosen after checking it collides with none of the ~700 rule ids already in
#: use.  A collision is not silently tolerated — see the assertion below.
_ARCHIVE_RULE_ID = "7d2a493b-6c18-4f57-9b03-e8452c7f1d60"


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE experience.node_observations
          ADD COLUMN IF NOT EXISTS plugin_version text NOT NULL DEFAULT '';
        """
    )
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_TABLE} (
          archive_link_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id uuid NOT NULL REFERENCES iam.tenants(id),
          run_id uuid NOT NULL,
          project_id uuid NOT NULL REFERENCES iam.projects(id),
          note text NOT NULL DEFAULT '',
          trace_id text NOT NULL,
          archived_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (tenant_id, run_id, project_id)
        );
        """
    )
    op.execute(
        f"""
        ALTER TABLE {_TABLE} ENABLE ROW LEVEL SECURITY;
        ALTER TABLE {_TABLE} FORCE ROW LEVEL SECURITY;
        DROP POLICY IF EXISTS archive_links_tenant_isolation ON {_TABLE};
        CREATE POLICY archive_links_tenant_isolation ON {_TABLE}
          USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
          WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid);
        -- Append-only for the app role: an archived link is evidence and is
        -- never updated or deleted (same stance as 0055's observation tables).
        GRANT SELECT, INSERT ON {_TABLE} TO audit_app;
        CREATE INDEX IF NOT EXISTS archive_links_project_idx ON {_TABLE} (tenant_id, project_id);
        CREATE INDEX IF NOT EXISTS archive_links_run_idx ON {_TABLE} (tenant_id, run_id);
        """
    )
    # Append one grant to the local-dev topology read set rather than restating
    # the whole rule list (0056's eleven rules verbatim would be a copy that
    # drifts).
    #
    # Two things this must get right, both learned the hard way here:
    # * ``policy.policy_sets`` is FORCE-RLS, so the tenant context has to be set
    #   on the connection first or the UPDATE silently matches zero rows
    #   (0056's reseed does the same);
    # * the idempotency guard keys on the rule *id*, so a collision with one of
    #   the ~700 ids already in use would silently skip the grant.  The
    #   assertion after it turns that silence into a migration failure.
    op.execute(
        f"""
        SELECT set_config(
          'app.tenant_id',
          (SELECT id::text FROM iam.tenants WHERE slug='local-dev'), true);

        UPDATE policy.policy_sets p
        SET rules = p.rules || jsonb_build_array(
          jsonb_build_object('rule_id','{_ARCHIVE_RULE_ID}','effect','allow',
            'match',jsonb_build_object('capabilities',jsonb_build_array('topology.run.archive'),
              'risk_classes',jsonb_build_array('low'))))
        WHERE p.tenant_id = (SELECT id FROM iam.tenants WHERE slug='local-dev')
          AND p.name='local-plugin-topology-read' AND p.version=1
          AND NOT EXISTS (
            SELECT 1 FROM jsonb_array_elements(p.rules) AS r
            WHERE r->>'rule_id' = '{_ARCHIVE_RULE_ID}'
          );

        DO $$
        BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM policy.policy_sets p, jsonb_array_elements(p.rules) AS r
            WHERE p.name = 'local-plugin-topology-read' AND p.version = 1
              AND p.tenant_id = (SELECT id FROM iam.tenants WHERE slug='local-dev')
              AND r->'match'->'capabilities' @> jsonb_build_array('topology.run.archive')
          ) THEN
            RAISE EXCEPTION
              'topology.run.archive was not granted (rule_id collision in 0062?)';
          END IF;
        END $$;
        """
    )


def downgrade() -> None:
    raise RuntimeError("Archived run links are evidence; archive explicitly.")
