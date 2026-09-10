"""Checks that L2 inputs are sliced when aligned and only nearest-resampled otherwise."""
from __future__ import annotations

import os

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")



from mhm_qgis.core.grid import TargetGrid  # noqa: E402
from mhm_qgis.core.meteorology.forcing.api import _resample_ready  # noqa: E402
from mhm_qgis.core.meteorology.ERA5Land.mhm.io import write_header  # noqa: E402
from mhm_qgis.core.grid import (  # noqa: E402
    assert_header_file_matches,
    assert_matches_header,
)

xr = pytest.importorskip("xarray")


L2_HEADER = {
    "ncols": 4,
    "nrows": 3,
    "xllcorner": 1000.0,
    "yllcorner": 2000.0,
    "cellsize": 100.0,
    "nodata_value": -9999.0,
}
CRS = "EPSG:32632"


def _target_grid():
    """Return a projected L2 target grid matching ``L2_HEADER``."""
    lon = np.linspace(10.0, 10.3, L2_HEADER["ncols"])
    lat = np.linspace(50.2, 50.0, L2_HEADER["nrows"])
    sample_lon, sample_lat = np.meshgrid(lon, lat)
    return TargetGrid(
        header=dict(L2_HEADER),
        lon=lon,
        lat=lat,
        crs=CRS,
        sample_lon=sample_lon,
        sample_lat=sample_lat,
    )


def _source(x_values, y_values, values):
    """Return a one-step mHM-ready dataset on the given projected axes."""
    da = xr.DataArray(
        values[np.newaxis, :, :].astype("float64"),
        dims=("time", "y", "x"),
        coords={
            "time": np.array(["2000-01-01"], dtype="datetime64[ns]"),
            "y": np.asarray(y_values, dtype="float64"),
            "x": np.asarray(x_values, dtype="float64"),
        },
        name="pre",
    )
    return da, da.to_dataset()


def test_aligned_l2_input_is_sliced_and_padded_without_resampling(monkeypatch):
    target = _target_grid()
    # Source covers only the middle two columns of the target grid.
    values = np.array([[1.0, 2.0], [3.0, 4.0], [5.0, 6.0]])
    da, ds = _source([1150.0, 1250.0], [2250.0, 2150.0, 2050.0], values)

    def _no_interp(*_args, **_kwargs):
        raise AssertionError("An aligned L2 input must not be interpolated.")

    monkeypatch.setattr(xr.DataArray, "interp", _no_interp)

    result = _resample_ready(da, ds, CRS, target, np, xr)

    assert result.sizes["lat"] == L2_HEADER["nrows"]
    assert result.sizes["lon"] == L2_HEADER["ncols"]
    placed = result.isel(time=0).values
    np.testing.assert_array_equal(placed[:, 1:3], values)
    assert np.all(np.isnan(placed[:, 0]))
    assert np.all(np.isnan(placed[:, 3]))


def test_misaligned_l2_input_is_corrected_with_nearest_neighbour():
    target = _target_grid()
    # Half a cell east and south of the target cell centres.
    values = np.arange(12, dtype="float64").reshape(3, 4)
    da, ds = _source(
        [1100.0, 1200.0, 1300.0, 1400.0],
        [2200.0, 2100.0, 2000.0],
        values,
    )

    result = _resample_ready(da, ds, CRS, target, np, xr)
    placed = result.isel(time=0).values

    assert placed.shape == (L2_HEADER["nrows"], L2_HEADER["ncols"])
    # Nearest neighbour reproduces source values exactly; interpolation would
    # have produced intermediate values instead.
    assert set(np.unique(placed[~np.isnan(placed)])).issubset(set(values.ravel()))


