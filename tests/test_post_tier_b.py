"""Post P6: signatures, events, climatology and observation operators (Tier B)."""
import json

import numpy as np
import pandas as pd
import pytest
from post_fixtures import make_run, write_observation

from mhm_qgis.core.executions import post as post_exec
from mhm_qgis.core.post import (
    cache,
    climatology,
    events,
    observe,
    runs,
    scan,
    signatures,
)

netCDF4 = pytest.importorskip("netCDF4")
V5 = {"directories_general": {"dir_out": ["output/"], "dirconfigout": "output/",
                              "mhm_file_restartout": ["restart/mHM_restart_001.nc"]},
      "directories_mhm": {"dir_precipitation": ["data/meteo/pre/"]},
      "mainconfig": {"write_restart": True, "resolution_hydrology": [2000.0]}}
DAYS = pd.date_range("2000-01-01", periods=3 * 365, freq="D")


def test_signatures_against_hand_values():
    k = 20.0
    t = np.arange(400)
    recession = pd.Series(50 * np.exp(-t / k), index=pd.date_range("2000-01-01", periods=400, freq="D"))
    assert signatures.recession_constant(recession.to_numpy()) == pytest.approx(k, rel=1e-6)
    constant = np.full(100, 3.0)
    np.testing.assert_allclose(signatures.baseflow(constant), constant)
    p, flows = signatures.fdc([3, 1, 2])
    assert p.tolist() == [0.25, 0.5, 0.75]
    assert flows.tolist() == [3, 2, 1]
    q = pd.Series(np.full(len(DAYS), 1.0), index=DAYS)  # 1 m³/s over 86.4 km² is 1 mm/day
    found = signatures.signatures(q, p=pd.Series(2.0, index=DAYS), area_km2=86.4)
    assert found["Runoff ratio"] == pytest.approx(0.5)
    assert found["Baseflow index"] == pytest.approx(1.0)
    assert found["Zero-flow fraction"] == 0
    assert signatures.usable(q.resample("MS").mean()) == "Signatures need daily or finer discharge."
    table = signatures.compare({"Q5": 2.0}, {"Q5": 3.0})
    assert table.loc["Q5", "relative difference"] == pytest.approx(0.5)


def test_events_are_found_and_scored():
    base = np.ones(200)
    flow = base.copy()
    for peak in (50, 130):
        flow[peak - 3:peak + 4] += 20 * (1 - np.abs(np.arange(-3, 4)) / 4)
    frame = pd.DataFrame({"obs": flow, "sim": np.roll(flow, 1) * 1.1}, index=DAYS[:200])
    found = events.detect(flow)
    assert len(found) == 2
    assert all(start <= peak <= end for (start, end), peak in zip(found, (50, 130), strict=True))
    rain = pd.Series(0.0, index=frame.index)
    rain.iloc[[48, 128]] = 30.0
    table = events.table(frame, rain=rain, area_km2=100.0)
    assert table["timing error (days)"].tolist() == [1.0, 1.0]
    assert table["peak error (%)"].iloc[0] == pytest.approx(10.0, abs=0.5)
    assert (table["rainfall"] == 30.0).all()
    assert (table["runoff coefficient"] > 0).all()
    assert table["centroid lag (days)"].between(0, 5).all()  # rain two days before the peak
    no_obs = events.table(frame.assign(obs=np.nan))
    assert "observed peak" not in no_obs
    assert "timing error (days)" not in no_obs
    assert events.ranked(table, "observed peak").index[0] in (0, 1)


def test_climatology_and_anomalies(tmp_path):
    months = pd.date_range("2000-01-01", periods=36, freq="MS")
    series = pd.Series(10 + 5 * np.sin(2 * np.pi * (months.month - 1) / 12) + np.repeat([0, 1, -1], 12), index=months)
    stats = climatology.monthly(series)
    assert stats.loc[1, "count"] == 3
    assert stats.loc[1, "mean"] == pytest.approx(10.0)
    z = climatology.anomaly(series)
    assert z.groupby(z.index.month).mean().abs().max() < 1e-9
    assert climatology.usable(series.iloc[:6]) != ""
    make_run(tmp_path, days=400)
    ref = runs.list_runs(tmp_path)[0]
    scan.run_scan(post_exec.scan_job(ref, values=V5, series=()))
    mean, std = climatology.maps(cache.folder(ref), "aET")
    assert mean.shape[0] == 12
    assert np.isfinite(mean[0]).any()
    assert np.isnan(std[3]).all()  # one April in the run: no spread


