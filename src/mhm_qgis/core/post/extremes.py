"""Annual extremes and return periods (Tier C, §7.10).

Annual maxima and minima (calendar or water year), the 7-day minimum, peaks over
Q5; empirical return periods by Gringorten and a GEV fitted by L-moments (Hosking
1997), shown only with ten or more years.
"""
from __future__ import annotations

from math import gamma, log

import numpy as np
import pandas as pd

MIN_YEARS_GEV = 10
RETURN_PERIODS = (2, 5, 10, 20, 50, 100)


def _years(index, start_month):
    """The (water) year of each moment; a water year is named by the year it ends in."""
    return index.year + (index.month >= start_month) * (start_month > 1)


def annual(q: pd.Series, how="max", start_month=1) -> pd.Series:
    """Annual maxima or minima; only years the series covers completely."""
    flow = q.dropna()
    years = _years(flow.index, start_month)
    grouped = flow.groupby(years)
    counts = grouped.count()
    full = counts[counts >= 0.9 * counts.max()].index  # tolerate gaps, drop partial edge years
    return getattr(grouped, how)().loc[full]


def am7(q: pd.Series, start_month=1) -> pd.Series:
    """The annual minimum of the 7-day mean flow."""
    return annual(q.rolling(7, min_periods=7).mean(), "min", start_month)


def pot_counts(q: pd.Series, start_month=1) -> pd.Series:
    """Peaks over Q5 (the flow exceeded 5 % of the time) per year: separate exceedances."""
    flow = q.dropna()
    above = flow > np.percentile(flow, 95)
    starts = above & ~above.shift(1, fill_value=False)
    return starts.groupby(_years(flow.index, start_month)).sum()


def plotting_positions(values):
    """(sorted values descending, return period T = (n + 0.12) / (m - 0.44)) — Gringorten."""
    x = np.sort(np.asarray(values, float)[np.isfinite(values)])[::-1]
    m = np.arange(1, len(x) + 1)
    return x, (len(x) + 0.12) / (m - 0.44)


def lmoments(values):
    """Sample L-moments (l1, l2, t3) from probability-weighted moments."""
    x = np.sort(np.asarray(values, float)[np.isfinite(values)])
    n = len(x)
    i = np.arange(n)
    b0 = x.mean()
    b1 = np.sum(i / (n - 1) * x) / n
    b2 = np.sum(i * (i - 1) / ((n - 1) * (n - 2)) * x) / n
    l1, l2, l3 = b0, 2 * b1 - b0, 6 * b2 - 6 * b1 + b0
    return l1, l2, l3 / l2


def gev_fit(values):
    """GEV (location xi, scale alpha, shape k; Hosking's sign convention) by L-moments."""
    l1, l2, t3 = lmoments(values)
    c = 2 / (3 + t3) - log(2) / log(3)
    k = 7.8590 * c + 2.9554 * c ** 2
    alpha = l2 * k / ((1 - 2 ** -k) * gamma(1 + k))
    xi = l1 - alpha * (1 - gamma(1 + k)) / k
    return xi, alpha, k


def gev_quantile(params, period):
    """The flow with return period T years (annual maxima)."""
    xi, alpha, k = params
    y = -np.log(1 - 1 / np.asarray(period, float))
    return xi + alpha / k * (1 - y ** k)


def return_levels(annual_maxima: pd.Series) -> pd.DataFrame:
    """GEV return levels for the standard periods; empty below ten years."""
    if annual_maxima.notna().sum() < MIN_YEARS_GEV:
        return pd.DataFrame(columns=["return period (years)", "flow"])
    params = gev_fit(annual_maxima.to_numpy())
    return pd.DataFrame({"return period (years)": RETURN_PERIODS,
                         "flow": gev_quantile(params, RETURN_PERIODS)})
