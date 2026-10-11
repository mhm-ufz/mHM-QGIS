"""v6 uses a different input layout; v5.13 must be untouched by it."""
from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")



from mhm_qgis.core.handlers.store.layout import (
    domain_dem_path,
    expected_meteo_outputs,
    input_state_path,
    lai_folder,
    meteo_mask_path,
    morph_folder,
    project_version,
    streamflow_observation_folder,
)
from mhm_qgis.core.handlers.store.paths import workspace_folder

xr = pytest.importorskip("xarray")


def _project(tmp_path, version):
    workspace = Path(workspace_folder(tmp_path))
    workspace.mkdir(parents=True, exist_ok=True)
    state = Path(input_state_path(tmp_path))
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(json.dumps({"mhm_version": version}), encoding="utf-8")
    return tmp_path


def _relative(project, path):
    return str(Path(path).relative_to(workspace_folder(project)))


def test_v6_places_every_input_where_the_template_expects_it(tmp_path):
    project = _project(tmp_path, "6.0")

    assert project_version(project) == "v6"
    assert _relative(project, morph_folder(project)) == "data/morph"
    assert _relative(project, lai_folder(project)) == "data/morph"
    assert _relative(project, streamflow_observation_folder(project)) == (
        "data/gauge/streamflow")
    assert _relative(project, domain_dem_path(project, "280")) == "data/morph/dem.nc"


def test_v5_13_layout_is_unchanged(tmp_path):
    project = _project(tmp_path, "5.13")

    assert project_version(project) == "v5.13"
    assert _relative(project, morph_folder(project)) == "data/static/morph"
    assert _relative(project, lai_folder(project)) == "data/lai"
    assert _relative(project, streamflow_observation_folder(project)) == (
        "data/observation/streamflow")
    assert _relative(project, domain_dem_path(project, "280")) == "data/static/morph/dem.asc"


def test_an_unknown_or_missing_version_falls_back_to_v5_13(tmp_path):
    # No state file at all: every existing project already uses this layout.
    assert project_version(tmp_path) == "v5.13"
    broken = _project(tmp_path / "broken", "5.13")
    Path(input_state_path(broken)).write_text("{ not json", encoding="utf-8")
    assert project_version(broken) == "v5.13"


def test_v6_meteo_is_flat_and_has_no_header(tmp_path):
    project = _project(tmp_path, "6.0")
    outputs = expected_meteo_outputs(project)

    assert _relative(project, outputs["pre"]["netcdf"]) == "data/meteo/pre.nc"
    assert outputs["pre"]["header"] is None
    assert _relative(project, meteo_mask_path(project)) == "data/meteo/mask.nc"


def test_v5_13_meteo_keeps_its_folders_and_headers(tmp_path):
    project = _project(tmp_path, "5.13")
    outputs = expected_meteo_outputs(project)

    assert _relative(project, outputs["pre"]["netcdf"]) == (
        "data/meteo/pre/pre.nc")
    assert _relative(project, outputs["pre"]["header"]) == (
        "data/meteo/pre/header.txt")


def test_the_meteo_mask_matches_the_example_format(tmp_path):
    from mhm_qgis.core.grid import TargetGrid
    from mhm_qgis.core.meteorology.mask import write_meteo_mask

    header = {
        "ncols": 4, "nrows": 3, "xllcorner": 100.0, "yllcorner": 200.0,
        "cellsize": 10.0,
    }
    grid = TargetGrid(lon=np.arange(4), lat=np.arange(3), header=header)
    output = write_meteo_mask(tmp_path / "mask.nc", grid)

    # mask_and_scale would upcast to float when applying _FillValue; the file
    # itself stores int32, which is what the mHM example has.
    with xr.open_dataset(output, mask_and_scale=False) as result:
        assert result["mask"].dims == ("y", "x")
        assert result["mask"].shape == (3, 4)
        assert result["mask"].dtype == np.int32
        assert np.all(result["mask"].values == 1)
        np.testing.assert_allclose(
            result["x"].values, [105.0, 115.0, 125.0, 135.0])
        np.testing.assert_allclose(result["y"].values, [225.0, 215.0, 205.0])
        assert result["mask"].attrs["standard_name"] == "land_binary_mask"


