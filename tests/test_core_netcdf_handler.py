"""Tests for reading gridded NetCDF without assuming how it was written."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import numpy as np
import pytest

from mhm_qgis.core.handlers.file import netcdf as nch

# Real mHM output, deliberately different from each other in every way that
# a naive reader would get wrong.
DAILY_YX = Path("/home/bashyal/Codes/mhm/examples/domain_01/output/mhm_output.nc")
MONTHLY_NE = Path(
    "/home/bashyal/Works/mhm-debug/test_domain/output_b1/mHM_Fluxes_States.nc"
)


def _write(path, *, y_name, x_name, time_units, offsets, var="Q"):
    """Write a 3-D grid with the given dimension names and time encoding."""
    from netCDF4 import Dataset

    with Dataset(path, "w") as ds:
        ds.createDimension("time", len(offsets))
        ds.createDimension(y_name, 3)
        ds.createDimension(x_name, 4)
        axis = ds.createVariable("time", "i4", ("time",))
        axis.units = time_units
        axis[:] = np.asarray(offsets, dtype="i4")
        yv = ds.createVariable(y_name, "f8", (y_name,))
        yv.standard_name = "projection_y_coordinate"
        yv[:] = [2000.0, 1000.0, 0.0]
        xv = ds.createVariable(x_name, "f8", (x_name,))
        xv.standard_name = "projection_x_coordinate"
        xv[:] = [0.0, 1000.0, 2000.0, 3000.0]
        data = ds.createVariable(var, "f8", ("time", y_name, x_name))
        data.long_name = "total runoff"
        data.units = "mm"
        data[:] = np.zeros((len(offsets), 3, 4))
    return path


def test_spatial_dims_are_discovered_not_assumed(tmp_path):
    yx = _write(tmp_path / "yx.nc", y_name="y", x_name="x",
                time_units="days since 1990-01-01", offsets=[0, 1, 2])
    ne = _write(tmp_path / "ne.nc", y_name="northing", x_name="easting",
                time_units="hours since 1990-01-01", offsets=[0, 24, 48])

    assert [(v.y_dim, v.x_dim) for v in nch.grid_variables(yx)] == [("y", "x")]
    assert [(v.y_dim, v.x_dim) for v in nch.grid_variables(ne)] == [
        ("northing", "easting")
    ]


def test_the_time_axis_uses_the_files_own_units(tmp_path):
    days = _write(tmp_path / "d.nc", y_name="y", x_name="x",
                  time_units="days since 1990-01-01", offsets=[0, 1])
    hours = _write(tmp_path / "h.nc", y_name="y", x_name="x",
                   time_units="hours since 1990-01-01", offsets=[0, 24])

    # Same raw offsets would decode differently; both must land on the same days.
    assert nch.time_axis(days)[:2] == [datetime(1990, 1, 1), datetime(1990, 1, 2)]
    assert nch.time_axis(hours)[:2] == [datetime(1990, 1, 1), datetime(1990, 1, 2)]


def test_variables_without_a_time_dimension_are_not_offered(tmp_path):
    from netCDF4 import Dataset

    path = tmp_path / "flat.nc"
    with Dataset(path, "w") as ds:
        ds.createDimension("y", 2)
        ds.createDimension("x", 2)
        ds.createVariable("mask", "i4", ("y", "x"))[:] = np.ones((2, 2))
    assert nch.grid_variables(path) == []


def test_raster_variables_exclude_coordinate_axes(tmp_path):
    from netCDF4 import Dataset

    from mhm_qgis.core.handlers.file.netcdf.raster import raster_variables

    path = tmp_path / "raster.nc"
    with Dataset(path, "w") as dataset:
        dataset.createDimension("y", 2)
        dataset.createDimension("x", 3)
        dataset.createDimension("bounds", 2)
        dataset.createVariable("x", "f8", ("x",))[:] = [0, 1, 2]
        dataset.createVariable("y", "f8", ("y",))[:] = [0, 1]
        x = dataset.variables["x"]
        x.bounds = "x_bnds"
        dataset.createVariable("x_bnds", "f8", ("x", "bounds"))[:] = 0
        latitude = dataset.createVariable("lat", "f8", ("y", "x"))
        latitude.standard_name = "latitude"
        latitude[:] = 0
        classes = dataset.createVariable("classes", "i4", ("y", "x"))
        classes.grid_mapping = "crs"
        classes[:] = 1
        crs = dataset.createVariable("crs", "i4")
        crs.grid_mapping_name = "transverse_mercator"

    assert raster_variables(path) == ("classes",)


def test_a_missing_file_yields_nothing(tmp_path):
    assert nch.grid_variables(tmp_path / "absent.nc") == []
    assert nch.time_axis(tmp_path / "absent.nc") == []


def test_output_files_are_newest_first(tmp_path):
    import os
    import time

    for name, age in (("old.nc", 100), ("new.nc", 0)):
        p = tmp_path / name
        p.write_bytes(b"")
        os.utime(p, (time.time() - age, time.time() - age))
    assert [p.name for p in nch.output_files(tmp_path)] == ["new.nc", "old.nc"]


def test_a_slice_is_georeferenced_by_the_supplied_crs(tmp_path):
    path = _write(tmp_path / "g.nc", y_name="northing", x_name="easting",
                  time_units="days since 1990-01-01", offsets=[0, 1, 2])
    variable = nch.grid_variables(path)[0]
    data = nch.read_slice(path, variable, 1, crs="EPSG:3035")

    # The dims are normalised so downstream code never has to branch.
    assert data.dims == ("y", "x")
    assert data.rio.crs.to_epsg() == 3035
    assert data.rio.transform().a == pytest.approx(1000.0)


# --- against the real files, which is where the assumptions actually bite ---

@pytest.mark.skipif(not DAILY_YX.is_file(), reason="sample output not present")
def test_real_daily_output_with_yx_dims():
    variables = nch.grid_variables(DAILY_YX)
    assert len(variables) >= 6  # enabled mHM outputs vary by run configuration
    assert {v.name for v in variables} >= {"Q", "QD", "QB"}
    assert (variables[0].y_dim, variables[0].x_dim) == ("y", "x")
    steps = nch.time_axis(DAILY_YX)
    assert len(steps) == 1461
    assert (steps[1] - steps[0]).days == 1


@pytest.mark.skipif(not MONTHLY_NE.is_file(), reason="sample output not present")
def test_real_monthly_output_with_northing_easting_dims():
    variables = nch.grid_variables(MONTHLY_NE)
    assert len(variables) == 22
    assert (variables[0].y_dim, variables[0].x_dim) == ("northing", "easting")
    steps = nch.time_axis(MONTHLY_NE)
    assert len(steps) == 48
    # Monthly spacing - anything assuming a fixed step would be wrong here.
    assert {(steps[i + 1] - steps[i]).days for i in range(5)} == {28, 30, 31}
