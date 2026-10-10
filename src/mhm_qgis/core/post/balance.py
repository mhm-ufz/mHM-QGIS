"""Water balance of a basin or per cell: P - ET - Q - ΔS = R (F3, F8, F9, F10).

States are interval means (F3), so the balance runs over intervals 2..T: fluxes are
summed from the end of the first interval's averaging window and ΔS = S_T - S_1. With
model-step output the written states are end-of-step values and the balance is exact;
otherwise each end is off by at most the change to its neighbouring interval while
storage is monotone across that pair, reported as `bound` ("± x mm, output-interval
averaging"). Half that change (a linear trend) underestimates a fast recession. R is "unaccounted, incl. karstic loss"
(F9), never "error". S sums the storages with F8 weights (applied in the scan).
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from ..datamodel import output_registry
from . import cache, series

FLUXES = {"P": "pre", "ET": "aET", "Q": "Q", "QD": "QD", "QIf": "QIf", "QIs": "QIs", "QB": "QB",
          "recharge": "recharge", "PET": "PET"}
RATIOS = {"Q/P": ("Q", "P"), "ET/P": ("ET", "P"), "ET/PET": ("ET", "PET"), "recharge/P": ("recharge", "P")}


def storages(index) -> list[str]:
    """The scanned variables that are storage terms (`balance: storage`)."""
    roles = output_registry()["outputs"]
    return [name for name, info in index["variables"].items()
            if roles.get(info.get("output"), {}).get("balance") == "storage"]


def notes(index) -> list[str]:
    """Why a balance is approximate or incomplete."""
    written = {info.get("output") for info in index["variables"].values()}
    missing = [name for name, spec in output_registry()["outputs"].items()
               if spec.get("balance") and spec.get("file") == "mhm" and name not in written]
    found = [f"Incomplete: {', '.join(missing)} not written; R holds them."] if missing else []
    if "pre" not in index["variables"]:
        found.append("P unavailable: no forcing precipitation found for this run.")
    found += [problem for problem in index.get("problems", []) if "sealed" in problem]
    return found


def basin(root, name="domain", freq="period", exact=False, period=None) -> pd.DataFrame:
    """Balance terms in mm per group (period, year or month), with ΔS, R, the averaging
    bound, `days` and the ratios. `exact`: the output step is the model step;
    `period` (start, end) keeps the intervals inside it."""
    root = Path(root)
    index = cache.load_index(root)
    intervals = series.bounds(root)
    terms = {}
    for column, variable in FLUXES.items():
        values = series.basin(root, variable, name)
        terms[column] = values.to_numpy() if values is not None else np.full(len(intervals), np.nan)
    parts = [series.basin(root, variable, name) for variable in storages(index)]
    storage = np.sum([part.to_numpy() for part in parts], axis=0) if parts else np.full(len(intervals), np.nan)
    if period is not None:
        keep = (intervals[:, 0] >= np.datetime64(period[0])) & (intervals[:, 1] <= np.datetime64(period[1]))
        intervals, storage = intervals[keep], storage[keep]
        terms = {column: values[keep] for column, values in terms.items()}
    days = (intervals[:, 1] - intervals[:, 0]) / np.timedelta64(1, "D")
    starts = intervals[1:, 0]
    labels = (np.zeros(len(starts), int) if freq == "period"
              else starts.astype("datetime64[Y]" if freq == "year" else "datetime64[M]"))
    rows, keys = [], []
    for label in dict.fromkeys(labels):
        chosen = np.flatnonzero(labels == label) + 1  # interval indices, all >= 1
        first, last = chosen[0], chosen[-1]
        row = {column: values[chosen].sum() for column, values in terms.items()}
        row["days"] = days[chosen].sum()
        row["dS"] = storage[last] - storage[first - 1]
        row["R"] = row["P"] - row["ET"] - row["Q"] - row["dS"]
        row["bound"] = 0.0 if exact else (abs(storage[first] - storage[first - 1])
                                          + abs(storage[last] - storage[max(last - 1, 0)]))
        with np.errstate(invalid="ignore", divide="ignore"):
            row |= {ratio: row[a] / row[b] for ratio, (a, b) in RATIOS.items()}
        rows.append(row)
        keys.append(label)
    frame = pd.DataFrame(rows, index=keys if freq == "period" else pd.DatetimeIndex(np.array(keys, "datetime64[s]")))
    frame.attrs["notes"] = notes(index)
    return frame


def per_year(frame) -> pd.DataFrame:
    """Terms in mm/yr (ratios and days unchanged)."""
    scale = 365.25 / frame["days"]
    columns = [c for c in frame.columns if c not in RATIOS and c != "days"]
    return frame.assign(**{column: frame[column] * scale for column in columns})


def cell_maps(root, exact=False) -> dict:
    """Whole-run per-cell totals (mm) over intervals 2..T: P, ET, Q, P-ET, dS, R, the
    bound, the ratios (with `<ratio> > 1` flags, the mHM check protocol) and `days`."""
    root = Path(root)
    index = cache.load_index(root)
    stats = {name: np.load(root / "stats" / f"{name}.npz") for name in index["variables"]}
    intervals = series.bounds(root)
    maps = {}
    for column, variable in FLUXES.items():
        if variable in stats:
            maps[column] = stats[variable]["sum"] - stats[variable]["first"]
    weighted = _storage_maps(root, index, stats)
    if weighted is not None:
        first, second, last, previous = weighted
        maps["dS"] = last - first
        maps["bound"] = 0.0 if exact else np.abs(second - first) + np.abs(last - previous)
    if {"P", "ET", "Q", "dS"} <= set(maps):
        maps["R"] = maps["P"] - maps["ET"] - maps["Q"] - maps["dS"]
    if {"P", "ET"} <= set(maps):
        maps["P-ET"] = maps["P"] - maps["ET"]
    with np.errstate(invalid="ignore", divide="ignore"):
        for ratio, (a, b) in RATIOS.items():
            if a in maps and b in maps:
                maps[ratio] = maps[a] / maps[b]
                maps[f"{ratio} > 1"] = maps[ratio] > 1
    maps["days"] = float(((intervals[1:, 1] - intervals[1:, 0]) / np.timedelta64(1, "D")).sum())
    return maps


def _storage_maps(root, index, stats):
    """Weighted total storage per cell at intervals 0, 1, T-1 and T-2 (F8 by scene)."""
    names = storages(index)
    if not names:
        return None
    sealed = np.load(root / "sealed.npy") if (root / "sealed.npy").is_file() else None
    scenes = np.load(root / "scenes.npy") if (root / "scenes.npy").is_file() else None
    steps = len(series.bounds(root))

    def total(key, step):
        fraction = 0.0 if sealed is None else np.nan_to_num(sealed[scenes[step]])
        factors = {"cell": 1.0, "pervious": 1 - fraction, "sealed": fraction}
        return sum(stats[name][key] * factors[index["variables"][name].get("weight", "cell")] for name in names)

    return total("first", 0), total("second", 1), total("last", steps - 1), total("penultimate", steps - 2)


def summary(root, run, gauges=(), exact=False) -> pd.DataFrame:
    """One row per basin (the domain, then each gauge): area (km²), P, ET, Q (mm/yr),
    Q/P, ET/P, and at gauges BFI, NSE, KGE, PBIAS and observation coverage (%).
    Missing data stay blank (NaN), never zero. Reads the gauge files: a task."""
    from . import metrics, series, signatures

    root = Path(root)
    basins = next(iter(cache.load_index(root)["variables"].values()), {}).get("basins", ["domain"])
    area = np.load(root / "area.npy")
    rows = {}
    for name in dict.fromkeys(["domain", *basins, *(str(g) for g in gauges)]):
        row = {"area (km²)": np.nan}
        if name in basins:
            row["area (km²)"] = float((np.load(root / "weights" / f"{name}.npy") * area).sum()) / 1e6
            frame = per_year(basin(root, name, exact=exact))
            if len(frame):
                row |= {column: frame[column].iloc[0] for column in ("P", "ET", "Q", "Q/P", "ET/P")}
        if name != "domain" and name.isdigit() and int(name) in set(map(int, gauges)):
            pair = series.gauge(run, int(name))
            scores = metrics.evaluate(pair["sim"], pair["obs"])
            reference = pair["obs"] if pair["obs"].notna().any() else pair["sim"]
            row |= {"BFI": float(signatures.baseflow(reference.fillna(0).to_numpy()).sum() / reference.sum()),
                    "NSE": scores["NSE"], "KGE": scores["KGE"], "PBIAS": scores["PBIAS"],
                    "observed (%)": 100 * float(pair["obs"].notna().mean())}
        rows["Domain" if name == "domain" else f"Gauge {name}"] = row
    return pd.DataFrame.from_dict(rows, orient="index")
