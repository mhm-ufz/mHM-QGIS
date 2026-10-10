"""Droughts (Tier C, §7.11): streamflow below a seasonal threshold, and the soil
moisture index (SMI) of the German Drought Monitor.

Streamflow: the threshold-level method with a day-of-year Q80 (the flow exceeded
80 % of the time, from a ±15-day window over the reference years); events are days
below it, pooled when less than 10 days apart, at least 5 days long. SMI: the
empirical CDF of monthly SM_Lall per calendar month and cell; drought below 0.2.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

WINDOW = 31
POOL_DAYS = 10
MIN_DURATION = 5
SMI_DROUGHT = 0.2


def threshold(q: pd.Series, exceeded=80, reference=None) -> pd.Series:
    """The day-of-year threshold (flow exceeded `exceeded` % of the time)."""
    flow = q.dropna()
    if reference is not None:
        flow = flow[(flow.index >= pd.Timestamp(reference[0])) & (flow.index < pd.Timestamp(reference[1]))]
    doy = np.minimum(flow.index.dayofyear.to_numpy(), 365)
    values = flow.to_numpy()
    levels = np.empty(365)
    for day in range(1, 366):
        distance = np.abs((doy - day + 182) % 365 - 182)
        levels[day - 1] = np.percentile(values[distance <= WINDOW // 2], 100 - exceeded)
    return pd.Series(levels[np.minimum(q.index.dayofyear.to_numpy(), 365) - 1], index=q.index)


def events(q: pd.Series, level: pd.Series) -> pd.DataFrame:
    """Drought events: start, end, duration (days), deficit (m³) and intensity (m³ s⁻¹)."""
    below = (q < level).to_numpy()
    runs, start = [], None
    for t, dry in enumerate([*below, False]):
        if dry and start is None:
            start = t
        elif not dry and start is not None:
            runs.append([start, t - 1])
            start = None
    pooled = []
    for a, b in runs:
        if pooled and a - pooled[-1][1] - 1 < POOL_DAYS:
            pooled[-1][1] = b
        else:
            pooled.append([a, b])
    step = float(np.median(np.diff(q.index.values) / np.timedelta64(1, "s"))) if len(q) > 1 else 86400.0
    rows = []
    for a, b in pooled:
        duration = (b - a + 1) * step / 86400
        if duration < MIN_DURATION:
            continue
        gap = np.clip((level - q).to_numpy()[a:b + 1], 0, None)
        deficit = float(np.nansum(gap) * step)
        rows.append({"start": q.index[a], "end": q.index[b], "duration (days)": duration,
                     "deficit (m³)": deficit, "intensity (m³/s)": deficit / (duration * 86400)})
    return pd.DataFrame(rows, columns=["start", "end", "duration (days)", "deficit (m³)", "intensity (m³/s)"])


def smi(monthly, labels):
    """SMI per month and cell: the rank of each month's value among the same calendar
    month's values of the run, (rank - 0.5) / n (an empirical CDF)."""
    values = np.asarray(monthly, float)
    calendar = np.asarray(labels, dtype="datetime64[M]").astype(int) % 12
    index = np.full_like(values, np.nan)
    for month in range(12):
        chosen = calendar == month
        if not chosen.any():
            continue
        block = values[chosen]
        ranks = np.argsort(np.argsort(np.where(np.isnan(block), np.inf, block), axis=0), axis=0) + 1
        count = np.isfinite(block).sum(axis=0)
        with np.errstate(invalid="ignore", divide="ignore"):
            index[chosen] = np.where(np.isfinite(block), (ranks - 0.5) / count, np.nan)
    return index


def smi_products(index, domain):
    """(drought-area fraction per month, longest drought (months) per cell, drought
    frequency per cell) from SMI (months, cells) over the domain cells."""
    dry = (index < SMI_DROUGHT) & np.isfinite(index)
    with np.errstate(invalid="ignore", divide="ignore"):
        area = dry[:, domain].mean(axis=1)
        frequency = np.where(np.isfinite(index).any(axis=0), dry.mean(axis=0), np.nan)
    longest = np.zeros(index.shape[1:], int)
    current = np.zeros(index.shape[1:], int)
    for month in dry:
        current = np.where(month, current + 1, 0)
        longest = np.maximum(longest, current)
    return area, longest, frequency


def monthly_from_series(root, variable="SM_Lall"):
    """Monthly time-weighted means per cache cell, (months, cells), and the month labels;
    None without the variable's series cache."""
    from pathlib import Path

    from .series import bounds

    path = Path(root) / "series" / f"{variable}.npy"
    if not path.is_file():
        return None
    intervals = bounds(root)
    data = np.load(path, mmap_mode="r")
    months = intervals[:, 0].astype("datetime64[M]")
    labels = np.unique(months)
    days = (intervals[:, 1] - intervals[:, 0]) / np.timedelta64(1, "D")
    out = np.empty((len(labels), data.shape[0]))
    for row, label in enumerate(labels):
        chosen = np.flatnonzero(months == label)
        out[row] = np.asarray(data[:, chosen], float) @ days[chosen] / days[chosen].sum()
    return out, labels
