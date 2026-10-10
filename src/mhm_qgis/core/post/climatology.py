"""Monthly climatology and anomalies (Tier B; the anomaly z is used in Tier C).

Per calendar month: the mean and standard deviation of the monthly values, for a
basin series or per cell from the scan's monthly totals. z = (x - mean_m) / std_m.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from .cache import load_index


def usable(monthly: pd.Series) -> str:
    """'' when a climatology makes sense, else why not (runs under a year)."""
    return "" if monthly.notna().sum() >= 12 else "A climatology needs at least a year of monthly values."


def monthly(series: pd.Series) -> pd.DataFrame:
    """12 rows (month 1-12): mean, std and count of the monthly values."""
    by_month = series.dropna().groupby(series.dropna().index.month)
    return by_month.agg(["mean", "std", "count"]).reindex(range(1, 13))


def anomaly(series: pd.Series) -> pd.Series:
    """z of each monthly value against its calendar month."""
    stats = monthly(series)
    months = series.index.month
    with np.errstate(invalid="ignore", divide="ignore"):
        return (series - stats["mean"].reindex(months).to_numpy()) / stats["std"].reindex(months).to_numpy()


def maps(root, variable):
    """(mean, std) per calendar month per cell, (12, ny, nx), from the monthly totals."""
    root = Path(root)
    labels = np.array(load_index(root)["variables"][variable]["months"], dtype="datetime64[M]")
    totals = np.load(root / "monthly" / f"{variable}.npy").astype("f8")
    calendar = labels.astype(int) % 12
    mean = np.stack([np.nanmean(totals[calendar == m], axis=0) if (calendar == m).any()
                     else np.full(totals.shape[1:], np.nan) for m in range(12)])
    std = np.stack([np.nanstd(totals[calendar == m], axis=0) if (calendar == m).sum() > 1
                    else np.full(totals.shape[1:], np.nan) for m in range(12)])
    return mean, std


def anomaly_maps(root, variable):
    """z per month of the run and cell, (months, ny, nx), with the month labels."""
    root = Path(root)
    labels = np.array(load_index(root)["variables"][variable]["months"], dtype="datetime64[M]")
    totals = np.load(root / "monthly" / f"{variable}.npy").astype("f8")
    mean, std = maps(root, variable)
    calendar = labels.astype(int) % 12
    with np.errstate(invalid="ignore", divide="ignore"):
        return (totals - mean[calendar]) / std[calendar], labels