def test_the_meteo_mask_honours_an_explicit_valid_array(tmp_path):
    from mhm_qgis.core.grid import TargetGrid
    from mhm_qgis.core.meteorology.mask import write_meteo_mask

    header = {
        "ncols": 2, "nrows": 2, "xllcorner": 0.0, "yllcorner": 0.0,
        "cellsize": 1.0,
    }
    grid = TargetGrid(lon=np.arange(2), lat=np.arange(2), header=header)
    valid = np.array([[True, False], [False, True]])
    output = write_meteo_mask(tmp_path / "mask.nc", grid, valid=valid)

    with xr.open_dataset(output) as result:
        np.testing.assert_array_equal(result["mask"].values, [[1, 0], [0, 1]])

    with pytest.raises(ValueError, match="expected"):
        write_meteo_mask(tmp_path / "bad.nc", grid, valid=np.ones((3, 3)))


def test_the_v6_namelist_points_at_the_new_locations(tmp_path):
    from mhm_qgis.vSpecific import build_initial_values

    class _Dialog:
        project_folder = str(tmp_path)

        def current_l1_resolution(self):
            return 1000

        def current_l11_resolution(self):
            return 2000

        def current_grid_unit(self):
            return "m"

    values = build_initial_values("6.0", _Dialog())["main"]
    # Input paths come only from what the setup prepared; nothing is registered here,
    # so the editor gets no input path (only outputs and restarts).
    assert values["config_input"] == {}
    assert {key for key in values["config_mpr"] if not key.startswith("restart")} == set()
    assert "scc_gauges_path" not in values["config_mrm"]

    from mhm_qgis.core.handlers.state.nml_settings import update_section

    _project(tmp_path, "6.0")
    update_section(tmp_path, "land_cover", {"output_path": "data/morph/lc.nc", "variable": "land_cover"})
    update_section(tmp_path, "geology", {"classdefinition_path": "data/morph/geology_classdefinition.txt"})
    update_section(tmp_path, "soil", {"classdefinition_path": "data/morph/soil_classdefinition.txt"})
    mpr = build_initial_values("6.0", _Dialog())["main"]["config_mpr"]
    assert mpr["land_cover_path"] == ["data/morph/lc.nc"]
    assert mpr["geo_lut_path"] == ["data/morph/geology_classdefinition.txt"]
    assert mpr["soil_lut_path"] == ["data/morph/soil_classdefinition.txt"]
    assert not {"lai_lut_path", "lai_path"} & set(mpr)  # no LAI prepared


def test_v6_ready_lai_points_at_the_morphology_bundle(tmp_path):
    from mhm_qgis.core.handlers.state.nml_settings import update_section
    from mhm_qgis.vSpecific import build_initial_values

    _project(tmp_path, "6.0")
    update_section(tmp_path, "lai", {
        "mode": "mhm_ready",
        "time_step": 0,
        "output_path": "data/morph/LAI_class.nc",
        "classdefinition_path": "data/morph/LAI_classdefinition.txt",
    })

    class _Dialog:
        project_folder = str(tmp_path)

        def current_l1_resolution(self):
            return 1000

        def current_l11_resolution(self):
            return 2000

        def current_grid_unit(self):
            return "m"

    main = build_initial_values("6.0", _Dialog())["main"]
    assert main["config_input"]["lai_class_path"] == [
        "data/morph/input.nc"
    ]
    assert main["config_input"]["lai_class_var"] == ["LAI_class"]
    assert main["config_mpr"]["lai_time_step"] == [0]
    assert main["config_mpr"]["lai_lut_path"] == [
        "data/morph/LAI_classdefinition.txt"
    ]
