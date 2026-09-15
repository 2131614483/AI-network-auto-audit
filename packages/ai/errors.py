"""Fault taxonomy for the unified AI access layer.

Every AI transport failure — chat, planning, embedding, settings probe —
ultimately raises a subclass of :class:`AIClientError`.  The pre-existing
``OpenAICompatChatError`` and ``OllamaChatError`` now derive from this base, so
call sites written against either legacy name keep working unchanged while new
code can catch a single type.
"""

from __future__ import annotations


class AIClientError(RuntimeError):
    """The configured model backend is unreachable, unauthenticated or unusable."""


class AIConfigurationError(AIClientError):
    """The provider selection itself is invalid or incomplete.

    Distinct from :class:`AIClientError` because a bad ``AI_PROVIDER`` is a
    configuration mistake (fixable in the AI settings page), not a transient
    transport outage of an otherwise valid provider.
    """
