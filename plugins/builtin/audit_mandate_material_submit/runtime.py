"""audit.mandate.material-submit: register the auditee's submitted materials.

Consumes a notice-accept payload (artifact-ref JSON) and emits a
submitted-material artifact-ref aggregating per-unit material lists. Material
contents are passed through as-is. Read-only.
"""
from __future__ import annotations

from typing import Any

from plugins.builtin._child_common import (
    InputRejected,
    allowed_roots,
    check_identity,
    child_main,
    read_artifact_json,
    read_verified_artifact,
)

PLUGIN_ID = "audit.mandate.material-submit"
CAPABILITY = "audit.mandate.material-submit"
MAX_UNITS = 10_000


def handle(envelope: dict[str, Any]) -> dict[str, Any]:
    check_identity(envelope, PLUGIN_ID, CAPABILITY)
    port = envelope.get("notice-accept")
    if not isinstance(port, dict) or not isinstance(port.get("artifact"), dict):
        raise InputRejected("notice-accept reference is missing")
    artifact = port["artifact"]
    roots = allowed_roots()
    read_verified_artifact(artifact, roots)
    payload = read_artifact_json(artifact, roots)
    body = payload.get("artifact")
    units = body.get("units") if isinstance(body, dict) else payload.get("units")
    if not isinstance(units, list):
        raise InputRejected("notice-accept requires units array")

    registered = []
    for index, unit in enumerate(units[:MAX_UNITS], start=1):
        if not isinstance(unit, dict):
            continue
        materials = unit.get("materials")
        if not isinstance(materials, list):
            continue
        registered.append({
            "unit_name": str(unit.get("unit_name") or f"单位{index}"),
            "material_count": len(materials),
            "materials": [str(m) for m in materials],
            "submitted_at": str(unit.get("submitted_at") or ""),
        })
    if not registered:
        raise InputRejected("notice-accept contains no submitted materials")

    return {
        "contract_id": "artifact-ref", "contract_version": "1.0.0",
        "artifact_id": f"material-bundle-{payload.get('project') or 'project'}",
        "tenant_id": str(payload.get("tenant_id") or "local-dev"),
        "media_type": "application/json",
        "sha256": str(artifact.get("sha256") or ""),
        "size_bytes": int(artifact.get("size_bytes") or 0),
        "uri": str(artifact.get("uri") or ""),
        "classification": "audit_confidential",
        "metadata": {"unit_count": len(registered), "units": registered},
    }


if __name__ == "__main__":
    raise SystemExit(child_main(handle))