def test_prepared_grid_and_written_header_are_validated(tmp_path):
    dataset = xr.DataArray(
        np.zeros((1, L2_HEADER["nrows"], L2_HEADER["ncols"])),
        dims=("time", "lat", "lon"),
        name="pre",
    ).to_dataset()
    assert_matches_header(dataset, "pre", L2_HEADER)

    wrong = xr.DataArray(
        np.zeros((1, L2_HEADER["nrows"] + 1, L2_HEADER["ncols"])),
        dims=("time", "lat", "lon"),
        name="pre",
    ).to_dataset()
    with pytest.raises(ValueError, match="expected"):
        assert_matches_header(wrong, "pre", L2_HEADER)

    header_file = tmp_path / "header.txt"
    write_header(dataset, "pre", header_file, header=dict(L2_HEADER))
    assert_header_file_matches(header_file, L2_HEADER)

    with pytest.raises(ValueError):
        assert_header_file_matches(
            header_file, {**L2_HEADER, "cellsize": 200.0}
        )


def _era5_file(path, days=31, rows=4, cols=5):
    """Write one hourly ERA5-Land style monthly file."""
    times = np.arange(
        np.datetime64("1980-01-01T00"),
        np.datetime64("1980-01-01T00") + np.timedelta64(24 * days, "h"),
        np.timedelta64(1, "h"),
    )
    xr.DataArray(
        np.zeros((times.size, rows, cols), dtype="float32"),
        dims=("valid_time", "latitude", "longitude"),
        coords={
            "valid_time": times,
            "latitude": np.linspace(31.0, 30.0, rows),
            "longitude": np.linspace(79.0, 80.0, cols),
        },
        name="t2m",
    ).to_dataset().to_netcdf(path)


def test_a_long_record_falls_back_to_one_variable_at_a_time(tmp_path, monkeypatch):
    """Reading files once for tavg/tmin/tmax must not multiply the peak memory."""
    from mhm_qgis.core.meteorology.ERA5Land.mhm.build import (
        METEO_MAX_BYTES_ENV,
        estimated_single_pass_bytes,
        single_pass_byte_limit,
    )

    path = tmp_path / "ERA5_Land_2m_temperature_1980_01.nc"
    _era5_file(path)
    lat = np.linspace(31.0, 30.0, 51)
    lon = np.linspace(79.0, 90.0, 111)

    # 31 days x 552 files x 3 variables x 51 x 111 cells x 8 bytes x 2 copies.
    estimate = estimated_single_pass_bytes([path] * 552, 3, lat, lon)
    assert estimate == 31 * 552 * 3 * 51 * 111 * 8 * 2
    assert estimate > single_pass_byte_limit()

    # A short record still gets the single-pass read.
    assert estimated_single_pass_bytes([path] * 12, 3, lat, lon) < single_pass_byte_limit()

    # The budget is operator-tunable.
    monkeypatch.setenv(METEO_MAX_BYTES_ENV, str(64 * 1024 ** 3))
    assert estimate < single_pass_byte_limit()


def test_the_single_pass_and_sequential_paths_agree(tmp_path, monkeypatch):
    from mhm_qgis.core.meteorology.ERA5Land.mhm.build import (
        METEO_MAX_BYTES_ENV,
        build_daily_datasets,
    )
    from mhm_qgis.core.meteorology.ERA5Land.mhm.specs import FORCING_SPECS

    files = []
    for month in (1, 2):
        path = tmp_path / f"ERA5_Land_2m_temperature_1980_{month:02d}.nc"
        _era5_file(path, days=5)
        files.append(path)
    specs = [s for s in FORCING_SPECS if s.output_variable in ("tavg", "tmin", "tmax")]
    lat = np.linspace(31.0, 30.0, 4)
    lon = np.linspace(79.0, 80.0, 5)
    kwargs = dict(bounds=None, target_lat=lat, target_lon=lon)

    monkeypatch.setenv(METEO_MAX_BYTES_ENV, str(64 * 1024 ** 3))
    single = build_daily_datasets(files, specs, **kwargs)
    monkeypatch.setenv(METEO_MAX_BYTES_ENV, "1")
    sequential = build_daily_datasets(files, specs, **kwargs)

    assert sorted(single) == sorted(sequential) == ["tavg", "tmax", "tmin"]
    for variable in single:
        np.testing.assert_allclose(
            single[variable][variable].values,
            sequential[variable][variable].values,
        )
