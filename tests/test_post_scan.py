"""Post P2: the scan's products equal brute force; disk check, cancel, progress."""
import collections
import json
import subprocess

import numpy as np
import pytest
from post_fixtures import make_run

from mhm_qgis.core.executions import post as post_exec
from mhm_qgis.core.executions.morphology import commands
from mhm_qgis.core.post import cache, runs, scan

netCDF4 = pytest.importorskip("netCDF4")
V5 = {"directories_general": {"dir_out": ["output/"], "dirconfigout": "output/",
                              "mhm_file_restartout": ["restart/mHM_restart_001.nc"]},
      "directories_mhm": {"dir_precipitation": ["data/meteo/pre/"]},
      "mainconfig": {"write_restart": True, "resolution_hydrology": [2000.0]}}


class Task:
    def __init__(self, cancel_after=None):
        self.polls, self.cancel_after, self.progress = 0, cancel_after, []

    def isCanceled(self):
        self.polls += 1
        return self.cancel_after is not None and self.polls > self.cancel_after

    def setProgress(self, value):
        self.progress.append(value)


def _job(folder, **options):
    ref = runs.list_runs(folder)[0]
    return ref, post_exec.scan_job(ref, values=V5, **options)


def _file(truth, name):
    with netCDF4.Dataset(truth.files["mhm"]) as handle:
        return np.ma.filled(np.ma.asarray(handle[name][:], "f8"), np.nan)


def test_products_equal_brute_force(tmp_path):
    truth = make_run(tmp_path, days=62)
    ref, job = _job(tmp_path, series=("aET", "pre"), budget_mb=0.0002)  # 1-step blocks
    seen = []
    summary = scan.run_scan(job, seen.append)
    assert seen[-1] == 100
    assert seen == sorted(seen)
    root, mask, area = cache.folder(ref), truth.mask, truth.cell_area_km2
    cells = np.load(root / "cells_mhm.npy")
    aet = _file(truth, "aET")
    np.testing.assert_allclose(np.load(root / "series" / "aET.npy"), aet[:, cells[0], cells[1]].T, rtol=1e-6)
    stats = np.load(root / "stats" / "aET.npz")
    np.testing.assert_allclose(stats["sum"][mask], np.nansum(aet, 0)[mask])
    np.testing.assert_allclose(stats["max"][mask], aet[:, mask].max(0))
    assert np.isnan(stats["sum"][~mask]).all()
    np.testing.assert_allclose(np.load(root / "basins" / "aET.npy")[0],
                               (aet * area)[:, mask].sum(1) / area[mask].sum())
    monthly = np.load(root / "monthly" / "aET.npy")
    assert summary["aET"]["months"] == ["2000-01", "2000-02", "2000-03"]
    np.testing.assert_allclose(monthly[0][mask], aet[:31].sum(0)[mask], rtol=1e-6)
    assert np.isnan(monthly[2]).all()  # March holds two days only
    np.testing.assert_allclose(np.load(root / "annual" / "aET_max.npy")[0][mask], aet[:, mask].max(0), rtol=1e-6)
    low, high = summary["aET"]["range"]
    assert np.nanmin(aet) <= low < high <= np.nanmax(aet)
    sealed = np.where(mask, truth.f_sealed, 0)
    swc, sm = _file(truth, "SWC_L01"), _file(truth, "SM_Lall")
    pervious = area * (1 - sealed)
    np.testing.assert_allclose(np.load(root / "basins" / "SWC_L01.npy")[0],  # an amount: per basin area
                               (swc * pervious)[:, mask].sum(1) / area[mask].sum())
    np.testing.assert_allclose(np.load(root / "basins" / "SM_Lall.npy")[0],  # an intensity: per pervious area
                               (sm * pervious)[:, mask].sum(1) / pervious[mask].sum())


def test_precipitation_comes_from_the_l2_forcing(tmp_path):
    truth = make_run(tmp_path, days=90, step="month")
    ref, job = _job(tmp_path, series=("pre",))
    summary = scan.run_scan(job)
    assert summary["pre"]["incomplete"] == 0
    l1 = truth.p[:, 2 * (np.arange(4) // 2)][:, :, 2 * (np.arange(6) // 2)]  # each L1 cell takes its L2 cell
    expected = np.stack([l1[a:b].sum(0) for a, b in truth.intervals])
    cells = np.load(cache.folder(ref) / "cells_mhm.npy")
    np.testing.assert_allclose(np.load(cache.folder(ref) / "series" / "pre.npy"), expected[:, cells[0], cells[1]].T)
    domain = np.load(cache.folder(ref) / "basins" / "pre.npy")[0]
    area = truth.cell_area_km2 * truth.mask
    np.testing.assert_allclose(domain, (expected * area).sum((1, 2)) / area.sum())


def test_scan_runs_in_the_worker_and_is_fingerprinted(tmp_path):
    make_run(tmp_path)
    ref, job = _job(tmp_path, variables=("aET", "Q"), series=("Q",))
    task = Task()
    summary = post_exec.scan(task, job)
    assert set(summary) == {"aET", "Q", "pre"}
    assert task.progress[-1] == 100
    index = json.loads((cache.folder(ref) / "index.json").read_text())
    assert index["variables"]["Q"]["fingerprint"] == job["fingerprints"]["Q"]
    assert index["domain_source"] == "restart"
    assert _job(tmp_path, variables=("aET", "Q"), series=("Q",))[1]["variables"] == []
    (tmp_path / "output" / "mHM_Fluxes_States.nc").touch()
    assert len(_job(tmp_path, variables=("aET", "Q"), series=("Q",))[1]["variables"]) == 3


def test_disk_check_refuses_a_cache_that_does_not_fit(tmp_path, monkeypatch):
    make_run(tmp_path)
    usage = collections.namedtuple("usage", "total used free")
    monkeypatch.setattr(cache.shutil, "disk_usage", lambda _path: usage(100, 90, 10))
    with pytest.raises(ValueError, match="needs"):
        _job(tmp_path)
    assert _job(tmp_path, series=())[1]["series_bytes"] == 0


def test_cancel_kills_the_child(tmp_path, monkeypatch):
    make_run(tmp_path)
    _, job = _job(tmp_path)
    started = []

    class Popen(subprocess.Popen):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            started.append(self)

    monkeypatch.setattr(commands.subprocess, "Popen", Popen)
    with pytest.raises(RuntimeError, match="cancelled"):
        post_exec.scan(Task(cancel_after=1), job)
    assert started[0].poll() is not None
    assert started[0].returncode != 0


def test_derive_reads_one_cell(tmp_path):
    truth = make_run(tmp_path)
    values = post_exec.derive(Task(), {"what": "cell", "path": str(truth.files["mhm"]), "variable": "Q",
                                       "iy": 1, "ix": 2}, tmp_path)
    np.testing.assert_allclose(values, _file(truth, "Q")[:, 1, 2])
