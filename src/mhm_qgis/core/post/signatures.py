"""Hydrological signatures of a discharge series (Tier B, §7.6).

They need daily or finer data; `usable` says why a series cannot be used. Observed
and simulated signatures are compared side by side by the caller.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

ALPHA = 0.925       # Lyne-Hollick filter parameter
RECESSION_DAYS = 5  # shortest recession segment
AFTER_PEAK = 2      # steps skipped after a peak


def usable(series: pd.Series) -> str:
    """'' when the series is daily or finer, else why signatures are disabled."""
    steps = np.diff(series.index.values) / np.timedelta64(1, "D")
    if not len(steps) or np.median(steps) > 1 + 1e-9:
        return "Signatures need daily or finer discharge."
    flow = series.dropna()
    if len(flow) < 2 or (flow.index[-1] - flow.index[0]) / np.timedelta64(1, "D") < 2 * 365 - 1:
        return "Signatures need at least two years of discharge."
    return ""


def fdc(q):
    """Flow duration curve: exceedance p = i / (n + 1) (Weibull) and sorted flows."""
    flows = np.sort(np.asarray(q, float)[np.isfinite(q)])[::-1]
    return np.arange(1, len(flows) + 1) / (len(flows) + 1), flows


def baseflow(q, alpha=ALPHA, passes=3):
    """Lyne-Hollick digital filter, forward, backward, forward."""
    flow = np.nan_to_num(np.asarray(q, float))
    base = flow.copy()
    for number in range(passes):
        series = base if number % 2 == 0 else base[::-1]
        quick = np.zeros_like(series)
        for t in range(1, len(series)):
            quick[t] = alpha * quick[t - 1] + (1 + alpha) / 2 * (series[t] - series[t - 1])
            quick[t] = min(max(quick[t], 0.0), series[t])
        result = series - quick
        base = result if number % 2 == 0 else result[::-1]
    return base


def recession_constant(q, step_days=1.0):
    """Median k (days) of ln Q = ln Q0 - t / k over recessions of 5+ falling steps."""
    flow = np.asarray(q, float)
    falling = np.r_[False, np.diff(flow) < 0]
    constants, start = [], None
    for t, down in enumerate([*falling, False]):
        if down and start is None:
            start = t
        elif not down and start is not None:
            segment = flow[start + AFTER_PEAK:t]
            if t - start >= RECESSION_DAYS and len(segment) >= 3 and (segment > 0).all():
                slope = np.polyfit(np.arange(len(segment)) * step_days, np.log(segment), 1)[0]
                if slope < 0:
                    constants.append(-1 / slope)
            start = None
    return float(np.median(constants)) if constants else np.nan


def signatures(q: pd.Series, p=None, area_km2=None, zero_threshold=0.001) -> dict:
    """Signatures of a daily (or finer) discharge series in m³ s⁻¹; P (mm per step,
    aligned) and the catchment area give the runoff ratio."""
    flow = q.dropna()
    values = flow.to_numpy(float)
    step_days = float(np.median(np.diff(flow.index.values) / np.timedelta64(1, "D")))
    years = len(values) * step_days / 365.25
    q5, q33, q66, q95 = np.percentile(values, [95, 67, 34, 5])  # flow exceeded 5 %, 33 %, 66 %, 95 %
    base = baseflow(values)
    result = {
        "Mean Q": values.mean(), "CV": values.std() / values.mean(),
        "Q5": q5, "Q95": q95, "Q5/Q95": q5 / q95 if q95 > 0 else np.nan,
        "FDC mid-slope": (np.log(q33) - np.log(q66)) / (0.66 - 0.33) if q66 > 0 else np.nan,
        "Baseflow index": base.sum() / values.sum(),
        "Recession k (days)": recession_constant(values, step_days),
        "High-flow days per year": (values > 9 * np.median(values)).sum() * step_days / years,
        "Low-flow days per year": (values < 0.2 * values.mean()).sum() * step_days / years,
        "Zero-flow fraction": float((values < zero_threshold).mean()),
        "Runoff ratio": np.nan,
    }
    if p is not None and area_km2:  # totals over the same span: P may come at any step
        rain = p[(p.index >= flow.index[0]) & (p.index <= flow.index[-1])].dropna()
        runoff_mm = (flow * step_days * 86400).sum() / (area_km2 * 1e6) * 1e3
        result["Runoff ratio"] = runoff_mm / rain.sum() if rain.sum() > 0 else np.nan
    return {name: float(value) for name, value in result.items()}


def compare(observed: dict, simulated: dict) -> pd.DataFrame:
    """Observed, simulated and the relative difference per signature."""
    frame = pd.DataFrame({"observed": observed, "simulated": simulated})
    with np.errstate(invalid="ignore", divide="ignore"):
        frame["relative difference"] = (frame["simulated"] - frame["observed"]) / frame["observed"].abs()
    return frame
