"""Validate the generated 100-plugin audit network (ComfyUI-style folders).

Checks every audit_* folder (excluding the pre-existing verified audit
plugins) has protocol + manifest that validate against the project jsonschema,
ids are unique, every capability is read-only contract_only, and the port
contracts are well-formed.
"""
from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]
PLUGINS = ROOT / "plugins" / "builtin"
PROTOCOL_SCHEMA = json.loads(
    (ROOT / "contracts" / "jsonschema" / "unified-plugin-protocol.schema.json").read_text(encoding="utf-8")
)
MANIFEST_SCHEMA = json.loads(
    (ROOT / "contracts" / "jsonschema" / "plugin-manifest.schema.json").read_text(encoding="utf-8")
)

# The 7 pre-existing verified audit plugins are the network's reusable nucleus;
# the 100 new folders live alongside them without overwriting them.
PRE_EXISTING_AUDIT = {
    "audit_evidence_lineage", "audit_finding_draft", "audit_investigation_plan",
    "audit_journal_anomaly", "audit_ledger_quality", "audit_report_draft",
    "audit_workpaper_export",
}

# 34 network plugins implemented with a verified runtime + binding (2026-09-10, batch D field stage).
VERIFIED_NETWORK = {
    "audit_foundation_finance_clean", "audit_risk_finance_anomaly_alert",
    "audit_risk_risk_matrix_build", "audit_finding_issue_type_judge",
    "audit_finding_issue_amount_compute", "audit_report_issue_desc_write",
    "audit_foundation_quality_check", "audit_foundation_tag_manage",
    "audit_foundation_metric_compute",
    "audit_foundation_nlp_process", "audit_foundation_rule_engine",
    "audit_foundation_ocr_extract",
    "audit_evidence_evidence_verify", "audit_finding_suspicion_merge",
    "audit_remedy_remedy_progress_track",
    "audit_finding_violation_clause_match", "audit_finding_responsible_party_find",
    "audit_finding_issue_grade", "audit_finding_auditee_feedback",
    "audit_finding_issue_final_review", "audit_field_suspicion_flag",
    # batch D: field stage (现场实施) — 13 plugins
    "audit_field_asset_check", "audit_field_audit_log", "audit_field_confirm_letter",
    "audit_field_cross_dept_inquiry", "audit_field_evidence_photo",
    "audit_field_extension_approve", "audit_field_interview_record",
    "audit_field_inventory_count", "audit_field_meeting_minutes",
    "audit_field_progress_report", "audit_field_site_checkin_track",
    "audit_field_voucher_drilldown", "audit_field_workpaper_build",
    # batch E: mandate (立项) + plan (计划) — 19 plugins
    "audit_mandate_demand_collect", "audit_mandate_strategy_align",
    "audit_mandate_annual_propose", "audit_mandate_proposal_score",
    "audit_mandate_project_library", "audit_mandate_priority_rank",
    "audit_mandate_notice_generate", "audit_mandate_material_submit",
    "audit_mandate_material_precheck", "audit_mandate_team_forming",
    "audit_plan_annual_plan_build", "audit_plan_project_scheme_build",
    "audit_plan_program_template_match", "audit_plan_sampling_select",
    "audit_plan_sample_size_compute", "audit_plan_staff_schedule",
    "audit_plan_effort_budget", "audit_plan_resource_conflict_detect",
    "audit_plan_plan_version_control",
    # batch F: risk (风险识别与评估) — 9 plugins
    "audit_risk_macro_policy_risk_scan", "audit_risk_industry_risk_benchmark",
    "audit_risk_internal_control_risk_map", "audit_risk_process_gap_detect",
    "audit_risk_fraud_risk_match", "audit_risk_risk_level_assign",
    "audit_risk_high_risk_area_locate", "audit_risk_risk_heatmap_draw",
    "audit_risk_risk_advice_generate",
    # batch G: evidence & workpaper (证据与底稿管理) — 9 plugins
    "audit_evidence_evidence_archive", "audit_evidence_evidence_index_link",
    "audit_evidence_workpaper_reconcile", "audit_evidence_workpaper_review3",
    "audit_evidence_e_signature", "audit_evidence_workpaper_encrypt_store",
    "audit_evidence_workpaper_version_diff", "audit_evidence_workpaper_template_update",
    "audit_evidence_workpaper_borrow_approve",
    # batch H: report (报告与成果输出) — 6 plugins
    "audit_report_report_frame_build", "audit_report_advice_match",
    "audit_report_report_data_check", "audit_report_report_multi_review",
    "audit_report_result_distill", "audit_report_notice_mask_publish",
    # batch I: remedy (整改跟踪与闭环管理) — 6 plugins
    "audit_remedy_remedy_dispatch", "audit_remedy_remedy_plan_review",
    "audit_remedy_remedy_overdue_alert", "audit_remedy_remedy_effect_verify",
    "audit_remedy_remedy_close", "audit_remedy_remedy_publish",
    # batch J: govern (治理优化层) — 6 plugins
    "audit_govern_project_quality_score", "audit_govern_effect_evaluate",
    "audit_govern_case_library_update", "audit_govern_issue_trend_analysis",
    "audit_govern_rule_iteration", "audit_govern_method_distill",
    # batch K: foundation/support layer (补缺) — 11 plugins
    "audit_foundation_biz_standardize", "audit_foundation_data_encrypt",
    "audit_foundation_data_mask", "audit_foundation_fulltext_search",
    "audit_foundation_lineage_track", "audit_foundation_master_mapping",
    "audit_foundation_metadata_manage", "audit_foundation_multi_source_collect",
    "audit_foundation_permission_control", "audit_foundation_viz_analysis",
    "audit_foundation_workflow_engine",
}

