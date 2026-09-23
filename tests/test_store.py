"""Tests for the file-and-folder store."""

from __future__ import annotations

from pathlib import Path

import pytest

from mhm_qgis.core.handlers.file import json as jsonio
from mhm_qgis.core.handlers.store import layout, paths, registry


@pytest.fixture
def project(tmp_path):
    (tmp_path / "mhm-qgis").mkdir()
    return tmp_path


# --- paths: the leaf that state imports ---

def test_paths_is_a_leaf_with_no_intra_package_imports():
    """state imports only this module, so it must not reach back into state."""
    source = Path(paths.__file__).read_text(encoding="utf-8")
    assert "from ." not in source
    assert "import mhm_qgis" not in source


def test_plugin_root_is_the_package_not_this_directory():
    root = Path(paths.plugin_root())
    assert root.name == "mhm_qgis"
    assert (root / "metadata.txt").is_file()


# --- layout: the version branch ---

def test_the_version_is_read_from_the_saved_input_state(project):
    assert layout.project_version(project) == "v5.13"      # no state yet

    jsonio.write(Path(layout.input_state_path(project)), {"mhm_version": "6.0"})
    layout.forget_project_version(project)
    assert layout.project_version(project) == "v6"


def test_an_unreadable_state_falls_back_rather_than_raising(project):
    path = Path(layout.input_state_path(project))
    path.parent.mkdir(parents=True)
    path.write_text("{ not json", encoding="utf-8")
    layout.forget_project_version(project)
    assert layout.project_version(project) == "v5.13"


def test_the_version_cache_does_not_outlive_a_rewrite(project):
    state = Path(layout.input_state_path(project))
    jsonio.write(state, {"mhm_version": "5.13"})
    layout.forget_project_version(project)
    assert layout.morph_folder(project).endswith("static/morph")

    jsonio.write(state, {"mhm_version": "6.0"})
    layout.forget_project_version(project)
    assert layout.morph_folder(project).endswith("data/morph")


def test_an_explicit_version_overrides_the_saved_one(project):
    jsonio.write(Path(layout.input_state_path(project)), {"mhm_version": "5.13"})
    layout.forget_project_version(project)
    # This is what ensure_project_structure needs: a new project has no state.
    assert layout.morph_folder(project, version="v6").endswith("data/morph")
    assert layout.lai_folder(project, version="v6").endswith("data/morph")


# --- registry: what exists ---

def test_the_key_is_workspace_relative_so_a_project_stays_portable(project):
    key = registry.key_for(project, Path(paths.temp_morphology_folder(project)) / "dem.tif")
    assert key == ".temp/Morphology/dem.tif"


def test_the_most_processed_variant_wins(project):
    geometry = Path(paths.temp_morphology_folder(project))
    geometry.mkdir(parents=True)
    raw = geometry / "1_dem_filled.tif"
    for name in ("1_dem_filled.tif", "1_dem_filled_crop.tif"):
        (geometry / name).write_bytes(b"")

    assert Path(registry.preferred(raw)).name == "1_dem_filled_crop.tif"
    (geometry / "1_dem_filled_masked.tif").write_bytes(b"")
    assert Path(registry.preferred(raw)).name == "1_dem_filled_masked.tif"


def test_an_already_masked_path_is_left_alone(project):
    geometry = Path(paths.temp_morphology_folder(project))
    geometry.mkdir(parents=True)
    masked = geometry / "dem_masked.tif"
    masked.write_bytes(b"")
    assert registry.preferred(masked) == str(masked)


def test_the_filesystem_beats_the_journal(project):
    output = project / "dem.tif"
    output.write_bytes(b"")
    registry.register(project, output, name="dem.tif")
    assert registry.available(project, output) is True

    # A user clearing the folder must be noticed, whatever the journal says.
    output.unlink()
    assert registry.available(project, output) is False
    assert registry.registered(project)[registry.key_for(project, output)][
        "exists"
    ] is False

    output.write_bytes(b"")
    assert registry.available(project, output) is True
    assert registry.registered(project)[registry.key_for(project, output)][
        "exists"
    ] is True


def test_a_file_produced_outside_the_plugin_is_adopted(project):
    output = project / "appeared.tif"
    output.write_bytes(b"")
    assert not registry.registered(project)

    assert registry.available(project, output) is True
    assert registry.key_for(project, output) in registry.registered(project)


def test_an_absent_file_is_never_recorded(project):
    assert registry.register(project, project / "nope.tif") is None
    assert not registry.registered(project)


def test_outputs_are_listed_newest_first(project):
    import os
    import time

    for name, age in (("old.nc", 100), ("new.nc", 0)):
        p = project / name
        p.write_bytes(b"")
        os.utime(p, (time.time() - age, time.time() - age))
    assert [p.name for p in registry.outputs_in(project)] == ["new.nc", "old.nc"]
