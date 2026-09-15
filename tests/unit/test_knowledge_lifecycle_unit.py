from __future__ import annotations

from uuid import uuid4

import pytest

from packages.knowledge.lifecycle import ChangeOperation, KnowledgeLifecycleService, _checksum


def test_checksum_is_stable_for_uuid_payloads() -> None:
    value = {"id": uuid4(), "items": [1, 2, 3]}
    assert _checksum(value) == _checksum(value)


def test_unsupported_change_kind_is_rejected_before_database_access() -> None:
    with pytest.raises(ValueError, match="only support graph.node"):
        KnowledgeLifecycleService("postgresql://invalid").create_changeset(
            "bad", "test", [ChangeOperation("graph.edge", "create", {})]
        )
