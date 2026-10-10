"""Automated plausibility checks (Tier C, §7.12): the rules of `checks.yml`.

Each finding carries a location to jump to: cells (row, column), a gauge or a month.
Everything reads the scan cache except the gauge rule, which takes the gauges'
scores from the caller (they need netCDF, so a task computes them).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import cache
from pathlib import Path

import numpy as np
import yaml

from . import balance, climatology
from .cache import load_index

MAX_CELLS = 20  # cells listed per finding


@dataclass
class Finding:
    rule: str
    severity: str
    message: str
    cells: list = field(default_factory=list)   # (row, column)
    gauge: int | None = None
    month: str | None = None


@cache
def rules() -> list:
    return yaml.safe_load((Path(__file__).with_name("checks.yml")).read_text(encoding="utf-8"))["rules"]


def _cells(mask):
    rows, cols = np.nonzero(mask)
    return [(int(r), int(c)) for r, c in zip(rows[:MAX_CELLS], cols[:MAX_CELLS], strict=True)]


def _finding(rule, mask=None, **values):
    count = int(np.sum(mask)) if mask is not None else 0
    return Finding(rule["id"], rule["severity"], rule["message"].format(count=count, **values),
                   _cells(mask) if mask is not None else [])


def _ratio_map(rule, run):
    mask = (run["maps"].get(rule["ratio"], np.zeros(run["domain"].shape)) > rule["above"]) & run["domain"]
    return [_finding(rule, mask)] if mask.any() else []


def _negative(rule, run):
    masks = {name: (run["stats"][name]["min"] < 0) & run["domain"] for name in balance.storages(run["index"])}
    return [_finding(rule, mask, variable=name) for name, mask in masks.items() if mask.any()]


def _missing(rule, run):
    masks = {name: (run["stats"][name]["n"] < run["steps"]) & run["domain"] for name in run["stats"] if name != "pre"}
    return [_finding(rule, mask, variable=name) for name, mask in masks.items() if mask.any()]


def _all_zero(rule, run):
    return [_finding(rule, variable=name) for name in rule["variables"]
            if name in run["stats"] and np.nanmax(np.abs(run["stats"][name]["max"][run["domain"]])) == 0]


def _winter(rule, run):
    mask = _winter_snow(run["root"], run["index"], run["domain"])
    return [_finding(rule, mask)] if mask is not None and mask.any() else []


def _residual(rule, run):
    frame = balance.basin(run["root"], exact=run["exact"])
    if not len(frame) or not np.isfinite(frame["P"].iloc[0]) or frame["P"].iloc[0] <= 0:
        return []
    percent = 100 * abs(frame["R"].iloc[0]) / frame["P"].iloc[0]
    return [_finding(rule, percent=percent)] if percent > rule["above_percent"] else []


def _anomaly(rule, run):
    if not run["index"]["variables"].get(rule["variable"], {}).get("months"):
        return []
    z, labels = climatology.anomaly_maps(run["root"], rule["variable"])
    extreme = (np.abs(z) >= rule["z"]) & run["domain"][None]
    if not extreme.any():
        return []
    finding = _finding(rule, extreme.any(axis=0), variable=rule["variable"], z=rule["z"])
    finding.message = rule["message"].format(count=int(extreme.sum()), variable=rule["variable"], z=rule["z"])
    finding.month = str(labels[np.argmax(extreme.sum(axis=(1, 2)))])
    return [finding]


def _gauge_nse(rule, run):
    found = []
    for gauge, scores in (run["gauge_scores"] or {}).items():
        if np.isfinite(scores.get("NSE", np.nan)) and scores["NSE"] < rule["below"]:
            finding = _finding(rule, value=scores["NSE"], below=rule["below"], gauge=gauge)
            finding.gauge = int(gauge)
            found.append(finding)
    return found


CHECKS = {"ratio_map": _ratio_map, "negative": _negative, "missing": _missing, "all_zero": _all_zero,
          "winter_snow": _winter, "residual": _residual, "anomaly": _anomaly, "gauge_nse": _gauge_nse}


def run(root, exact=False, gauge_scores=None) -> list[Finding]:
    """Every rule against the scanned run; `gauge_scores` is {gauge: metrics.evaluate(...)}."""
    root = Path(root)
    index = load_index(root)
    context = {"root": root, "index": index, "exact": exact, "gauge_scores": gauge_scores,
               "domain": np.load(root / "weights" / "domain.npy") > 0,
               "stats": {name: np.load(root / "stats" / f"{name}.npz") for name in index["variables"]},
               "steps": len(np.load(root / "bounds_mhm.npy")), "maps": balance.cell_maps(root, exact)}
    return [finding for rule in rules() for finding in CHECKS[rule["check"]](rule, context)]


def _winter_snow(root, index, domain):
    """Cells with snow whose mean DJF snowpack is not above JJA's (northern seasons;
    swapped south of the equator), from the snowpack series cache."""
    from .drought import monthly_from_series

    if "snowpack" not in index["variables"]:
        return None
    monthly = monthly_from_series(root, "snowpack")
    if monthly is None:
        return None
    values, labels = monthly
    month = labels.astype(int) % 12 + 1
    winter, summer = np.isin(month, (12, 1, 2)), np.isin(month, (6, 7, 8))
    if not winter.any() or not summer.any():
        return None
    cells = np.load(Path(root) / "cells_mhm.npy")
    south = bool(index.get("geographic")) and np.load(Path(root) / "y.npy").mean() < 0
    if south:
        winter, summer = summer, winter
    with np.errstate(invalid="ignore"):
        bad = (values[winter].mean(0) <= values[summer].mean(0)) & (values.max(0) > 0)
    mask = np.zeros(domain.shape, bool)
    mask[cells[0], cells[1]] = bad
    return mask & domain
