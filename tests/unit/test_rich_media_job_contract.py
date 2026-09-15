from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from jsonschema import Draft202012Validator


def test_rich_media_job_contract_accepts_only_safe_queue_envelope() -> None:
    schema_path = Path(__file__).resolve().parents[2] / "contracts" / "jsonschema" / "rich-media-job.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    payload = {
        "job_id": str(uuid4()),
        "ingest_file_id": str(uuid4()),
        "status": "queued",
        "attempt_count": 0,
        "method": "txt",
    }
    assert not list(Draft202012Validator(schema).iter_errors(payload))
    payload["command"] = "untrusted.exe"
    assert list(Draft202012Validator(schema).iter_errors(payload))
