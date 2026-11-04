"""Tests for the period and horizon mHM-tools adapter calls."""
from __future__ import annotations

import sys
from types import ModuleType

import pytest

from mhm_qgis.externals.mhm_tools_handler import (
    create_latlon_file,
    lai_time_step,
    prepare_lai_file,
    prepare_land_cover_periods,
    prepare_soil_horizons,
)


def test_latlon_adapter_writes_netcdf_without_compression_argument(tmp_path):
    pytest.importorskip("mhm_tools.pre.latlon")
    xr = pytest.importorskip("xarray")
    output = create_latlon_file(
        tmp_path / "latlon.nc",
        {"ncols": 4, "nrows": 4, "xllcorner": 80.0, "yllcorner": 25.0,
         "cellsize": 0.5, "NODATA_value": -9999},
        level1=1.0,
        level11=1.0,
        level2=2.0,
        crs="EPSG:4326",
    )
    with xr.open_dataset(output) as dataset:
        for name, size in (("lat_l0", 4), ("lat", 2), ("lat_l11", 2), ("lat_l2", 1)):
            assert dataset[name].shape == (size, size)
            assert dataset[name].notnull().all().item()


def _install_fake(monkeypatch, **functions):
    package = ModuleType("mhm_tools")
    pre = ModuleType("mhm_tools.pre")
    for name, function in functions.items():
        setattr(pre, name, function)
    package.pre = pre
    monkeypatch.setitem(sys.modules, "mhm_tools", package)
    monkeypatch.setitem(sys.modules, "mhm_tools.pre", pre)


def test_land_cover_period_adapter_uses_public_api(tmp_path, monkeypatch):
    calls = []

    # Signature copied from mhm_tools.pre.format_lc_periods, so a keyword the
    # adapter renames is a test failure rather than a runtime TypeError.
    def format_lc_periods(
        input_file,
        dem_file,
        output_path,
        lookup_table,
        mapping_field,
        class_field,
        output_type="nc",
        **kwargs,
    ):
        calls.append(dict(kwargs, input_file=input_file, output_path=output_path))
        output = output_path / "lc_periods.nc"
        output.write_bytes(b"nc")
        return (output,)

    _install_fake(monkeypatch, format_lc_periods=format_lc_periods)
    output = prepare_land_cover_periods(
        tmp_path / "format-data.csv",
        tmp_path / "dem.tif",
        tmp_path / "output",
        tmp_path / "lookup.csv",
        "source",
        "class",
        output_type="nc",
    )

    assert [path.name for path in output] == ["lc_periods.nc"]
    assert calls[0]["input_file"] == tmp_path / "format-data.csv"
    assert calls[0]["resampling"] == "auto"


@pytest.mark.parametrize(("options", "expected"), [
    ({}, (5.0, 0.1)),
    ({"composition_step": 2.5, "bulk_density_step": 0.05}, (2.5, 0.05)),
])
def test_soil_horizon_adapter_returns_data_and_definition(tmp_path, monkeypatch, options, expected):
    calls = []

    # Signature copied from mhm_tools.pre.format_soil_horizons.
    def format_soil_horizons(
        input_file, dem_file, output_path, output_type="nc", *,
        resampling="auto", composition_step=5.0, bulkdensity_step=0.1,
    ):
        calls.append((input_file, composition_step, bulkdensity_step))
        data = output_path / "soil_class.asc"
        definition = output_path / "soil_classdefinition.txt"
        data.write_bytes(b"asc")
        definition.write_text("definition", encoding="utf-8")
        return data, definition

    _install_fake(monkeypatch, format_soil_horizons=format_soil_horizons)
    data, definition = prepare_soil_horizons(
        tmp_path / "format-data.csv",
        tmp_path / "dem.tif",
        tmp_path / "output",
        output_type="asc",
        **options,
    )

    assert data.name == "soil_class.asc"
    assert definition.name == "soil_classdefinition.txt"
    assert calls == [(tmp_path / "format-data.csv", *expected)]


