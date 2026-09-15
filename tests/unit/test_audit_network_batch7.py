# -*- coding: utf-8 -*-
"""Unit tests for the 9 batch-G evidence & workpaper plugins (direct handle)."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from uuid import uuid4

import pytest

from packages.plugin_runtime.runner import ArtifactInput
from plugins.builtin.audit_evidence_e_signature import runtime as esign
from plugins.builtin.audit_evidence_evidence_archive import runtime as archive
from plugins.builtin.audit_evidence_evidence_index_link import runtime as index_link
from plugins.builtin.audit_evidence_workpaper_borrow_approve import runtime as borrow
from plugins.builtin.audit_evidence_workpaper_encrypt_store import runtime as encrypt
from plugins.builtin.audit_evidence_workpaper_reconcile import runtime as reconcile
from plugins.builtin.audit_evidence_workpaper_review3 import runtime as review3
from plugins.builtin.audit_evidence_workpaper_template_update import runtime as tmpl
from plugins.builtin.audit_evidence_workpaper_version_diff import runtime as vdiff

WORKPAPER = {
    "contract_id": "workpaper-export", "contract_version": "1.0.0", "period": "2026",
    "sections": [
        {"section_id": "WP-0001", "title": "1. 资金支付", "claim": "资金支付审批完整", "severity": "low", "version": "v1"},
        {"section_id": "WP-0002", "title": "2. 采购验收", "claim": "采购验收程序有效", "severity": "medium", "version": "v1"},
    ],
}


_SHARED_TMP: Path | None = None


def _write(payload: dict) -> ArtifactInput:
    global _SHARED_TMP
    if _SHARED_TMP is None:
        _SHARED_TMP = Path(tempfile.mkdtemp())
        os.environ["AUDIT_PLUGIN_READ_ROOTS"] = json.dumps([str(_SHARED_TMP)])
    path = _SHARED_TMP / f"in-{uuid4().hex}.json"
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    path.write_bytes(raw)
    return ArtifactInput(
        artifact_id=uuid4(), tenant_id=uuid4(), uri=path.resolve().as_uri(),
        media_type="application/json", sha256=hashlib.sha256(raw).hexdigest(),
        size_bytes=len(raw), classification="audit_confidential",
    )


def _env(plugin_id: str, capability: str, **ports) -> dict:
    env = {
        "protocol": "audit-network-plugin-child-v1",
        "plugin_id": plugin_id,
        "capability": capability,
        "trace_id": str(uuid4()),
    }
    for name, payload in ports.items():
        art = _write(payload)
        env[name.replace("_", "-")] = {"contract_id": "artifact-ref", "contract_version": "1.0.0",
                                       "artifact": {"uri": art.uri, "sha256": art.sha256, "size_bytes": art.size_bytes}}
    return env


def test_evidence_archive_classifies() -> None:
    env = _env("audit.evidence.evidence-archive", "audit.evidence.evidence-archive",
               evidence_input={"contract_id": "artifact-ref", "contract_version": "1.0.0", "period": "2026",
                               "photos": [{"id": "PH-1", "kind": "photo", "source_ref": "field-evidence-photo"},
                                          {"id": "CF-1", "kind": "confirm", "source_ref": "field-confirm-letter"}]})
    out = archive.handle(env)
    assert out["artifact"]["kinds"]["photo"] == 1
    assert out["artifact"]["kinds"]["confirm"] == 1
    assert len(out["artifact"]["archived"]) == 2


def test_evidence_archive_rejects_empty() -> None:
    env = _env("audit.evidence.evidence-archive", "audit.evidence.evidence-archive",
               evidence_input={"contract_id": "artifact-ref", "contract_version": "1.0.0", "photos": []})
    with pytest.raises(Exception):
        archive.handle(env)


def test_index_link_builds_links() -> None:
    env = _env("audit.evidence.evidence-index-link", "audit.evidence.evidence-index-link",
               workpaper_draft=WORKPAPER,
               photo_evidence={"contract_id": "artifact-ref", "contract_version": "1.0.0",
                               "evidence_id": "EV-2026-001", "uri": "file:///evidence/PH-1.png"})
    out = index_link.handle(env)
    assert out["contract_id"] == "evidence-lineage"
    assert len(out["nodes"]) == 3  # 2 workpaper + 1 evidence
    assert len(out["edges"]) == 2
    assert out["nodes"][0]["sha256"] and len(out["nodes"][0]["sha256"]) == 64
    assert out["edges"][0]["target"] == "evidence:EV-2026-001"


def test_workpaper_reconcile_ok() -> None:
    env = _env("audit.evidence.workpaper-reconcile", "audit.evidence.workpaper-reconcile",
               workpaper_draft=WORKPAPER)
    out = reconcile.handle(env)
    assert out["checked"] == 2
    assert out["passed"] == 2
    assert out["failed"] == 0


def test_workpaper_reconcile_flags_negative() -> None:
    wp = {"contract_id": "workpaper-export", "contract_version": "1.0.0", "period": "2026",
          "sections": [{"section_id": "WP-X", "claim": "", "severity": "low"}]}
    env = _env("audit.evidence.workpaper-reconcile", "audit.evidence.workpaper-reconcile",
               workpaper_draft=wp)
    out = reconcile.handle(env)
    assert out["failed"] == 1
    assert out["issues"][0]["level"] == "warn"


def test_review3_approves_when_clean() -> None:
    rc = {"contract_id": "dataset-validation", "contract_version": "1.0.0",
          "dataset": "workpaper-reconcile", "checked": 2, "passed": 2, "failed": 0, "issues": []}
    env = _env("audit.evidence.workpaper-review3", "audit.evidence.workpaper-review3",
               workpaper_draft=WORKPAPER, reconcile_report=rc)
    out = review3.handle(env)
    assert out["artifact"]["review_status"][0]["approved"] is True
    assert out["artifact"]["review_status"][0]["current"] == "head"


def test_e_signature_stamps_approved() -> None:
    rs = {"contract_id": "document-content", "contract_version": "1.0.0", "period": "2026",
          "artifact": {"review_status": [{"workpaper_id": "WP-0001", "approved": True},
                                         {"workpaper_id": "WP-0002", "approved": True}]}}
    env = _env("audit.evidence.e-signature", "audit.evidence.e-signature", review_status=rs)
    out = esign.handle(env)
    assert out["artifact"]["signed_workpapers"][0]["signed"] is True
    assert out["artifact"]["signed_workpapers"][0]["seal_id"].startswith("SEAL-")


def test_encrypt_store_archives() -> None:
    sw = {"contract_id": "document-content", "contract_version": "1.0.0", "period": "2026",
          "artifact": {"signed_workpapers": [{"workpaper_id": "WP-0001", "signed": True},
                                             {"workpaper_id": "WP-0002", "signed": True}]}}
    env = _env("audit.evidence.workpaper-encrypt-store", "audit.evidence.workpaper-encrypt-store",
               signed_workpaper=sw)
    out = encrypt.handle(env)
    assert len(out["artifact"]["stored"]) == 2
    assert out["artifact"]["stored"][0]["encrypted"] is True


def test_version_diff_reports_changes() -> None:
    env = _env("audit.evidence.workpaper-version-diff", "audit.evidence.workpaper-version-diff",
               workpaper_draft=WORKPAPER)
    out = vdiff.handle(env)
    assert len(out["artifact"]["version_diffs"]) == 2
    assert out["artifact"]["version_diffs"][0]["change_count"] >= 1


def test_template_update_aligns() -> None:
    env = _env("audit.evidence.workpaper-template-update", "audit.evidence.workpaper-template-update",
               workpaper_draft=WORKPAPER)
    out = tmpl.handle(env)
    assert len(out["artifact"]["template_updates"]) == 2
    assert out["artifact"]["template_updates"][0]["standard_version"] == "2026.1"


def test_borrow_approve_decides() -> None:
    req = {"contract_id": "document-content", "contract_version": "1.0.0", "period": "2026",
           "requests": [{"request_id": "B-1", "workpaper_id": "WP-0001", "purpose": "底稿复核"},
                        {"request_id": "B-2", "workpaper_id": "WP-0002", "purpose": ""}]}
    env = _env("audit.evidence.workpaper-borrow-approve", "audit.evidence.workpaper-borrow-approve",
               borrow_request=req)
    out = borrow.handle(env)
    statuses = out["artifact"]["borrow_status"]
    assert statuses[0]["approved"] is True
    assert statuses[1]["approved"] is False