def test_gridded_comparison_harmonises_time_space_and_units(tmp_path):
    truth = make_run(tmp_path, days=120, shape=(4, 6))
    write_observation(truth, tmp_path / "et.nc")
    out = observe.compare({"adapter": "et", "model": str(truth.files["mhm"]), "observation": str(tmp_path / "et.nc"),
                           "variable": "et", "out": str(tmp_path / "cmp"), "min_pairs": 2})
    maps = np.load(f"{out}/maps.npz")
    scores = json.loads((tmp_path / "cmp" / "scores.json").read_text())
    assert maps["bias"].shape == (2, 3)  # the coarser, observation grid
    finite = np.isfinite(maps["bias"])
    np.testing.assert_allclose(maps["bias"][finite], 0, atol=1e-3)
    assert np.nanmin(maps["KGE"]) > 0.999
    assert len(scores["windows"]) == 3  # April has 29 of 30 days: incomplete, dropped
    assert scores["basin"]["NSE"] == pytest.approx(1.0, abs=1e-6)
    assert scores["pattern_mean"]["SPAEF"] > 0.99
    write_observation(truth, tmp_path / "rate.nc", units="mm d-1", scale=0.0)
    with netCDF4.Dataset(tmp_path / "rate.nc", "a") as handle:  # monthly totals as daily rates
        days = (handle["time_bnds"][:, 1] - handle["time_bnds"][:, 0])[:, None, None]
        write_observation(truth, tmp_path / "plain.nc")
        with netCDF4.Dataset(tmp_path / "plain.nc") as plain:
            handle["et"][:] = plain["et"][:] / days
    observe.compare({"adapter": "et", "model": str(truth.files["mhm"]), "observation": str(tmp_path / "rate.nc"),
                     "variable": "et", "out": str(tmp_path / "rate"), "min_pairs": 2})
    np.testing.assert_allclose(np.load(tmp_path / "rate" / "maps.npz")["bias"][finite], 0, atol=1e-3)
    write_observation(truth, tmp_path / "odd.nc", units="furlongs")
    with pytest.raises(ValueError, match="units 'furlongs'"):
        observe.compare({"adapter": "et", "model": str(truth.files["mhm"]), "observation": str(tmp_path / "odd.nc"),
                         "variable": "et", "out": str(tmp_path / "odd")})


def test_soil_moisture_is_depth_weighted():
    weights = observe.soil_weights({"soildata": {"soil_depth": [300.0, 1000.0]}}, "v5.13", 1, 500.0)
    assert weights == pytest.approx({1: 1 / 500, 2: 200 / 700 / 500})
    v6 = observe.soil_weights({"config_mpr": {"soil_depth": [[300.0], [1000.0]]}}, "v6", 1, 100.0)
    assert v6 == pytest.approx({1: 1 / 300})
    with pytest.raises(ValueError, match="shallower"):
        observe.soil_weights({"soildata": {"soil_depth": [300.0]}}, "v5.13", 1, 500.0)
    with pytest.raises(ValueError, match="SWC_L02 is not written"):
        observe.depth_terms({"soildata": {"soil_depth": [300.0, 1000.0]}}, "v5.13", 1, 500.0, {"SWC_L01"})


def test_mhm_tools_window_resampler_averages_so_fluxes_use_our_own():
    """§7.8 check: resample_to_reference_windows averages over each window, right for
    states but not for mHM's summed fluxes (a month of 1 mm days must total 31 mm)."""
    xr = pytest.importorskip("xarray")
    from mhm_tools.common.time_utils import resample_to_reference_windows

    days = pd.date_range("2000-01-01", periods=59, freq="D")
    daily = xr.DataArray(np.ones(59), dims="time", coords={"time": days}).to_dataset(name="et")
    months = pd.to_datetime(["2000-01-01"])
    reference = xr.Dataset({"et": ("time", [31.0])}, coords={"time": months})
    reference["time_bnds"] = (("time", "bnds"), np.array([[np.datetime64("2000-01-01"), np.datetime64("2000-02-01")]]))
    reference["time"].attrs["bounds"] = "time_bnds"
    averaged, _ = resample_to_reference_windows(daily, reference)
    assert float(averaged["et"][0]) == pytest.approx(1.0)  # a mean, not the 31 mm total


