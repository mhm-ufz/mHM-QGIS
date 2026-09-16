import importlib
from pathlib import Path

import numpy as np
import pytest
import xarray as xr

from mhm_qgis.core.meteorology.forcing import (
    ERA5LAND,
    MHM_READY,
    MeteoFolderSpec,
    inspect_meteo_folder,
    inspect_meteo_inputs,
    process_meteo_inputs,
)
from mhm_qgis.core.grid import TargetGrid, resolution_in_crs


LAT = np.array([51.0, 50.0])
LON = np.array([10.0, 11.0])


def test_legacy_era5land_apis_are_removed():
    from mhm_qgis.core.meteorology import ERA5Land

    assert not hasattr(ERA5Land, "process_era5_to_swat")
    assert not hasattr(ERA5Land, "process_era5_to_mhm")
    assert not hasattr(ERA5Land, "inspect_era5_folder")
    for module in ("mhm_forcing", "mhm.api"):
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module(
                f"mhm_qgis.core.meteorology.ERA5Land.{module}")


def _write_nc(
        path: Path,
        variable: str,
        times=("2001-01-01T12:00:00",),
        lat=LAT,
        lon=LON,
        values=None,
        units=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    times = np.asarray(times, dtype="datetime64[ns]")
    if values is None:
        values = np.ones((len(times), len(lat), len(lon)), dtype="float64")
    ds = xr.Dataset(
        {variable: (("time", "latitude", "longitude"), values)},
        coords={"time": times, "latitude": lat, "longitude": lon},
    )
    if units:
        ds[variable].attrs["units"] = units
    ds.to_netcdf(path, engine="scipy")


def _write_projected_nc(path: Path, variable: str, value: float):
    from pyproj import CRS

    path.parent.mkdir(parents=True, exist_ok=True)
    ds = xr.Dataset(
        {
            variable: (
                ("time", "yc", "xc"),
                np.full((1, 2, 2), value, dtype="float64"),
                {"grid_mapping": "crs"},
            ),
            "crs": ((), 0, {"spatial_ref": CRS.from_epsg(3857).to_wkt()}),
        },
        coords={
            "time": np.asarray(["2001-01-01"], dtype="datetime64[ns]"),
            "yc": [1500.0, 500.0],
            "xc": [500.0, 1500.0],
        },
    )
    ds.to_netcdf(path, engine="scipy")



def target():
    return TargetGrid(lon=LON, lat=LAT, crs="EPSG:4326", header={
        "ncols": 2, "nrows": 2, "xllcorner": 9.5, "yllcorner": 49.5,
        "cellsize": 1., "nodata_value": -9999.})


def test_scan_reads_metadata_and_glob_only(tmp_path, monkeypatch):
    _write_nc(tmp_path / "wanted.nc", "pre", units="mm")
    (tmp_path / "ignored.nc").write_text("not netcdf")
    spec = MeteoFolderSpec("pre", tmp_path, unit="mm", pattern="wanted*.nc")
    def no_data(*args, **kwargs):
        raise AssertionError("scan read a forcing cube")
    monkeypatch.setattr(xr.DataArray, "load", no_data)
    metadata = inspect_meteo_folder(spec)
    assert metadata.shape == (2, 2)
    assert metadata.resolution == 1.
    assert len(metadata.files) == 1
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("cadence,times", [
    ("Hourly", ("2001-01-01T00", "2001-01-01T01", "2001-01-01T02")),
    ("Daily", ("2001-01-01", "2001-01-02", "2001-01-03")),
    ("Monthly", ("2000-01-01", "2000-02-01", "2000-03-01")),
    ("Yearly", ("2000-01-01", "2001-01-01", "2002-01-01")),
])
def test_native_cadence_is_preserved(tmp_path, cadence, times):
    _write_nc(tmp_path / "raw/pre.nc", "pre", times=times, units="mm")
    spec = MeteoFolderSpec("pre", tmp_path / "raw", unit="mm", temporal_resolution=cadence)
    result = process_meteo_inputs([spec], tmp_path / "out", target())
    with xr.open_dataset(result.outputs["pre"]) as ds:
        np.testing.assert_array_equal(ds.time, np.array(times, dtype="datetime64[ns]"))
        np.testing.assert_allclose(ds.pre, 1.)
        assert ds.pre.dtype == "float64"
        assert ds.time.attrs["bounds"] == "time_bnds"


def test_cumulative_hourly_cross_file_and_chunk_boundaries(tmp_path):
    times = np.datetime64("2001-01-01T01") + np.arange(72) * np.timedelta64(1, "h")
    values = np.broadcast_to(((np.arange(72) % 24 + 1) / 1000.)[:, None, None], (72, 2, 2))
    for name, selection in (("a", slice(0, 37)), ("b", slice(37, None))):
        _write_nc(tmp_path / f"raw/{name}.nc", "tp", times=times[selection],
                  values=values[selection], units="m")
    spec = MeteoFolderSpec("pre", tmp_path / "raw", unit="m", temporal_resolution="Hourly",
                          cumulative=True, cumulative_resolution="Daily")
    result = process_meteo_inputs([spec], tmp_path / "out", target(), flat_layout=True)
    assert result.headers == {}
    with xr.open_dataset(result.outputs["pre"]) as ds:
        assert ds.sizes["time"] == 72
        np.testing.assert_allclose(ds.pre, 1., atol=1e-12)
        np.testing.assert_array_equal(ds.time, times.astype("datetime64[ns]"))
        np.testing.assert_array_equal(ds.time_bnds[:, 1], ds.time)
        assert ds.time_bnds[0, 0] == np.datetime64("2001-01-01T00")


@pytest.mark.parametrize("variable,source,method,expected", [
    ("tavg", "t2m", "Average", 11.5),
    ("tmin", "t2m", "Minimum", 0.0),
    ("tmax", "t2m", "Maximum", 23.0),
    ("pre", "pre", "Sum", 276.0),
])
def test_hourly_values_are_aggregated_before_conversion(
        tmp_path, variable, source, method, expected):
    times = np.datetime64("2001-01-01T00") + np.arange(24) * np.timedelta64(1, "h")
    values = np.broadcast_to(np.arange(24)[:, None, None], (24, 2, 2)).astype(float)
    unit = "K" if variable.startswith("t") else "mm"
    if unit == "K":
        values += 273.15
    _write_nc(tmp_path / "raw/values.nc", source, times=times, values=values, units=unit)
    spec = MeteoFolderSpec(
        variable, tmp_path / "raw", unit=unit, temporal_resolution="Hourly",
        output_temporal_resolution="Daily", aggregation=method)
    result = process_meteo_inputs([spec], tmp_path / "out", target())
    with xr.open_dataset(result.outputs[variable]) as ds:
        assert ds.sizes["time"] == 1
        np.testing.assert_allclose(ds[variable], expected)


def test_partial_output_interval_warns_and_is_written(tmp_path):
    times = np.datetime64("2001-01-01T03") + np.arange(3) * np.timedelta64(1, "h")
    _write_nc(tmp_path / "raw/pre.nc", "pre", times=times, units="mm")
    messages = []
    spec = MeteoFolderSpec(
        "pre", tmp_path / "raw", unit="mm", temporal_resolution="Hourly",
        output_temporal_resolution="Daily", aggregation="Sum")
    result = process_meteo_inputs([spec], tmp_path / "out", target(), log=messages.append)
    with xr.open_dataset(result.outputs["pre"]) as ds:
        np.testing.assert_allclose(ds.pre, 3.0)
    assert any("partial daily interval" in message for message in messages)


def test_era_hourly_precipitation_can_use_daily_maximum(tmp_path):
    times = np.datetime64("2001-01-01T01") + np.arange(24) * np.timedelta64(1, "h")
    values = np.broadcast_to(
        (np.arange(1, 25) / 1000.0)[:, None, None], (24, 2, 2))
    _write_nc(tmp_path / "raw/tp.nc", "tp", times=times, values=values, units="m")
    spec = MeteoFolderSpec(
        "pre", tmp_path / "raw", unit="m", temporal_resolution="Hourly",
        output_temporal_resolution="Daily", aggregation="Maximum")
    result = process_meteo_inputs([spec], tmp_path / "out", target())
    with xr.open_dataset(result.outputs["pre"]) as ds:
        assert ds.sizes["time"] == 1
        np.testing.assert_allclose(ds.pre, 24.0)


@pytest.mark.parametrize("variable,source,unit,value,expected", [
    ("tavg", "t2m", "K", 274.15, 1.),
    ("tmin", "tmin", "Celsius", -5., -5.),
    ("tmax", "tmax", "K", 283.15, 10.),
    ("tann", "tann", "Celsius", 10., 10.),
    ("pet", "pev", "m", -.002, 2.),
    ("eabs", "eabs", "hPa", 12., 1200.),
    ("windspeed", "windspeed", "km/h", 36., 10.),
    ("ssrd", "ssrd", "J m-2", 3600., 1.),
    ("strd", "strd", "W m-2", 10., 10.),
    ("pre", "pre", "kg m-2 s-1", .001, 3.6),
])
def test_units_for_independent_variables(tmp_path, variable, source, unit, value, expected):
    _write_nc(tmp_path / "raw/values.nc", source, units=unit, values=np.full((1, 2, 2), value))
    spec = MeteoFolderSpec(variable, tmp_path / "raw", unit=unit, temporal_resolution="Hourly")
    result = process_meteo_inputs([spec], tmp_path / "out", target())
    assert tuple(result.outputs) == (variable,)
    with xr.open_dataset(result.outputs[variable]) as ds:
        np.testing.assert_allclose(ds[variable], expected, atol=1e-12)


def test_discontinuous_times_units_and_grids_are_rejected(tmp_path):
    folder = tmp_path / "raw"
    _write_nc(folder / "a.nc", "pre", times=("2001-01-01",), units="mm")
    spec = MeteoFolderSpec("pre", folder, unit="m")
    with pytest.raises(ValueError, match="metadata unit"):
        inspect_meteo_folder(spec)
    spec = MeteoFolderSpec("pre", folder, unit="mm")
    _write_nc(folder / "b.nc", "pre", times=("2001-01-03",), units="mm")
    with pytest.raises(ValueError, match="continuous"):
        inspect_meteo_folder(spec)
    _write_nc(folder / "b.nc", "pre", times=("2001-01-02",), lon=np.array([10., 12.]), units="mm")
    with pytest.raises(ValueError, match="spatial"):
        inspect_meteo_folder(spec)


def test_incomplete_cumulative_cycle_does_not_replace_outputs(tmp_path):
    _write_nc(tmp_path / "raw/a.nc", "tp", times=("2001-01-01T12",), units="m")
    output = tmp_path / "out/pre.nc"
    output.parent.mkdir()
    output.write_bytes(b"original")
    spec = MeteoFolderSpec("pre", tmp_path / "raw", unit="m", temporal_resolution="Hourly",
                          cumulative=True, cumulative_resolution="Daily")
    with pytest.raises(ValueError, match="mid-cycle"):
        process_meteo_inputs([spec], output.parent, target(), flat_layout=True)
    assert output.read_bytes() == b"original"


def test_supplied_inspection_and_padded_grid(tmp_path, monkeypatch):
    from mhm_qgis.core.meteorology.forcing import api
    from mhm_qgis.core.grid import target_grid_from_header
    _write_nc(tmp_path / "raw/pre.nc", "pre", units="mm")
    spec = MeteoFolderSpec("pre", tmp_path / "raw", unit="mm")
    inspected = inspect_meteo_inputs([spec])
    def repeated(*args, **kwargs):
        raise AssertionError("repeated inspection")
    monkeypatch.setattr(api, "inspect_meteo_inputs", repeated)
    header = dict(target().header, ncols=4, nrows=4, xllcorner=8.5, yllcorner=48.5)
    grid = target_grid_from_header(header, "EPSG:4326")
    result = process_meteo_inputs([spec], tmp_path / "out", grid, inspections=inspected)
    with xr.open_dataset(result.outputs["pre"]) as ds:
        assert ds.pre.shape == (1, 4, 4)
        assert np.isnan(ds.pre[:, 0, :]).all()
        np.testing.assert_allclose(ds.pre[:, 1:3, 1:3], 1.)


def test_projected_input_uses_exact_target_grid(tmp_path):
    from mhm_qgis.core.grid import target_grid_from_header, assert_matches_header
    _write_projected_nc(tmp_path / "raw/tavg.nc", "tavg", 273.15)
    spec = MeteoFolderSpec("tavg", tmp_path / "raw", unit="K")
    header = dict(target().header, xllcorner=0., yllcorner=0., cellsize=1000.)
    grid = target_grid_from_header(header, "EPSG:3857")
    result = process_meteo_inputs([spec], tmp_path / "out", grid)
    with xr.open_dataset(result.outputs["tavg"]) as ds:
        assert_matches_header(ds, "tavg", header)
        np.testing.assert_allclose(ds.tavg, 0.)
        assert ds.tavg.attrs["grid_mapping"] == "crs"


def test_publication_failure_restores_previous_files(tmp_path, monkeypatch):
    from mhm_qgis.core.meteorology.forcing import api
    _write_nc(tmp_path / "raw/pre.nc", "pre", units="mm")
    spec = MeteoFolderSpec("pre", tmp_path / "raw", unit="mm")
    output = tmp_path / "out/pre.nc"
    output.parent.mkdir()
    output.write_bytes(b"old")
    replace = api.os.replace
    def fail(source, destination):
        if Path(destination) == output and "backups" not in Path(source).parts:
            raise OSError("publish failed")
        return replace(source, destination)
    monkeypatch.setattr(api.os, "replace", fail)
    with pytest.raises(OSError, match="publish failed"):
        process_meteo_inputs([spec], output.parent, target(), flat_layout=True)
    assert output.read_bytes() == b"old"


def test_execute_meteo_prepares_and_reuses_the_configured_collection(tmp_path, monkeypatch):
    from mhm_qgis.core.executions import meteo
    from mhm_qgis.core.handlers.state.meteo_outputs import MeteorologyOutputState
    from mhm_qgis.core.handlers.store.layout import ensure_project_structure, input_state_path
    root = tmp_path / "mhm-qgis/Alpine"
    ensure_project_structure(root, "6")
    Path(input_state_path(root)).write_text('{"mhm_version": "6"}')
    times = ("2001-01-01T00", "2001-01-01T01", "2001-01-01T02")
    inputs = []
    for variable, unit in (("pre", "mm"), ("tavg", "Celsius")):
        _write_nc(tmp_path / f"raw/{variable}.nc", variable, times=times, units=unit)
        inputs.append(MeteoFolderSpec(variable, tmp_path / "raw", unit=unit,
                                     pattern=f"{variable}.nc", temporal_resolution="Hourly"))
    run = meteo.MeteorologyRun(root, tuple(inputs), target(), {"l2_header": target().header})
    state = MeteorologyOutputState(root)
    assert meteo.prepare_forcing(run, state)
    assert (root / "data/meteo/pre.nc").is_file()
    assert (root / "data/meteo/tavg.nc").is_file()
    assert (root / "data/meteo/mask.nc").is_file()
    assert not (root / "data/meteo/tmin.nc").exists()
    monkeypatch.setattr(meteo, "process_meteo_inputs", lambda **kwargs: pytest.fail("reused forcing was rebuilt"))
    assert meteo.prepare_forcing(run, state)
