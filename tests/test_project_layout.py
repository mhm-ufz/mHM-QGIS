"""Tests for the plugin-owned project workspace layout."""
from __future__ import annotations

from pathlib import Path

from mhm_qgis.core.handlers.store.paths import data_folder, data_raw_folder, domain_data_folder, geometry_folder, master_data_folder, meteo_folder, output_folder, raw_meteo_folder, relative_project_path, restart_folder, static_folder, workspace_folder, z_temp_folder
from mhm_qgis.core.handlers.store.layout import domain_dem_path, ensure_project_structure, lai_folder, morph_folder, streamflow_observation_folder


def test_all_generated_paths_are_inside_plugin_workspace(tmp_path: Path) -> None:
    project = tmp_path / "project"
    workspace = project / "mhm-qgis"

    assert Path(workspace_folder(project)) == workspace
    assert Path(z_temp_folder(project)) == workspace / "Z Temp"
    assert Path(geometry_folder(project)) == workspace / "Z Temp" / "Geometry"
    assert Path(data_folder(project)) == workspace / "data"
    assert Path(master_data_folder(project)) == workspace / "data" / "master"
    assert Path(data_raw_folder(project)) == workspace / "data_raw"
    assert Path(static_folder(project)) == workspace / "data" / "master" / "static"
    assert Path(morph_folder(project)) == workspace / "data" / "master" / "static" / "morph"
    assert Path(meteo_folder(project)) == workspace / "data" / "master" / "meteo"
    assert Path(raw_meteo_folder(project)) == workspace / "data_raw" / "meteo"
    assert Path(lai_folder(project)) == workspace / "data" / "master" / "lai"
    assert (
        Path(streamflow_observation_folder(project))
        == workspace / "data" / "master" / "observation" / "streamflow"
    )
    assert Path(output_folder(project)) == workspace / "output"
    assert Path(restart_folder(project)) == workspace / "restart"
    assert Path(domain_data_folder(project, "001")) == workspace / "data" / "001"
    assert Path(domain_dem_path(project, "001")) == workspace / "data" / "master" / "static" / "morph" / "dem.asc"


def test_domain_folder_rejects_path_traversal(tmp_path: Path) -> None:
    import pytest

    for value in ("", ".", "..", "../outside", "a/b", "a\\b"):
        with pytest.raises(ValueError, match="Outlet ID"):
            domain_data_folder(tmp_path, value)


def test_ensure_project_structure_does_not_create_outputs_at_outer_root(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"

    ensure_project_structure(project, "5.13")

    workspace = project / "mhm-qgis"
    assert (workspace / "Z Temp" / "Geometry").is_dir()
    assert (workspace / "data" / "master" / "static" / "morph").is_dir()
    assert (workspace / "data" / "master" / "meteo").is_dir()
    assert (workspace / "data" / "master" / "lai").is_dir()
    assert (workspace / "output").is_dir()
    assert (workspace / "restart").is_dir()
    for name in ("Z Temp", "data", "data_raw", "output", "restart"):
        assert not (project / name).exists()


def test_ensure_project_structure_honours_the_requested_version(
    tmp_path: Path,
) -> None:
    """The caller's version decides the tree, not the saved state.

    A new project has no input state yet, so reading the version back always
    answered v5.13 -- and asking for 6.0 still built the v5.13 folders.
    """
    project = tmp_path / "project"

    ensure_project_structure(project, "6.0")

    master = project / "mhm-qgis" / "data" / "master"
    assert (master / "morph").is_dir()
    assert (master / "gauge" / "streamflow").is_dir()
    # v6 keeps morphology in one input.nc, so these v5.13 folders are not made.
    assert not (master / "static" / "morph").exists()
    assert not (master / "observation").exists()


def test_generated_path_is_relative_to_outer_project(tmp_path: Path) -> None:
    project = tmp_path / "project"
    output = Path(morph_folder(project)) / "dem.asc"

    assert (
        relative_project_path(project, output)
        == "mhm-qgis/data/master/static/morph/dem.asc"
    )
