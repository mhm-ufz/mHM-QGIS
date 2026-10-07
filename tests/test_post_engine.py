"""Post P3: series, aggregation, water balance and metrics (Tier A engine)."""
import numpy as np
import pytest
from post_fixtures import make_run

from mhm_qgis.core.executions import post as post_exec
from mhm_qgis.core.post import (
    aggregate,
    balance,
    cache,
    catalog,
    metrics,
    runs,
    scan,
    series,
)

netCDF4 = pytest.importorskip("netCDF4")
V5 = {"directories_general": {"dir_out": ["output/"], "dirconfigout": "output/",
                              "mhm_file_restartout": ["restart/mHM_restart_001.nc"]},
      "directories_mhm": {"dir_precipitation": ["data/meteo/pre/"]},
      "mainconfig": {"write_restart": True, "resolution_hydrology": [2000.0]}}


def _scanned(folder, **run_options):
    truth = make_run(folder, **run_options)
    ref = runs.list_runs(folder)[0]
    scan.run_scan(post_exec.scan_job(ref, values=V5, series=("aET", "SM_Lall")))
    return truth, cache.folder(ref)


def _basin_mean(truth, field):
    area = truth.cell_area_km2 * truth.mask
    return (field * area).sum(axis=(-2, -1)) / area.sum()


