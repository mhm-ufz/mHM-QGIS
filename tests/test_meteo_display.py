"""Tests for resolving prepared meteorology forcing to a raster band."""

from __future__ import annotations

from datetime import date, datetime

import numpy as np
import pytest

from mhm_qgis.core.meteorology import display


def _write_forcing(project, variable, first, days):
    """Write a minimal prepared forcing file in the v5.13 layout."""
    from netCDF4 import Dataset

    folder = project / "mhm-qgis" / "data" / "master" / "meteo" / variable
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{variable}.nc"
    offset = (first - display.TIME_EPOCH).days
    with Dataset(path, "w") as ds:
        ds.createDimension("time", days)
        ds.createDimension("lat", 2)
        ds.createDimension("lon", 3)
        axis = ds.createVariable("time", "i4", ("time",))
        axis.units = "days since 1900-01-01 00:00:00"
        axis.calendar = "proleptic_gregorian"
        axis[:] = np.arange(offset, offset + days, dtype="i4")
        data = ds.createVariable(variable, "f8", ("time", "lat", "lon"))
        data[:] = np.zeros((days, 2, 3))
    return path


@pytest.fixture
def project(tmp_path):
    _write_forcing(tmp_path, "pre", date(2016, 1, 1), 366)
    _write_forcing(tmp_path, "tavg", date(2016, 1, 1), 366)
    return tmp_path


def test_only_prepared_variables_are_offered(project):
    # tmin, tmax and pet were never written, so they must not be offered even
    # though expected_meteo_outputs names all five.
    assert display.available_meteo_variables(project) == ["pre", "tavg"]


def test_a_project_without_forcing_offers_nothing(tmp_path):
    assert display.available_meteo_variables(tmp_path) == []
    assert display.project_time_range(tmp_path) is None


def test_time_range_spans_the_written_period(project):
    assert display.project_time_range(project) == (datetime(2016, 1, 1), datetime(2016, 12, 31))


def test_bands_count_from_one_at_the_first_step(project):
    first = display.resolve_meteo_output(project, "pre", date(2016, 1, 1))
    assert first.band == 1
    assert first.variable == "pre"
    assert first.path.name == "pre.nc"

    last = display.resolve_meteo_output(project, "pre", date(2016, 12, 31))
    assert last.band == 366

    middle = display.resolve_meteo_output(project, "pre", date(2016, 3, 1))
    assert middle.band == 61  # 2016 is a leap year


@pytest.mark.parametrize("when", [date(2015, 12, 31), date(2017, 1, 1)])
def test_dates_outside_the_period_resolve_to_nothing(project, when):
    assert display.resolve_meteo_output(project, "pre", when) is None


def test_an_unprepared_variable_resolves_to_nothing(project):
    assert display.resolve_meteo_output(project, "tmin", date(2016, 1, 1)) is None



def test_native_hourly_bands_and_calendar_units(tmp_path):
    from netCDF4 import Dataset
    path = _write_forcing(tmp_path, "pre", date(2016, 1, 1), 3)
    with Dataset(path, "a") as ds:
        ds["time"].units = "hours since 2016-01-01"
        ds["time"][:] = [0, 1, 2]
    assert len(display.meteo_steps(path)) == 3
    assert display.resolve_meteo_output(tmp_path, "pre", datetime(2016, 1, 1, 1)).band == 2
    assert display.resolve_meteo_output(tmp_path, "pre", datetime(2016, 1, 1, 3)) is None
