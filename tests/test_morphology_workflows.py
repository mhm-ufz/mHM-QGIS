"""Focused tests for the public morphology execution plans."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from mhm_qgis.core.executions import morphology
from mhm_qgis.core.executions.morphology import setup
from mhm_qgis.core.handlers.state import processing
from mhm_qgis.core.handlers.store.layout import input_state_path


HEADERS = {
    level: {
        "ncols": 6,
        "nrows": 4,
        "xllcorner": 0.0,
        "yllcorner": 0.0,
        "cellsize": cellsize,
    }
    for level, cellsize in (("L0", 1.0), ("L1", 2.0), ("L11", 2.0), ("L2", 3.0))
}


def _request(tmp_path, workflow="execute_all_morphology"):
    return setup.SetupRequest(
        project_folder=str(tmp_path), headers=HEADERS, workflow=workflow
    )


def _recording_commands(monkeypatch, *, fail=""):
    calls = []

    def command(name):
        def run(_request, *, log=None, task=None):
            calls.append(name)
            if name == fail:
                raise RuntimeError(f"{name} failed")
            return ()

        return run

    monkeypatch.setattr(
        setup,
        "_COMMANDS",
        {stage.command: command(stage.command) for stage in morphology.PUBLICATION_STAGES},
    )
    return calls


def test_combined_setup_runs_the_public_plan_in_order(tmp_path, monkeypatch):
    calls = _recording_commands(monkeypatch)
    messages = []

    assert setup.run(_request(tmp_path), log=messages.append)

    assert calls == ["publish"]
    assert processing.workflow(tmp_path, "execute_all_morphology")["status"] == "completed"


def test_each_setup_command_records_its_own_completed_status(tmp_path, monkeypatch):
    _recording_commands(monkeypatch)

    request = _request(tmp_path)
    setup.run(request)

    workflows = processing.section(tmp_path, "workflows", {})
    expected = [
        f"{request.workflow}_{stage.command}"
        for stage in morphology.PUBLICATION_STAGES
    ]
    assert [name for name in expected if name in workflows] == expected
    assert all(workflows[name]["status"] == "completed" for name in expected)


def test_combined_setup_records_a_publication_failure(tmp_path, monkeypatch):
    calls = _recording_commands(monkeypatch, fail="publish")

    with pytest.raises(RuntimeError, match="publish failed"):
        setup.run(_request(tmp_path))

    assert calls == ["publish"]
    workflow = processing.workflow(tmp_path, "execute_all_morphology")
    assert workflow["status"] == "failed"
    assert "publish failed" in workflow["message"]
    assert processing.workflow(tmp_path, "execute_all_morphology_publish")["status"] == "failed"


def test_v6_publication_does_not_recreate_latlon(tmp_path, monkeypatch):
    path = Path(input_state_path(tmp_path))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"mhm_version": "6"}), encoding="utf-8")
    calls = _recording_commands(monkeypatch)

    setup.run(_request(tmp_path))

    assert calls == ["publish"]


def test_execute_all_plan_contains_every_user_visible_command():
    stages = morphology.EXECUTE_ALL_STAGES
    assert [stage.command for stage in stages] == [
        "dem_derivatives",
        "gauge_position",
        "morph_mask",
        "categorical",
        "categorical",
        "categorical",
        "lai",
        "publication",
    ]
    assert [stage.args for stage in stages if stage.command == "categorical"] == [
        ("lc",),
        ("soil",),
        ("geology",),
    ]


def test_only_the_dem_comes_from_the_masked_bundle(tmp_path):
    """Watershed-masking every derivative would carve nodata out of them."""
    import numpy as np
    from osgeo import gdal, osr
    from mhm_qgis.core.handlers.file.netcdf.raster import copy_rasters, source

    def raster(name, value):
        path = tmp_path / f"{name}.tif"
        dataset = gdal.GetDriverByName("GTiff").Create(str(path), 4, 3, 1, gdal.GDT_Float32)
        dataset.SetGeoTransform((0.0, 1.0, 0, 3.0, 0, -1.0))
        crs = osr.SpatialReference()
        crs.ImportFromEPSG(32632)
        dataset.SetProjection(crs.ExportToWkt())
        dataset.GetRasterBand(1).WriteArray(np.full((3, 4), value, dtype="float32"))
        dataset.GetRasterBand(1).SetNoDataValue(-9999)
        dataset = None
        return path

    copy_rasters(tmp_path / "dem_derivatives.nc", {
        "dem_filled": raster("dem_filled", 1.0),
        "slope": raster("slope", 2.0),
        "facc": raster("facc", 3.0),
    })
    copy_rasters(tmp_path / "dem_derivatives_masked.nc", {"dem": raster("dem", 4.0)})

    layers = setup._model_sources(tmp_path)

    assert layers["dem"] == source(tmp_path / "dem_derivatives_masked.nc", "dem")
    assert layers["slope"] == source(tmp_path / "dem_derivatives.nc", "slope")
    assert layers["facc"] == source(tmp_path / "dem_derivatives.nc", "facc")
