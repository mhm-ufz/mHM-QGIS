"""Tests for resolving a finished mHM run into displayable rasters."""

from __future__ import annotations

from datetime import datetime

import numpy as np
import pytest

from mhm_qgis.core import post


def _project_with_output(root, *, offsets, time_units="days since 1990-01-01"):
    """Build a project whose mHM run left one output file behind."""
    from netCDF4 import Dataset

    folder = root / "output"
    folder.mkdir(parents=True, exist_ok=True)
    with Dataset(folder / "mHM_Fluxes_States.nc", "w") as ds:
        ds.createDimension("time", len(offsets))
        ds.createDimension("northing", 3)
        ds.createDimension("easting", 4)
        axis = ds.createVariable("time", "i4", ("time",))
        axis.units = time_units
        axis[:] = np.asarray(offsets, dtype="i4")
        yv = ds.createVariable("northing", "f8", ("northing",))
        yv.standard_name = "projection_y_coordinate"
        yv[:] = [2000.0, 1000.0, 0.0]
        xv = ds.createVariable("easting", "f8", ("easting",))
        xv.standard_name = "projection_x_coordinate"
        xv[:] = [0.0, 1000.0, 2000.0, 3000.0]
        for name, long_name in (("Q", "total runoff"), ("SM_L01", "soil moisture")):
            v = ds.createVariable(name, "f8", ("time", "northing", "easting"))
            v.long_name = long_name
            v.units = "mm"
            v[:] = np.zeros((len(offsets), 3, 4))
    return root


# Month starts through 1990, so the spacing is deliberately uneven.
MONTHLY = [0, 31, 59, 90, 120, 151]


@pytest.fixture
def project(tmp_path):
    return _project_with_output(tmp_path, offsets=MONTHLY)


def test_a_project_without_a_run_offers_nothing(tmp_path):
    assert post.output_source(tmp_path) is None
    assert post.available_variables(tmp_path) == []
    assert post.simulation_period(tmp_path) is None
    assert post.resolve_output(tmp_path, "Q", 0) is None


def test_variables_are_discovered_from_the_output(project):
    assert {v.name for v in post.available_variables(project)} == {"Q", "SM_L01"}
    assert post.find_variable(project, "Q").display_label == "total runoff [mm]"
    assert post.find_variable(project, "absent") is None


def test_the_period_comes_from_the_files_own_steps(project):
    assert post.simulation_period(project) == (
        datetime(1990, 1, 1),
        datetime(1990, 6, 1),
    )
    assert len(post.simulation_steps(project)) == 6


def test_a_moment_maps_to_the_step_at_or_before_it(project):
    steps = post.simulation_steps(project)
    assert post.step_for_when(steps, datetime(1990, 1, 1)) == 0
    # Mid-month falls back to the month that contains it, not the next one.
    assert post.step_for_when(steps, datetime(1990, 3, 15)) == 2
    assert post.step_for_when(steps, datetime(1990, 6, 1)) == 5


def test_moments_outside_the_run_clamp_to_its_ends(project):
    steps = post.simulation_steps(project)
    assert post.step_for_when(steps, datetime(1980, 1, 1)) == 0
    assert post.step_for_when(steps, datetime(2030, 1, 1)) == 5
    assert post.step_for_when([], datetime(1990, 1, 1)) == 0


def test_resolving_returns_a_placed_raster_for_the_right_moment(project):
    raster = post.resolve_output(project, "Q", 3, crs="EPSG:3035")
    assert raster.variable == "Q"
    assert raster.when == datetime(1990, 4, 1)
    assert raster.name == "total runoff (Q)"
    assert raster.data.dims == ("y", "x")
    assert raster.data.rio.crs.to_epsg() == 3035


def test_an_out_of_range_step_clamps_rather_than_failing(project):
    assert post.resolve_output(project, "Q", 99, crs="EPSG:3035").when == datetime(
        1990, 6, 1
    )
    assert post.resolve_output(project, "Q", -5, crs="EPSG:3035").when == datetime(
        1990, 1, 1
    )


def test_output_carries_no_crs_of_its_own(project):
    # This is why the caller must supply one; without it the raster is unplaced.
    raster = post.resolve_output(project, "Q", 0)
    assert raster.data.rio.crs is None
