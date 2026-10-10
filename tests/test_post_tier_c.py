"""Post P7: comparison, extremes, drought, checks, Why here and the summary (Tier C)."""
import numpy as np
import pandas as pd
import pytest
from post_fixtures import make_run, write_mrm

from mhm_qgis.core.executions import post as post_exec
from mhm_qgis.core.post import (
    balance,
    cache,
    catalog,
    checks,
    compare,
    drought,
    explain,
    extremes,
    grid,
    runs,
    scan,
)

netCDF4 = pytest.importorskip("netCDF4")
V5 = {"directories_general": {"dir_out": ["output/"], "dirconfigout": "output/",
                              "mhm_file_restartout": ["restart/mHM_restart_001.nc"]},
      "directories_mhm": {"dir_precipitation": ["data/meteo/pre/"]},
      "mainconfig": {"write_restart": True, "resolution_hydrology": [2000.0]}}


def _scanned(folder, **options):
    truth = make_run(folder, **options)
    ref = runs.list_runs(folder)[0]
    scan.run_scan(post_exec.scan_job(ref, values=V5, series=("aET", "snowpack", "SM_Lall")))
    return truth, ref, cache.folder(ref)


def test_compare_refuses_other_grids_and_differences_runs(tmp_path):
    _, _, a = _scanned(tmp_path / "a")
    _, _, b = _scanned(tmp_path / "b", seed=1)
    _, _, other = _scanned(tmp_path / "c", shape=(5, 6))
    assert compare.refusal(a, other) == "The runs are on different grids."
    _, _, late = _scanned(tmp_path / "d", days=62)
    assert compare.refusal(a, b) == ""
    first, second, delta, percent = compare.maps(a, b, "aET", "sum")
    np.testing.assert_allclose(delta, second - first)
    assert np.isfinite(percent[np.isfinite(first)]).all()
    np.testing.assert_allclose(compare.difference(np.array([2.0, 0.0]), np.array([3.0, 1.0]))[1], [50.0, np.nan])
    pair = compare.series_pair(a, b, "aET")
    assert {"A", "B", "B - A"} <= set(pair.columns)
    deltas = compare.score_deltas({"NSE": 0.5, "note": "x"}, {"NSE": 0.7})
    assert deltas.loc["NSE", "B - A"] == pytest.approx(0.2)
    assert compare.refusal(a, late) == ""  # overlapping periods compare


def test_extremes_and_return_periods():
    days = pd.date_range("2000-01-01", "2009-12-31", freq="D")
    q = pd.Series(1 + np.sin(2 * np.pi * days.dayofyear / 365) ** 2 * (days.year - 1999), index=days)
    maxima = extremes.annual(q)
    assert maxima.index.tolist() == list(range(2000, 2010))
    assert maxima.iloc[-1] == pytest.approx(11.0, rel=1e-3)
    assert extremes.am7(q).iloc[0] == pytest.approx(1.0, abs=0.01)
    assert (extremes.pot_counts(q) >= 0).all()
    x, period = extremes.plotting_positions([3.0, 1.0, 2.0])
    assert x.tolist() == [3.0, 2.0, 1.0]
    assert period[0] == pytest.approx(3.12 / 0.56)
    l1, l2, t3 = extremes.lmoments([1, 2, 3, 4, 5])
    assert (l1, l2, t3) == pytest.approx((3.0, 1.0, 0.0))
    rng = np.random.default_rng(3)
    truth = (100.0, 20.0, 0.1)
    sample = truth[0] + truth[1] / truth[2] * (1 - (-np.log(rng.random(20000))) ** truth[2])
    fitted = extremes.gev_fit(sample)
    assert fitted == pytest.approx(truth, rel=0.05, abs=0.02)  # L-moment sampling error on k
    assert extremes.gev_quantile(truth, 100) == pytest.approx(
        truth[0] + truth[1] / truth[2] * (1 - (-np.log(0.99)) ** truth[2]))
    assert extremes.return_levels(maxima.iloc[:5]).empty  # under ten years: no GEV
    assert len(extremes.return_levels(maxima)) == len(extremes.RETURN_PERIODS)


