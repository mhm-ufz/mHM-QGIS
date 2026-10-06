"""Tests for the plugin-owned domain layout."""
from __future__ import annotations

from pathlib import Path

from mhm_qgis.core.handlers.store.layout import (
    domain_dem_path,
    ensure_project_structure,
    lai_folder,
    morph_folder,
    streamflow_observation_folder,
)
from mhm_qgis.core.handlers.store.paths import (
    data_folder,
    meteo_folder,
    output_folder,
    portable_path,
    project_path,
    restart_folder,
    static_folder,
    temp_folder,
    temp_morphology_folder,
    workspace_folder,
)


def test_all_generated_paths_are_inside_the_domain(tmp_path: Path) -> None:
    domain = tmp_path / "mhm-qgis" / "basin"

    assert Path(workspace_folder(domain)) == domain
    assert Path(temp_folder(domain)) == domain / ".temp"
    assert Path(temp_morphology_folder(domain)) == domain / ".temp" / "Morphology"
    assert Path(data_folder(domain)) == domain / "data"
    assert Path(static_folder(domain)) == domain / "data" / "static"
    assert Path(morph_folder(domain)) == domain / "data" / "static" / "morph"
    assert Path(meteo_folder(domain)) == domain / "data" / "meteo"
    assert Path(lai_folder(domain)) == domain / "data" / "lai"
    assert (
        Path(streamflow_observation_folder(domain))
        == domain / "data" / "observation" / "streamflow"
    )
    assert Path(output_folder(domain)) == domain / "output"
    assert Path(restart_folder(domain)) == domain / "restart"
    assert Path(domain_dem_path(domain, "001")) == domain / "data" / "static" / "morph" / "dem.asc"


def test_ensure_project_structure_stays_inside_the_domain(tmp_path: Path) -> None:
    domain = tmp_path / "mhm-qgis" / "basin"

    ensure_project_structure(domain, "5.13")

    assert (domain / ".temp" / "Morphology" / "morph").is_dir()
    assert (domain / ".temp" / "Morphology" / "lai").is_dir()
    assert (domain / "data" / "static" / "morph").is_dir()
    assert (domain / "data" / "meteo").is_dir()
    assert (domain / "data" / "lai").is_dir()
    assert (domain / "output").is_dir()
    assert (domain / "restart").is_dir()
    assert sorted(path.name for path in (tmp_path / "mhm-qgis").iterdir()) == ["basin"]


def test_ensure_project_structure_honours_the_requested_version(
    tmp_path: Path,
) -> None:
    """The caller's version decides the tree, not the saved state.

    A new project has no input state yet, so reading the version back always
    answered v5.13 -- and asking for 6.0 still built the v5.13 folders.
    """
    domain = tmp_path / "mhm-qgis" / "basin"

    ensure_project_structure(domain, "6.0")

    data = domain / "data"
    assert (data / "morph").is_dir()
    assert (data / "gauge" / "streamflow").is_dir()
    # v6 keeps morphology in one input.nc, so these v5.13 folders are not made.
    assert not (data / "static" / "morph").exists()
    assert not (data / "observation").exists()


def test_generated_path_is_relative_to_outer_project(tmp_path: Path) -> None:
    domain = tmp_path / "mhm-qgis" / "basin"
    output = Path(morph_folder(domain)) / "dem.asc"

    saved = portable_path(tmp_path, output)
    assert saved == "mhm-qgis/basin/data/static/morph/dem.asc"
    assert project_path(tmp_path, saved) == str(output)
    outside = tmp_path.parent / "elsewhere.tif"
    assert portable_path(tmp_path, outside) == str(outside)
    assert project_path(tmp_path, str(outside)) == str(outside)
