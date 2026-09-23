"""Configuration contracts before either execution workflow starts."""
import numpy as np
import pytest
import xarray as xr

from mhm_qgis.core.handlers.state.nml_settings import validate_times, update_section
from mhm_qgis.core.meteorology.forcing import MeteoFolderSpec, inspect_meteo_folder, inspect_meteo_folder_cached


def precipitation(path, *, variable="pre", units="mm", hours=24, start="2001-01-01"):
    times = np.datetime64(start) + np.arange(3) * np.timedelta64(hours, "h")
    ds = xr.Dataset({variable: (("time", "lat", "lon"), np.ones((3, 2, 2)), {"units": units})},
                    coords={"time": times, "lat": [51., 50.], "lon": [10., 11.]})
    ds.to_netcdf(path)


@pytest.mark.parametrize("variable,units,hours,expected", [
    ("pre", "mm", 24, "mHM ready"), ("tp", "m", 1, "ERA5Land")])
def test_glob_scan_detects_metadata_and_ignores_unmatched_files(tmp_path, variable, units, hours, expected):
    precipitation(tmp_path / "wanted.nc", variable=variable, units=units, hours=hours)
    (tmp_path / "unrelated.nc").write_bytes(b"not a NetCDF")
    metadata = inspect_meteo_folder(MeteoFolderSpec("pre", tmp_path, unit=units, temporal_resolution="Hourly" if hours == 1 else "Daily", pattern="wanted*.nc"))
    assert metadata.source == "auto"
    assert [p.name for p in metadata.files] == ["wanted.nc"]
    assert metadata.start_time.startswith("2001-01-01")
    assert metadata.resolution == 1


@pytest.mark.parametrize("units,hours", [("kg", 24), ("m", 24), ("mm", 1)])
def test_ambiguous_or_unsupported_metadata_is_not_guessed(tmp_path, units, hours):
    precipitation(tmp_path / "pre.nc", units=units, hours=hours)
    with pytest.raises(ValueError, match="supported unit|metadata unit|continuous"):
        inspect_meteo_folder(MeteoFolderSpec("pre", tmp_path, unit="mm"))


def test_changed_glob_never_reuses_another_inspection(tmp_path):
    precipitation(tmp_path / "a.nc")
    precipitation(tmp_path / "b.nc", start="2002-01-01")
    a = inspect_meteo_folder_cached(tmp_path, MeteoFolderSpec("pre", tmp_path, unit="mm", pattern="a.nc"))
    b = inspect_meteo_folder_cached(tmp_path, MeteoFolderSpec("pre", tmp_path, unit="mm", pattern="b.nc"))
    assert a.start_time != b.start_time
    with pytest.raises(ValueError, match="glob"):
        inspect_meteo_folder(MeteoFolderSpec("pre", tmp_path, unit="mm", pattern="../*.nc"))


def test_periods_validate_and_reach_both_namelist_versions(tmp_path):
    from mhm_qgis.vSpecific import build_initial_values
    from types import SimpleNamespace
    values = dict(sim_start="2001-01-01T00:00", eval_start="2001-01-11T00:00", sim_end="2001-02-01T00:00")
    update_section(tmp_path, "time", values)
    dialog = SimpleNamespace(project_folder=tmp_path)
    v5 = build_initial_values("5.13", dialog)["main"]["time_periods"]
    assert v5["warming_Days"] == [10]
    assert v5["eval_Per"][0]["dStart"] == 11
    v6 = build_initial_values("6", dialog)["main"]["config_time"]
    assert v6["sim_start"] == [values["sim_start"]]
    with pytest.raises(ValueError, match="Dates must"):
        validate_times(dict(values, eval_start="2000-01-01"))
    with pytest.raises(ValueError, match="whole dates"):
        validate_times(dict(values, sim_start="2001-01-01T01:00"), "v5.13")
    with pytest.raises(ValueError, match="whole-hour"):
        validate_times(dict(values, sim_start="2001-01-01T00:30"))
