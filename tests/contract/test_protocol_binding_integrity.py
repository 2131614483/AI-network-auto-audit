"""Every verified binding must match its descriptor's raw bytes.

`plugin.runtime-binding.json` records a SHA-256 of the protocol file's *raw
bytes*, and `load_verified_binding` recomputes it at runtime:

    if binding["protocol"]["sha256"] != _sha256(protocol_path):
        raise PluginRuntimeError("protocol binding does not match its verified descriptor")

Two things make that easy to break silently, and both have already happened:

* editing a protocol without re-syncing the binding — the plugin looks fine
  until it is executed, then every node using it fails;
* line endings.  `Path.write_text` on Windows writes CRLF, which changes the
  bytes and therefore the hash, while `.gitattributes` pins `eol=lf` so a
  checkout re-materialises the file as LF — the same commit then hashes
  differently from what was recorded.  The repository documents an earlier
  incident where this failed 22 tests in CI.

These tests are cheap and catch both.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

PLUGINS = Path(__file__).resolve().parents[2] / "plugins" / "builtin"


def _plugin_dirs() -> list[Path]:
    return sorted(
        d for d in PLUGINS.iterdir()
        if d.is_dir() and (d / "plugin.protocol.json").exists()
        and (d / "plugin.runtime-binding.json").exists()
    )


def test_there_are_plugins_to_check() -> None:
    assert len(_plugin_dirs()) >= 100


@pytest.mark.parametrize("folder", _plugin_dirs(), ids=lambda d: d.name)
def test_binding_records_the_protocol_hash_it_will_be_checked_against(folder: Path) -> None:
    binding = json.loads((folder / "plugin.runtime-binding.json").read_text(encoding="utf-8"))
    recorded = (binding.get("protocol") or {}).get("sha256")
    if not recorded:
        pytest.skip("binding carries no protocol hash")
    actual = hashlib.sha256((folder / "plugin.protocol.json").read_bytes()).hexdigest()
    assert recorded == actual, (
        f"{folder.name}: the binding's recorded protocol hash is stale — re-sync it "
        "after editing the protocol, hashing the LF bytes"
    )


@pytest.mark.parametrize("folder", _plugin_dirs(), ids=lambda d: d.name)
def test_the_hashed_descriptor_is_lf_so_the_digest_is_platform_stable(folder: Path) -> None:
    """`.gitattributes` pins `eol=lf`; a CRLF working copy of the *protocol* would
    hash differently from what a Linux checkout (and CI) sees.

    Only the protocol is checked: it is the file whose bytes are hashed.  The
    binding's own line endings are irrelevant — nothing digests it — so pinning
    those would be 99 files of churn for no effect.
    """
    assert b"\r\n" not in (folder / "plugin.protocol.json").read_bytes(), (
        f"{folder.name}/plugin.protocol.json is CRLF; the recorded digest would then "
        "differ from a Linux checkout's"
    )
