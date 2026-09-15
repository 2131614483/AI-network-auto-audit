"""Layout invariants for the verified built-in plugin directory.

Every verified built-in plugin is one directory, described by a single
convention spelled out in :mod:`packages.plugin_runtime.layout`:

    plugin id            audit.evidence-lineage
    directory            plugins/builtin/audit_evidence_lineage/   ('.' '-' -> '_')
    module / entrypoint  plugins.builtin.audit_evidence_lineage.runtime:handle

These tests are the gate that keeps the directory from drifting away from that
convention.  They exist because the convention *has* drifted before: the
descriptors and the runtime used to live in two separate hand-maintained
directories, so a plugin could be half-added (descriptor on disk, no runtime, or
an id that no allow-list entry pointed at) and nothing noticed until a run
failed deep in an isolated child.

The last test is the load-bearing one — it fails when a plugin directory is
present on disk but not in the allow list, or listed but absent.  Adding a
plugin therefore requires a deliberate allow-list edit, which is also what keeps
the allow list *code* rather than directory discovery (``runner``: "never
resolve an untrusted entrypoint").
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from packages.plugin_runtime.layout import (
    BUILTIN_ROOT,
    VERIFIED_PLUGIN_IDS,
    declaration_dir,
    entrypoint,
    module_name,
    runtime_dir,
    runtime_module,
)
from packages.plugin_runtime.runner import load_verified_binding

IDS = sorted(VERIFIED_PLUGIN_IDS)


def _declaration_dirs() -> list[Path]:
    return sorted(
        d for d in BUILTIN_ROOT.iterdir()
        if d.is_dir() and (d / "plugin.protocol.json").exists()
    )


def test_allow_list_is_not_empty() -> None:
    assert len(IDS) >= 100


def test_allow_list_has_no_duplicates() -> None:
    assert len(IDS) == len(set(IDS))


def test_module_names_are_unique() -> None:
    """Two ids must never collapse onto one runtime package.

    ``a.b-c`` and ``a-b.c`` both map to ``a_b_c``; if that ever happens one
    plugin would silently shadow the other's ``runtime.py``.
    """
    names = [module_name(plugin_id) for plugin_id in IDS]
    duplicated = sorted({n for n in names if names.count(n) > 1})
    assert not duplicated, f"plugin ids collide on one module name: {duplicated}"


@pytest.mark.parametrize("plugin_id", IDS)
def test_every_allow_listed_plugin_has_all_three_descriptors_and_a_runtime(plugin_id: str) -> None:
    declaration = declaration_dir(plugin_id)
    for name in ("plugin.protocol.json", "plugin.manifest.json", "plugin.runtime-binding.json"):
        assert (declaration / name).is_file(), f"{plugin_id}: missing {name}"
    assert (runtime_dir(plugin_id) / "runtime.py").is_file(), f"{plugin_id}: missing runtime.py"


@pytest.mark.parametrize("plugin_id", IDS)
def test_directory_name_and_manifest_entrypoint_follow_the_convention(plugin_id: str) -> None:
    declaration = declaration_dir(plugin_id)
    # One plugin is one directory, named after the module (`.`, `-` -> `_`).
    assert declaration.name == module_name(plugin_id)
    assert declaration == runtime_dir(plugin_id)

    protocol = json.loads((declaration / "plugin.protocol.json").read_text(encoding="utf-8"))
    assert protocol["id"] == plugin_id

    manifest = json.loads((declaration / "plugin.manifest.json").read_text(encoding="utf-8"))
    assert manifest["entrypoint"] == entrypoint(plugin_id)
    assert manifest["id"] == plugin_id


@pytest.mark.parametrize("plugin_id", IDS)
def test_binding_verifies_and_resolves_to_the_derived_module(plugin_id: str) -> None:
    binding = load_verified_binding(plugin_id)
    assert binding.module == runtime_module(plugin_id)
    assert binding.entrypoint == entrypoint(plugin_id)


def test_on_disk_declaration_dirs_match_the_allow_list_exactly() -> None:
    """Drift detector: the allow list and the directory are the same set.

    A plugin directory that appears on disk but is not allow-listed (or an
    allow-listed id with no directory) fails here, at review time, instead of
    during an isolated run.
    """
    on_disk = {d.name for d in _declaration_dirs()}
    allow_list = {module_name(plugin_id) for plugin_id in IDS}
    assert on_disk == allow_list, (
        f"on disk but not allow-listed: {sorted(on_disk - allow_list)}; "
        f"allow-listed but not on disk: {sorted(allow_list - on_disk)}"
    )
