"""Run A against run B (Tier C, §7.13): differences of maps, series and scores.

Both runs must share the grid (axes equal within 1e-9 of the cell size) and overlap
in time; otherwise the comparison is refused with the reason.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from . import aggregate, series

EPSILON = 1e-6  # |A| below this gives no relative difference


def refusal(root_a, root_b) -> str:
    """'' when the two scanned runs can be compared, else why not."""
    root_a, root_b = Path(root_a), Path(root_b)
    for name in ("x", "y"):
        a, b = np.load(root_a / f"{name}.npy"), np.load(root_b / f"{name}.npy")
        cell = abs(a[1] - a[0]) if len(a) > 1 else 1.0
        if a.shape != b.shape or not np.allclose(a, b, rtol=0, atol=1e-9 * cell):
            return "The runs are on different grids."
    a, b = series.bounds(root_a), series.bounds(root_b)
    if max(a[0, 0], b[0, 0]) >= min(a[-1, 1], b[-1, 1]):
        return "The runs do not overlap in time."
    return ""


def overlap(root_a, root_b):
    """The common period (start, end) of two runs, as ISO strings."""
    a, b = series.bounds(root_a), series.bounds(root_b)
    return str(max(a[0, 0], b[0, 0])), str(min(a[-1, 1], b[-1, 1]))


def difference(a, b):
    """(ΔX = B - A, ΔX % = 100 ΔX / A, masked where |A| < EPSILON)."""
    delta = np.asarray(b, float) - np.asarray(a, float)
    with np.errstate(invalid="ignore", divide="ignore"):
        percent = np.where(np.abs(a) < EPSILON, np.nan, 100 * delta / np.asarray(a, float))
    return delta, percent


def maps(root_a, root_b, variable, stat, period=None, q=None):
    """(A, B, ΔX, ΔX %) of a statistic map over the common period; None entries when a
    run needs a derive pass for it."""
    reason = refusal(root_a, root_b)
    if reason:
        raise ValueError(reason)
    period = period or overlap(root_a, root_b)
    a = aggregate.statistic(root_a, variable, stat, period, q)
    b = aggregate.statistic(root_b, variable, stat, period, q)
    if a is None or b is None:
        return a, b, None, None
    return (a, b, *difference(a, b))


def series_pair(root_a, root_b, variable, basin="domain", period=None) -> pd.DataFrame:
    """A and B basin series on their common intervals, with Δ."""
    period = period or overlap(root_a, root_b)
    a = series.basin(root_a, variable, basin, period)
    b = series.basin(root_b, variable, basin, period)
    frame = pd.concat({"A": a, "B": b}, axis=1).dropna(how="all")
    frame["B - A"] = frame["B"] - frame["A"]
    return frame


def score_deltas(a: dict, b: dict) -> pd.DataFrame:
    """Scores or signatures of A and B side by side, with B - A."""
    names = [name for name in a if isinstance(a.get(name), (int, float)) and isinstance(b.get(name), (int, float))]
    frame = pd.DataFrame({"A": [a[n] for n in names], "B": [b[n] for n in names]}, index=names)
    frame["B - A"] = frame["B"] - frame["A"]
    return frame
