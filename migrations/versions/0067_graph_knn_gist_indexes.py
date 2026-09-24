"""GiST trigram indexes for KNN intent recall.

Recall needs "the N nodes closest to this phrase" over a graph that is meant to
hold 100 000+ plugins.  The GIN trigram indexes from 0048 support ``%`` (=
``similarity >= threshold``), but that operator depends on a *selectivity
estimate* to be chosen — and the estimate collapses to a 1 % guess when the query
runs as a non-superuser role subject to RLS (``graph.nodes`` is RLS-protected;
measured at 50 000 nodes: ``rows=1..509`` estimated versus 7 actual).  The planner
then picks a sequential scan, which is exactly the linear cost the index existed
to avoid.  ``EXPLAIN`` confirms it: as ``audit_app`` the GIN index is never
touched, while the same query as a superuser uses it.

GiST's ``<->`` distance supports KNN: ``ORDER BY label <-> q LIMIT k`` walks the
index in distance order and stops, so it needs no selectivity estimate at all.
Measured at 50 000 nodes as ``audit_app``: 244–274 ms (sequential) versus
6.0–6.6 ms (KNN), with identical results.

The GIN indexes are deliberately kept — other queries may still use ``%``, and
``tests/integration/test_plugin_topology_graph_planning_integration.py`` asserts
they exist.  These indexes are additive.

``CREATE INDEX`` (not ``CONCURRENTLY``) is used because migrations run explicitly
and are not applied at application start-up; on the current graph (hundreds of
nodes) it is instant.
"""
from alembic import op

revision = "0067_graph_knn_gist_indexes"
down_revision = "0066_rules_lineage_read_policy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX IF NOT EXISTS graph_nodes_label_gist_trgm_idx "
        "ON graph.nodes USING gist (label gist_trgm_ops)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS graph_node_aliases_alias_gist_trgm_idx "
        "ON graph.node_aliases USING gist (alias gist_trgm_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS graph.graph_nodes_label_gist_trgm_idx")
    op.execute("DROP INDEX IF EXISTS graph.graph_node_aliases_alias_gist_trgm_idx")