def test_lai_adapter_uses_the_mhm_tools_api(tmp_path, monkeypatch):
    calls = []
    format_lai = ModuleType("mhm_tools.pre.format_lai")

    def format_lai_netcdf_file(input_file, dem_file, output_file, **kwargs):
        calls.append((input_file, dem_file, output_file, kwargs))
        output_file.write_bytes(b"nc")
        return output_file

    format_lai.format_lai_netcdf_file = format_lai_netcdf_file
    format_lai.locate_lai_cube = lambda *_args: None  # a grid it can read
    format_lai.lai_time_step = lambda resolution: {
        "long-term-mean-monthly": 1
    }[resolution]
    monkeypatch.setitem(sys.modules, "mhm_tools.pre.format_lai", format_lai)
    xr = pytest.importorskip("xarray")
    xr.Dataset({"lai": (("time", "lat", "lon"), [[[1.0]]])},
               coords={"time": [0], "lat": [50.0], "lon": [10.0]}).to_netcdf(tmp_path / "source.nc")

    output = prepare_lai_file(
        tmp_path / "source.nc",
        tmp_path / "dem.tif",
        tmp_path / "lai.nc",
        output_temporal_resolution="monthly",
        input_crs="EPSG:3035",
        dem_crs="EPSG:4326",
    )

    assert output == tmp_path / "lai.nc"
    assert calls[0][3]["output_temporal_resolution"] == "monthly"
    assert (calls[0][3]["input_crs"], calls[0][3]["dem_crs"]) == ("EPSG:3035", "EPSG:4326")
    assert lai_time_step("long-term-mean-monthly") == 1


def _monthly_lai(path, shape, **coords):
    import numpy as np
    import pandas as pd
    import xarray as xr

    dims = ("time", "y", "x")
    values = np.arange(12 * shape[0] * shape[1], dtype=float).reshape(12, *shape)
    variables = {"lai": (dims, values), **{name: (("y", "x"), value) for name, value in coords.items()}}
    xr.Dataset(variables, coords={"time": pd.date_range("2001-01-31", periods=12, freq="ME")}).to_netcdf(path)
    return values


def test_lai_without_coordinates_is_used_as_is_on_the_l0_grid(tmp_path):
    pytest.importorskip("mhm_tools.pre.format_lai")
    gdal = pytest.importorskip("osgeo.gdal")
    xr = pytest.importorskip("xarray")
    values = _monthly_lai(tmp_path / "lai.nc", (3, 2))
    header = {"ncols": 2, "nrows": 3, "xllcorner": 0.0, "yllcorner": 0.0, "cellsize": 100.0}
    dem = gdal.GetDriverByName("GTiff").Create(str(tmp_path / "dem.tif"), 4, 5, 1)  # padded by one cell
    dem.SetGeoTransform((-100.0, 100.0, 0.0, 400.0, 0.0, -100.0))
    dem = None

    output = prepare_lai_file(tmp_path / "lai.nc", tmp_path / "dem.tif", tmp_path / "out.nc",
                              output_temporal_resolution="monthly", source_variable="lai",
                              dem_crs="EPSG:3035", l0_header=header)

    with xr.open_dataset(output) as staged:
        assert staged["lai"].shape == (12, 5, 4)  # on the filled DEM's grid, as publication requires
        assert (staged["lai"].values[:, 1:-1, 1:-1] == values).all()  # rows north-first
        assert list(staged["yc"].values) == [350.0, 250.0, 150.0, 50.0, -50.0]
    with pytest.raises(ValueError, match=r"\(4, 2\).*\(3, 2\)"):
        prepare_lai_file(tmp_path / "lai.nc", tmp_path / "dem.tif", tmp_path / "out.nc",
                         source_variable="lai", l0_header={**header, "nrows": 4})


def test_lai_on_x_y_axes_goes_to_mhm_tools_not_the_l0_copy(tmp_path, monkeypatch):
    """GIMMS names its lon/lat axes x/y; any grid mHM-tools reads is resampled, whatever its shape."""
    import numpy as np

    format_lai = pytest.importorskip("mhm_tools.pre.format_lai")
    xr = pytest.importorskip("xarray")
    x = xr.DataArray(79 + 0.1 * np.arange(5), dims="x", attrs={"standard_name": "longitude"})
    y = xr.DataArray(26 + 0.1 * np.arange(3), dims="y", attrs={"standard_name": "latitude"})
    _monthly_lai(tmp_path / "lai.nc", (3, 5))
    with xr.open_dataset(tmp_path / "lai.nc") as lai:
        lai.load().assign_coords(x=x, y=y).to_netcdf(tmp_path / "gimms.nc")
    seen = []
    monkeypatch.setattr(format_lai, "format_lai_netcdf_file",
                        lambda source, _dem, output, **_kwargs: seen.append(source) or output)
    (tmp_path / "out.nc").write_bytes(b"nc")

    prepare_lai_file(tmp_path / "gimms.nc", tmp_path / "dem.tif", tmp_path / "out.nc", source_variable="lai",
                     l0_header={"ncols": 9, "nrows": 9, "xllcorner": 0.0, "yllcorner": 0.0, "cellsize": 1.0})

    assert seen == [tmp_path / "gimms.nc"]