NETWORK_FOLDERS = [
    p for p in sorted(PLUGINS.iterdir())
    if p.is_dir() and p.name.startswith("audit_") and p.name not in PRE_EXISTING_AUDIT
]

EXPECTED_COUNT = 100


def _load(folder: Path, name: str) -> dict:
    path = folder / name
    assert path.is_file(), f"missing {name} in {folder.name}"
    return json.loads(path.read_text(encoding="utf-8"))


def test_network_has_exactly_100_plugin_folders() -> None:
    assert len(NETWORK_FOLDERS) == EXPECTED_COUNT, (
        f"expected {EXPECTED_COUNT} audit-network folders, got {len(NETWORK_FOLDERS)}"
    )


def test_every_folder_has_protocol_and_manifest() -> None:
    for folder in NETWORK_FOLDERS:
        _load(folder, "plugin.protocol.json")
        _load(folder, "plugin.manifest.json")


def test_every_protocol_validates_against_unified_schema() -> None:
    validator = Draft202012Validator(PROTOCOL_SCHEMA)
    for folder in NETWORK_FOLDERS:
        protocol = _load(folder, "plugin.protocol.json")
        errors = sorted(validator.iter_errors(protocol), key=lambda e: list(e.path))
        assert not errors, f"{folder.name}: {errors[0].message}"


def test_every_manifest_validates_against_manifest_schema() -> None:
    validator = Draft202012Validator(MANIFEST_SCHEMA)
    for folder in NETWORK_FOLDERS:
        manifest = _load(folder, "plugin.manifest.json")
        errors = sorted(validator.iter_errors(manifest), key=lambda e: list(e.path))
        assert not errors, f"{folder.name}: {errors[0].message}"


def test_ids_are_unique_and_match_folder() -> None:
    protocol_ids = [_load(f, "plugin.protocol.json")["id"] for f in NETWORK_FOLDERS]
    manifest_ids = [_load(f, "plugin.manifest.json")["id"] for f in NETWORK_FOLDERS]
    assert len(set(protocol_ids)) == EXPECTED_COUNT, "duplicate protocol id"
    assert protocol_ids == manifest_ids, "protocol/manifest id mismatch"
    for folder, pid in zip(NETWORK_FOLDERS, protocol_ids):
        assert pid.startswith("audit."), (folder.name, pid)


def test_capabilities_unique_and_read_only_contract_only() -> None:
    caps: list[str] = []
    for folder in NETWORK_FOLDERS:
        protocol = _load(folder, "plugin.protocol.json")
        expected_lifecycle = "verified" if folder.name in VERIFIED_NETWORK else "contract_only"
        assert protocol["lifecycle"] == expected_lifecycle, folder.name
        assert protocol["governance"]["policy"]["gateway_required"] is True, folder.name
        for capability in protocol["capabilities"]:
            assert capability["side_effects"] == "read_only", (folder.name, capability["id"])
            assert capability["inputs"] and capability["outputs"], (folder.name, capability["id"])
            caps.append(capability["id"])
            assert capability["id"] == protocol["id"], folder.name
    assert len(set(caps)) == EXPECTED_COUNT, "duplicate capability id"


def test_verified_network_plugins_have_bindings() -> None:
    for folder in NETWORK_FOLDERS:
        if folder.name not in VERIFIED_NETWORK:
            continue
        binding = _load(folder, "plugin.runtime-binding.json")
        assert binding["status"] == "verified", folder.name
        assert binding["execution_mode"] == "isolated_subprocess", folder.name
        protocol = _load(folder, "plugin.protocol.json")
        assert binding["protocol"]["id"] == protocol["id"], folder.name


def test_port_contracts_well_formed() -> None:
    for folder in NETWORK_FOLDERS:
        protocol = _load(folder, "plugin.protocol.json")
        for capability in protocol["capabilities"]:
            for port in [*capability["inputs"], *capability["outputs"]]:
                assert port["contract_id"] and port["schema_ref"], (folder.name, port)
                assert port["classification"] == "audit_confidential", (folder.name, port)
                assert port["version"].count(".") == 2, (folder.name, port)


def test_manifest_matches_protocol_binding_fields() -> None:
    for folder in NETWORK_FOLDERS:
        protocol = _load(folder, "plugin.protocol.json")
        manifest = _load(folder, "plugin.manifest.json")
        assert manifest["capabilities"] == [protocol["id"]], folder.name
        assert manifest["side_effects"] == "read_only", folder.name
        assert manifest["data_classification"] == "audit_confidential", folder.name
        assert manifest["permissions"]["network"] == "none", folder.name
        assert manifest["permissions"]["data_write"] == [], folder.name
