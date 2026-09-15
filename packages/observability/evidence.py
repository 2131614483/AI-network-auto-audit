"""CW6: verifiable evidence export — one run's full chain in a sealed bundle.

A run's evidence must be exportable so an auditor can re-verify it later
without the live database: plan/nodes/edges, persisted attempts, log segments,
artifact references, and a manifest with a sha256 per item.  ``verify``
recomputes every hash and reports any gap — a bundle that does not verify is
not evidence of success.
"""

from __future__ import annotations

import hashlib
import json
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_MANIFEST_NAME = "manifest.json"


@dataclass(frozen=True, slots=True)
class EvidenceManifest:
    run_id: str
    plan_key: str
    trace_id: str
    exported_at: str
    items: tuple[dict[str, Any], ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "plan_key": self.plan_key,
            "trace_id": self.trace_id,
            "exported_at": self.exported_at,
            "items": list(self.items),
        }


@dataclass(frozen=True, slots=True)
class EvidenceVerification:
    ok: bool
    checked: int = 0
    missing: tuple[str, ...] = ()
    mismatched: tuple[str, ...] = ()
    extra: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "checked": self.checked,
            "missing": list(self.missing),
            "mismatched": list(self.mismatched),
            "extra": list(self.extra),
        }


def _sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def export_evidence_bundle(
    out_path: str | Path,
    *,
    run_id: str,
    plan_key: str,
    trace_id: str,
    plan: dict[str, Any],
    attempts: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    log_segments: list[tuple[str, str]],  # (segment_jsonl_or_gz_path, logical_name)
    artifacts: list[dict[str, Any]],  # {path, media_type, sha256, size_bytes}
) -> EvidenceManifest:
    """Write ``out_path`` as a zip with manifest; return the manifest."""
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    items: list[dict[str, Any]] = []

    def _add_memory(name: str, payload: str) -> None:
        raw = payload.encode("utf-8")
        items.append({"name": name, "sha256": _sha256_bytes(raw), "size_bytes": len(raw)})

    plan_payload = json.dumps({"run_id": run_id, "plan_key": plan_key, "plan": plan}, ensure_ascii=False, sort_keys=True)
    attempts_payload = json.dumps(attempts, ensure_ascii=False, sort_keys=True)
    edges_payload = json.dumps(edges, ensure_ascii=False, sort_keys=True)
    _add_memory("plan.json", plan_payload)
    _add_memory("attempts.json", attempts_payload)
    _add_memory("edges.json", edges_payload)

    for artifact in artifacts:
        path = artifact.get("path")
        if not path or not Path(path).is_file():
            items.append({
                "name": f"artifacts/{artifact.get('name') or Path(str(path or '?')).name}",
                "sha256": str(artifact.get("sha256") or ""),
                "size_bytes": int(artifact.get("size_bytes") or 0),
                "media_type": str(artifact.get("media_type") or ""),
                "missing": True,
            })
            continue
        raw = Path(path).read_bytes()
        items.append({
            "name": f"artifacts/{artifact.get('name') or Path(path).name}",
            "sha256": _sha256_bytes(raw),
            "size_bytes": len(raw),
            "media_type": str(artifact.get("media_type") or ""),
        })

    for segment_path, logical_name in log_segments:
        if not Path(segment_path).is_file():
            items.append({"name": f"logs/{logical_name}", "sha256": "", "size_bytes": 0, "missing": True})
            continue
        raw = Path(segment_path).read_bytes()
        items.append({
            "name": f"logs/{logical_name}",
            "sha256": _sha256_bytes(raw),
            "size_bytes": len(raw),
        })

    manifest = EvidenceManifest(
        run_id=run_id,
        plan_key=plan_key,
        trace_id=trace_id,
        exported_at=datetime.now(timezone.utc).isoformat(),
        items=tuple(items),
    )
    manifest_raw = json.dumps(manifest.as_dict(), ensure_ascii=False, sort_keys=True).encode("utf-8")

    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr("plan.json", plan_payload)
        bundle.writestr("attempts.json", attempts_payload)
        bundle.writestr("edges.json", edges_payload)
        for artifact in artifacts:
            path = artifact.get("path")
            if path and Path(path).is_file():
                bundle.write(Path(path), f"artifacts/{artifact.get('name') or Path(path).name}")
        for segment_path, logical_name in log_segments:
            if Path(segment_path).is_file():
                bundle.write(Path(segment_path), f"logs/{logical_name}")
        bundle.writestr(_MANIFEST_NAME, manifest_raw)
    return manifest


def verify_evidence_bundle(bundle_path: str | Path) -> EvidenceVerification:
    """Recompute every manifest sha inside the zip; report missing/mismatched."""
    bundle = Path(bundle_path)
    if not bundle.is_file():
        return EvidenceVerification(ok=False)
    with zipfile.ZipFile(bundle, "r") as archive:
        names = set(archive.namelist())
        if _MANIFEST_NAME not in names:
            return EvidenceVerification(ok=False)
        try:
            manifest = json.loads(archive.read(_MANIFEST_NAME).decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return EvidenceVerification(ok=False)
        items = manifest.get("items") or []
        missing: list[str] = []
        mismatched: list[str] = []
        checked = 0
        for item in items:
            name = item.get("name")
            if not name:
                continue
            if name not in names:
                missing.append(name)
                continue
            raw = archive.read(name)
            if _sha256_bytes(raw) != item.get("sha256"):
                mismatched.append(name)
                continue
            checked += 1
        # Membership cross-check: a manifest that merely *omits* an entry would
        # otherwise launder a tampered member past the per-entry hashes — the
        # hashes are only as complete as the list of things they cover.  Every
        # archived member must therefore be declared, and vice versa.
        declared = {item["name"] for item in items if item.get("name")}
        extra = tuple(sorted(names - declared - {_MANIFEST_NAME}))
        return EvidenceVerification(
            ok=not missing and not mismatched and not extra,
            checked=checked,
            missing=tuple(missing),
            mismatched=tuple(mismatched),
            extra=extra,
        )