def test_drought_events_and_smi():
    days = pd.date_range("2000-01-01", "2004-12-31", freq="D")
    q = pd.Series(10.0, index=days)
    q.loc["2003-06-01":"2003-06-20"] = 1.0
    q.loc["2003-06-25":"2003-06-30"] = 1.0  # within 10 days: pooled with the first spell
    level = drought.threshold(q) * 0 + 5.0
    found = drought.events(q, level)
    assert len(found) == 1
    assert found["duration (days)"].iloc[0] == 30
    assert found["deficit (m³)"].iloc[0] == pytest.approx(26 * 4.0 * 86400)
    labels = np.arange(np.datetime64("2000-01"), np.datetime64("2003-01"))
    values = np.arange(36, dtype=float).reshape(36, 1)
    index = drought.smi(values, labels)
    assert index[:12, 0] == pytest.approx(np.full(12, 0.5 / 3))  # each first year is the driest
    area, longest, frequency = drought.smi_products(index, np.array([True]))
    assert longest[0] == 12
    assert frequency[0] == pytest.approx(1 / 3)
    assert area[:12].tolist() == [1.0] * 12


def test_checks_find_planted_problems(tmp_path):
    truth = make_run(tmp_path, days=400)
    with netCDF4.Dataset(truth.files["mhm"], "a") as handle:
        values = handle["snowpack"][:]
        values[:, 1, 1] = -5.0
        handle["snowpack"][:] = values
        handle["recharge"][:] = np.where(np.ma.getmaskarray(handle["recharge"][:]), handle["recharge"][:], 0.0)
    ref = runs.list_runs(tmp_path)[0]
    scan.run_scan(post_exec.scan_job(ref, values=V5, series=("snowpack",)))
    found = {finding.rule: finding for finding in checks.run(cache.folder(ref), gauge_scores={398: {"NSE": -0.3}})}
    assert found["negative_storage"].cells == [(1, 1)]
    assert "recharge is written but zero everywhere" in found["zero_flux"].message
    assert found["gauge_nse"].gauge == 398
    assert "et_over_pet" not in found  # ET never exceeds PET in the fixture


def test_why_here_explains_a_cell(tmp_path):
    truth, ref, root = _scanned(tmp_path, days=400)
    run = catalog.open_run(ref, V5)
    axes = grid.axes(truth.files["mhm"])
    found = explain.cell(root, run, axes, 1, 1)
    years = (400 / 365.25)
    assert found["values"]["ET"] == pytest.approx(truth.et[:, 1, 1].sum() / years, rel=1e-6)
    assert set(found["z"]) >= {"P", "ET", "Q"}
    assert sum(found["shares"].values()) == pytest.approx(1.0)  # QD + QIf + QIs + QB = Q in the fixture
    rule = [{"when": {"QB_share": "> 0.3"}, "says": "baseflow"}, {"when": {"P": "> 99"}, "says": "never"}]
    assert explain.phrases(found["z"], found["shares"], rule) == ["baseflow"]
    assert explain.phrases({}, {}, rule) == ["No dominant driver."]


def test_summary_has_a_row_per_basin(tmp_path):
    _, ref, root = _scanned(tmp_path, days=400)
    run = catalog.open_run(ref, V5)
    table = balance.summary(root, run, gauges=[398])
    assert list(table.index) == ["Domain", "Gauge 398"]
    assert np.isfinite(table.loc["Domain", "P"])
    assert np.isnan(table.loc["Gauge 398", "area (km²)"])  # no catchment mask: blank, not zero
    assert 0 < table.loc["Gauge 398", "observed (%)"] < 100


def test_bankfull_map_from_mrm(tmp_path):
    from mhm_qgis.externals.mhm_tools_handler import bankfull

    truth = make_run(tmp_path, days=1100)
    routed = write_mrm(truth, tmp_path / "output" / "mRM_Fluxes_States.nc")
    out = bankfull(str(routed), str(tmp_path / "bankfull.nc"))
    with netCDF4.Dataset(out) as handle:
        q = np.ma.filled(handle["Q_bkfl"][:], np.nan)
    assert np.isfinite(q[truth.mask]).all()
    assert (q[truth.mask] > 1.0).all()
