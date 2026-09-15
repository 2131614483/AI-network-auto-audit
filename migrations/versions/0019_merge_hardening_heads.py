"""Merge independently developed hardening migrations.

The knowledge, policy/control, and AIOps changes are additive and touch
separate schemas.  This explicit merge keeps a fresh database and an upgraded
development database on one deterministic Alembic head.
"""

revision = "0019_merge_hardening_heads"
down_revision = (
    "0018_aiops_change_authorization",
    "0018_knowledge_graph_hardening",
    "0018_policy_control",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    raise RuntimeError("Hardening migration history is retained and not cascade-dropped.")
