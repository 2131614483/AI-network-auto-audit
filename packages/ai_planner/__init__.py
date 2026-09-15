"""CW5: AI 自动组网 — local model planning with deterministic gates.

The AI only produces structured drafts; the compiler is the only authority
that turns a draft into an executable plan, and policy/compiler gates own all
execution permission.  Unknown capabilities, prompt injection, out-of-boundary
data and over-limit revisions fail closed and never silently fall back to a
fake run.
"""

from __future__ import annotations

from .catalog import CapabilityEntry, capability_catalog_from_db
from .chat import CanvasChatPlanner, build_chat_goal, reply_text_for
from .errors import CompileIssue, compile_issues
from .planner import AiPlanner, PlanningOutcome
from .templates import TEMPLATES

__all__ = [
    "AiPlanner",
    "CanvasChatPlanner",
    "CapabilityEntry",
    "CompileIssue",
    "PlanningOutcome",
    "TEMPLATES",
    "build_chat_goal",
    "capability_catalog_from_db",
    "compile_issues",
    "reply_text_for",
]