def _forcing_minus_truth(truth):
    """P from the L2 forcing differs from the fine P the fixture balanced (F10)."""
    l1 = truth.p[:, 2 * (np.arange(truth.p.shape[1]) // 2)][:, :, 2 * (np.arange(truth.p.shape[2]) // 2)]
    return l1 - truth.p


def test_daily_balance_is_exact_with_model_step_output(tmp_path):
    truth, root = _scanned(tmp_path)
    frame = balance.basin(root, exact=True)
    expected = _basin_mean(truth, (truth.loss + _forcing_minus_truth(truth))[1:].sum(0))
    assert frame["R"].iloc[0] == pytest.approx(expected, rel=1e-6)
    assert frame["bound"].iloc[0] == 0
    assert frame["days"].iloc[0] == 61
    assert frame.attrs["notes"] == []
    maps = balance.cell_maps(root, exact=True)
    cell_expected = (truth.loss + _forcing_minus_truth(truth))[1:].sum(0)
    np.testing.assert_allclose(maps["R"][truth.mask], cell_expected[truth.mask], rtol=1e-6)
    assert not maps["Q/P > 1"][truth.mask].any()


def test_monthly_balance_closes_within_the_averaging_bound(tmp_path):
    truth, root = _scanned(tmp_path, days=120, step="month")
    frame = balance.basin(root)
    (a, _), (_, end) = truth.intervals[1], truth.intervals[-1]
    loss = _basin_mean(truth, (truth.loss + _forcing_minus_truth(truth))[a:end].sum(0))
    stored = _basin_mean(truth, truth.storage)
    exact_ds = stored[end] - stored[a]
    assert abs(frame["dS"].iloc[0] - exact_ds) <= frame["bound"].iloc[0]
    assert abs(frame["R"].iloc[0] - loss) <= frame["bound"].iloc[0]
    yearly = balance.per_year(balance.basin(root, freq="month"))
    assert len(yearly) == 3  # February, March and April (January is interval 1)
    assert balance.basin(root, freq="month")["P"].sum() == pytest.approx(frame["P"].iloc[0])


def test_aggregation_follows_the_data_model(tmp_path):
    truth, root = _scanned(tmp_path, days=62)
    aet = series.basin(root, "aET")
    monthly = aggregate.temporal(root, "aET", "sum", "month")
    assert monthly.iloc[0] == pytest.approx(aet.iloc[:31].sum())
    assert np.isnan(monthly.iloc[2])  # March: two days only
    rate = aggregate.temporal(root, "aET", "mean", "month")
    assert rate.iloc[1] == pytest.approx(aet.iloc[31:60].sum() / 29)
    with pytest.raises(ValueError, match="is a state: sum is meaningless"):
        aggregate.temporal(root, "snowpack", "sum", "month")
    with pytest.raises(ValueError, match="is a state"):
        aggregate.statistic(root, "snowpack", "sum")
    cell = series.cell(root, "aET", 1, 1)
    with netCDF4.Dataset(truth.files["mhm"]) as handle:
        np.testing.assert_allclose(cell, handle["aET"][:, 1, 1])
    assert series.cell(root, "aET", 0, 5) is None  # outside the domain
    assert series.cell(root, "Q", 1, 1) is None  # no series cache for Q


def test_coarser_output_refuses_finer_aggregation(tmp_path):
    _, root = _scanned(tmp_path, days=90, step="month")
    with pytest.raises(ValueError, match="coarser than a day"):
        aggregate.temporal(root, "aET", "sum", "day")


def test_statistic_maps(tmp_path):
    truth, root = _scanned(tmp_path, days=62)
    with netCDF4.Dataset(truth.files["mhm"]) as handle:
        aet = np.ma.filled(handle["aET"][:], np.nan)
    mask = truth.mask
    np.testing.assert_allclose(aggregate.statistic(root, "aET", "sum")[mask], aet.sum(0)[mask])
    np.testing.assert_allclose(aggregate.statistic(root, "aET", "mean")[mask], aet.mean(0)[mask])
    january = ("2000-01-01", "2000-02-01")
    np.testing.assert_allclose(aggregate.statistic(root, "aET", "sum", january)[mask], aet[:31].sum(0)[mask],
                               rtol=1e-6)
    np.testing.assert_allclose(aggregate.statistic(root, "aET", "percentile", q=50)[mask],
                               np.median(aet, 0)[mask], rtol=1e-6)
    odd = ("2000-01-05", "2000-01-20")
    assert aggregate.statistic(root, "aET", "sum", odd) is None  # needs a derive pass
    out = tmp_path / "derived.npy"
    scan.derive({"what": "statistic", "path": str(truth.files["mhm"]), "variable": "aET", "stat": "max",
                 "kind": "flux", "period": odd, "out": str(out)})
    np.testing.assert_allclose(np.load(out)[mask], aet[4:19].max(0)[mask])


def test_metrics_against_hand_values():
    obs, sim = np.array([1.0, 2, 3, 4]), np.array([2.0, 2, 3, 5])
    assert metrics.nse(sim, obs) == pytest.approx(1 - 2 / 5)
    assert metrics.pbias(sim, obs) == pytest.approx(20)
    assert metrics.rmse(sim, obs) == pytest.approx(np.sqrt(0.5))
    assert metrics.mae(sim, obs) == pytest.approx(0.5)
    assert metrics.volume_error(sim, obs) == pytest.approx(0.2)
    assert metrics.nse(obs, obs) == 1
    assert metrics.nse(np.full(4, obs.mean()), obs) == 0
    r = np.corrcoef(sim, obs)[0, 1]
    hand_kge = 1 - np.sqrt((r - 1) ** 2 + (sim.std() / obs.std() - 1) ** 2 + (sim.mean() / obs.mean() - 1) ** 2)
    assert metrics.kge(sim, obs)[0] == pytest.approx(hand_kge)
    from mhm_tools.common.metrics.kge import calculate_kling_gupta_efficiency

    assert metrics.kge(sim, obs) == calculate_kling_gupta_efficiency(sim, obs)
    assert metrics.kge(obs, obs)[0] == 1
    cv = (sim.std() / sim.mean()) / (obs.std() / obs.mean())
    assert metrics.kge_2012(sim, obs) == pytest.approx(1 - np.sqrt((r - 1) ** 2 + (1.2 - 1) ** 2 + (cv - 1) ** 2))
    eps = 0.01 * obs.mean()
    assert metrics.log_nse(sim, obs) == pytest.approx(metrics.nse(np.log(sim + eps), np.log(obs + eps)))
    assert metrics.peak_magnitude_error(sim, obs) == pytest.approx(0.25)
    assert metrics.peak_timing_error([1, 5, 2], [1, 2, 5], np.arange(3)) == -1
    short = metrics.evaluate(sim, obs)
    assert np.isnan(short["NSE"])
    assert "4 paired values" in short["reason"]
    full = metrics.evaluate(np.r_[sim, np.nan, sim], np.r_[obs, 3.0, obs], min_pairs=8)
    assert full["pairs"] == 8
    assert full["NSE"] == pytest.approx(0.6)


def test_gauge_series_masks_missing_observations(tmp_path):
    truth = make_run(tmp_path, gauges=(398,))
    run = catalog.open_run(runs.list_runs(tmp_path)[0], V5)
    frame = series.gauge(run, 398)
    assert np.isnan(frame["obs"].iloc[::7]).all()
    assert frame.index[0] == np.datetime64("2000-01-01")
    q = (truth.q * truth.mask * truth.cell_area_km2 * 1e6).sum(axis=(1, 2)) / 1000 / 86400
    np.testing.assert_allclose(frame["sim"], q)
    with pytest.raises(ValueError, match="not in"):
        series.gauge(run, 7)