def _mhm_tools_drops_last_month() -> bool:
    """mhm-tools 0.3 ends a monthly record at `Period.end_time + 1 ns`, which pandas 3
    (`end_time` in microseconds) leaves inside the month, so the last month counts as
    not covered. Remove with the mhm-tools fix (the next period's start)."""
    try:
        import xarray as xr
        from mhm_tools.common.time_utils import get_data_coverage
    except ImportError:
        return False
    stamps = pd.to_datetime(["2003-11-16", "2003-12-16"])
    return get_data_coverage(xr.DataArray([0.0, 0.0], {"time": stamps}, ("time",)))[1] < pd.Timestamp("2004-01-01")


@pytest.mark.xfail(_mhm_tools_drops_last_month(), raises=ValueError, strict=True,
                   reason="mhm-tools 0.3 under pandas 3 drops a record's last month")
def test_tws_anomaly_and_snow_cover_need_a_geographic_run(tmp_path, monkeypatch):
    monkeypatch.setenv("MPLBACKEND", "Agg")
    projected = make_run(tmp_path / "p", days=60)
    with pytest.raises(ValueError, match="lat/lon grids only"):
        observe.run_job({"adapter": "snow_cover", "geographic": False, "model": str(projected.files["mhm"]),
                         "observation": "x", "out": str(tmp_path / "o")})
    truth = make_run(tmp_path / "g", geographic=True, days=1461, step="month", shape=(8, 12))
    ref = runs.RunRef(tmp_path / "g", tmp_path / "g", tmp_path / "g", tmp_path / "g", "v5.13")
    values = {**V5, "mainconfig": {"write_restart": True, "resolution_hydrology": [1 / 120]}}
    scan.run_scan(post_exec.scan_job(ref, values=values, series=()))
    index = cache.load_index(cache.folder(ref))
    model = truth.files["mhm"]
    storages = ("interception", "snowpack", "SWC_L01", "SWC_L02", "unsatSTW", "satSTW")
    with netCDF4.Dataset(model) as source, netCDF4.Dataset(tmp_path / "grace.nc", "w") as target:
        for name in ("time", "lat", "lon"):
            target.createDimension(name, None if name == "time" else source.dimensions[name].size)
            var = target.createVariable(name, "f8", (name,))
            var.setncatts({k: source[name].getncattr(k) for k in source[name].ncattrs() if k != "bounds"})
            var[:] = source[name][:]
        target["time"][:] = source["time_bnds"][:, 0]
        target.createDimension("bnds", 2)
        target.createVariable("time_bnds", "f8", ("time", "bnds"))[:] = source["time_bnds"][:]
        target["time"].bounds = "time_bnds"
        # a raw storage reference, so mhm-tools builds both anomalies the same way
        total = target.createVariable("storage", "f4", ("time", "lat", "lon"), fill_value=np.float32(np.nan))
        total.units = "mm"
        total[:] = sum(np.ma.filled(source[name][:], np.nan) for name in storages)
    files = observe.run_job({"adapter": "tws_anomaly", "geographic": True, "cache": str(cache.folder(ref)),
                             "model": str(model), "observation": str(tmp_path / "grace.nc"),
                             "variable": "storage", "out": str(tmp_path / "twsa"),
                             "terms": observe.tws_terms(index)})
    with netCDF4.Dataset(files["metrics"]) as handle:  # our F8-weighted TWS equals the storage sum
        assert np.nanmin(np.ma.filled(handle["gamma"][:], np.nan)) > 0.999
        assert np.nanmax(np.ma.filled(handle["rmse"][:], np.nan)) < 1e-2
