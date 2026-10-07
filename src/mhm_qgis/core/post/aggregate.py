"""Temporal aggregation and statistics, with the data model's semantics (F3, F4).

Fluxes are sums per interval: a sum over a period is exact, and a mean is a rate in
mm per day from `time_bnds`. States are interval means: mean (time-weighted), min
and max only; anything else is refused. Calendar groups (day, month, year) that the
run covers only in part are NaN; a request finer than the output step is refused.
Maps come from the scan's products; what they cannot answer returns None, and the
caller runs `derive("statistic", ...)` in the native worker.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from . import cache, series
from .scan import calendar_days

UNITS = {"day": "D", "month": "M", "year": "Y"}
MIN_DAYS = {"day": 1, "month": 28, "year": 365}


def semantics(root, variable) -> dict:
    info = cache.load_index(root)["variables"].get(variable)
    if info is None:
        raise ValueError(f"{variable} is not scanned yet.")
    return info


def check(variable, info, how, allowed=None) -> None:
    """Refuse a statistic the data model does not allow for this variable."""
    allowed = info["aggregate"] if allowed is None else allowed
    if how in allowed:
        return
    if info["kind"] == "state" and how == "sum":
        raise ValueError(f"{variable} is a state: sum is meaningless")
    raise ValueError(f"{variable} allows {', '.join(allowed)}, not {how}.")


def map_statistics(info) -> tuple:
    """Every map statistic; sums and anomalies (of monthly totals) only for fluxes."""
    return ("mean", "std", "min", "max", "percentile") + (("sum", "anomaly") if info["kind"] == "flux" else ())


def _period_mask(intervals, period):
    if period is None:
        return np.ones(len(intervals), bool)
    return (intervals[:, 0] >= np.datetime64(period[0])) & (intervals[:, 1] <= np.datetime64(period[1]))


def temporal(root, variable, how, freq="native", *, basin="domain", cell=None, period=None):
    """A basin's (or a cell's) series aggregated to `freq` (native, day, month, year, period)."""
    info = semantics(root, variable)
    check(variable, info, how)
    values = series.cell(root, variable, *cell, period) if cell else series.basin(root, variable, basin, period)
    if values is None:
        return None
    intervals = series.bounds(root)[_period_mask(series.bounds(root), period)]
    days = (intervals[:, 1] - intervals[:, 0]) / np.timedelta64(1, "D")
    flux = info["kind"] == "flux"
    if freq == "native":
        return values / days if flux and how == "mean" else values
    if freq != "period" and MIN_DAYS[freq] < np.median(days) * 0.99:
        raise ValueError(f"The output step ({np.median(days):g} days) is coarser than a {freq}.")
    starts = intervals[:, 0]
    labels = np.zeros(len(starts), int) if freq == "period" else starts.astype(f"datetime64[{UNITS[freq]}]")
    frame = pd.DataFrame({"x": values.to_numpy(), "d": days, "xd": values.to_numpy() * days,
                          "nan": np.isnan(values.to_numpy())}, index=labels)
    grouped = frame.groupby(level=0)
    if how == "sum":
        result = grouped["x"].sum()
    elif how == "mean":
        result = grouped["x"].sum() / grouped["d"].sum() if flux else grouped["xd"].sum() / grouped["d"].sum()
    else:
        result = getattr(grouped["x"], how)()
    result[grouped["nan"].sum() > 0] = np.nan
    if freq != "period":
        group_days = (grouped["d"].sum()).to_numpy()
        labels = np.array(result.index, dtype=f"datetime64[{UNITS[freq]}]")
        result[group_days < calendar_days(labels) - 1e-9] = np.nan  # a partly covered day, month or year
        result.index = pd.DatetimeIndex(labels.astype("datetime64[s]"))
    return result.rename(variable)


def statistic(root, variable, stat, period=None, q=None):  # noqa: PLR0911
    """A (ny, nx) map, or None when it needs a `derive("statistic")` pass.

    Whole-run statistics come from the scan's running sums; flux sums and means over
    whole months from the monthly totals; percentiles from the series cache (a full
    cache read: coordinator task); anomaly is the period's monthly mean minus the
    whole run's mean of the same calendar months (fluxes)."""
    root = Path(root)
    info = semantics(root, variable)
    check(variable, info, stat, map_statistics(info))
    intervals = series.bounds(root)
    keep = _period_mask(intervals, period)
    days = (intervals[:, 1] - intervals[:, 0]) / np.timedelta64(1, "D")
    flux = info["kind"] == "flux"
    if stat == "percentile":
        path = root / "series" / f"{variable}.npy"
        if not path.is_file():
            return None
        cells = np.load(root / "cells_mhm.npy")
        values = np.nanpercentile(np.load(path, mmap_mode="r")[:, keep], q, axis=1)
        return _grid(root, cells, values)
    if period is None or keep.all():
        stats = np.load(root / "stats" / f"{variable}.npz")
        with np.errstate(invalid="ignore", divide="ignore"):
            mean = stats["sum"] / stats["n"]
            maps = {"sum": stats["sum"], "min": stats["min"], "max": stats["max"],
                    "mean": stats["sum"] / days.sum() if flux else mean,
                    "std": np.sqrt(np.maximum(stats["sumsq"] / stats["n"] - mean ** 2, 0))}
        if stat in maps:
            return maps[stat]
    monthly = root / "monthly" / f"{variable}.npy"
    if not flux or not monthly.is_file() or stat not in ("sum", "mean", "anomaly"):
        return None
    labels = np.array(info["months"], dtype="datetime64[M]")
    totals = np.load(monthly)
    first, last = (intervals[keep, 0].min(), intervals[keep, 1].max()) if keep.any() else (None, None)
    if first is None or first != first.astype("datetime64[M]") or last != last.astype("datetime64[M]"):
        return None  # not whole months
    chosen = (labels >= first.astype("datetime64[M]")) & (labels < last.astype("datetime64[M]"))
    if stat == "sum":
        return totals[chosen].sum(axis=0)
    month_days = calendar_days(labels)
    if stat == "mean":
        return totals[chosen].sum(axis=0) / month_days[chosen].sum()
    calendar = labels.astype(int) % 12
    climate = {m: np.nanmean(totals[calendar == m], axis=0) for m in np.unique(calendar[chosen])}
    return np.nanmean([totals[i] - climate[calendar[i]] for i in np.flatnonzero(chosen)], axis=0)


def _grid(root, cells, values):
    shape = (len(np.load(Path(root) / "y.npy")), len(np.load(Path(root) / "x.npy")))
    out = np.full(shape, np.nan)
    out[cells[0], cells[1]] = values
    return out
