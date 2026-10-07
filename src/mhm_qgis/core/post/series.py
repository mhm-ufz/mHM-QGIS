"""Time series of a run: cells and basins from the scan cache, gauges from the files.

Cell and basin series read only `.npy` files of the cache (`cache.folder(ref)`), so
the GUI thread may call them. Gauge series read netCDF: coordinator tasks only.
Every series is indexed by its intervals' start, values per interval (F5).
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from . import cache
from .catalog import read_bounds


def bounds(root, file="mhm") -> np.ndarray:
    """(start, end) of every output interval, as scanned."""
    return np.load(Path(root) / f"bounds_{file}.npy")


def _select(intervals, values, period):
    keep = np.ones(len(intervals), bool)
    if period is not None:
        keep = (intervals[:, 0] >= np.datetime64(period[0])) & (intervals[:, 1] <= np.datetime64(period[1]))
    return pd.Series(np.asarray(values, "f8")[keep], index=pd.DatetimeIndex(intervals[keep, 0]))


def cell(root, variable, iy, ix, period=None) -> pd.Series | None:
    """One cell's series from the series cache; None without a cache or outside the domain
    (the caller then schedules `derive("cell", ...)`)."""
    root = Path(root)
    path = root / "series" / f"{variable}.npy"
    if not path.is_file():
        return None
    cells = np.load(root / "cells_mhm.npy")
    hit = np.flatnonzero((cells[0] == iy) & (cells[1] == ix))
    if not hit.size:
        return None
    return _select(bounds(root), np.load(path, mmap_mode="r")[hit[0]], period).rename(variable)


def basin(root, variable, name="domain", period=None) -> pd.Series | None:
    """A basin's weighted series (F8 applied in the scan); None when not scanned."""
    info = cache.load_index(root)["variables"].get(variable)
    if not info or name not in info.get("basins", ()):
        return None
    row = np.load(Path(root) / "basins" / f"{variable}.npy", mmap_mode="r")[info["basins"].index(name)]
    return _select(bounds(root), row, period).rename(variable)


def precipitation(root, name="domain", period=None) -> pd.Series | None:
    """Forcing P per output interval (F10), as the scan built it."""
    return basin(root, "pre", name, period)


def gauge(run, gauge_id, period=None) -> pd.DataFrame:
    """Observed and simulated discharge (m³ s⁻¹) at a gauge; gaps are NaN.

    v5.13: `Qobs_`/`Qsim_<id>` in `discharge.nc`. v6: simulated `discharge(station, time)`
    from the POI file, observed from the gauge input file the plugin prepared (P0)."""
    import netCDF4

    path = run.files.get("discharge")
    if path is None or not path.is_file():
        raise ValueError("This run wrote no gauge discharge file.")
    intervals = read_bounds(path)
    with netCDF4.Dataset(path) as handle:
        if "station" in handle.dimensions:
            stations = [int(value) for value in handle["station"][:]]
            if int(gauge_id) not in stations:
                raise ValueError(f"Gauge {gauge_id} is not in {path.name}.")
            sim = np.ma.filled(np.ma.asarray(handle["discharge"][stations.index(int(gauge_id))], "f8"), np.nan)
            obs = _observed(run, gauge_id, intervals)
        else:
            names = [f"Q{kind}_{int(gauge_id):010d}" for kind in ("obs", "sim")]
            if names[1] not in handle.variables:
                raise ValueError(f"Gauge {gauge_id} is not in {path.name}.")
            obs, sim = (np.ma.filled(np.ma.asarray(handle[name][:], "f8"), np.nan) if name in handle.variables
                        else np.full(len(intervals), np.nan) for name in names)
    obs[obs == -9999] = np.nan
    sim[sim == -9999] = np.nan
    frame = pd.DataFrame({"obs": obs, "sim": sim}, index=pd.DatetimeIndex(intervals[:, 0]))
    if period is not None:
        keep = (intervals[:, 0] >= np.datetime64(period[0])) & (intervals[:, 1] <= np.datetime64(period[1]))
        frame = frame[keep]
    return frame


def _observed(run, gauge_id, intervals):
    """Observations from the prepared gauge file, matched to the intervals' starts."""
    from ..handlers.file.discharge import records_from_mhm_file
    from ..handlers.state import domain_state

    record = next((r for r in domain_state.gauge_records(domain_state.load_state(run.ref.domain))
                   if r["gauge_id"] == int(gauge_id)), None)
    if record is None or not record["gauge_path"]:
        return np.full(len(intervals), np.nan)
    records = records_from_mhm_file(run.ref.domain / record["gauge_path"])
    observed = pd.Series({np.datetime64(r.timestamp, "s"): r.value for r in records})
    return observed.reindex(pd.DatetimeIndex(intervals[:, 0])).to_numpy("f8")
