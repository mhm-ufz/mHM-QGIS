"""Why here? A rule-based explanation of one cell (Tier C, §7.15).

For a cell: long-term P, PET, ET, Q and recharge (mm/yr), mean soil moisture and
maximum snowpack; static attributes from the L0 inputs under it (mean elevation and
slope, majority land cover, soil and geology class); each value's deviation from the
basin (% and z); the runoff shares. `explain.yml` maps deviation patterns to phrases;
every number is shown, and no matching rule gives "no dominant driver".
"""
from __future__ import annotations

import operator
from functools import cache
from pathlib import Path

import numpy as np
import yaml

from . import grid
from .cache import load_index
from .catalog import value

FLUXES = {"P": "pre", "PET": "PET", "ET": "aET", "Q": "Q", "recharge": "recharge"}
STATES = {"SM_Lall": ("SM_Lall", "mean"), "snowpack": ("snowpack", "max")}
SHARES = ("QD", "QIf", "QIs", "QB")
OPERATORS = {">": operator.gt, "<": operator.lt, ">=": operator.ge, "<=": operator.le}


@cache
def rules() -> list:
    return yaml.safe_load((Path(__file__).with_name("explain.yml")).read_text(encoding="utf-8"))["rules"]


def _static_sources(run):
    """name -> (path, variable, how) of the L0 inputs to summarise under a cell."""
    values, index, setup = run.values, run.ref.index, run.ref.setup
    if run.version == "v6":
        def source(key):
            path = value(values, f"config_input.{key}_path", index)
            return (setup / path, value(values, f"config_input.{key}_var", index)) if path else None

        named = {"elevation": (source("dem"), "mean"), "slope": (source("slope"), "mean"),
                 "geology class": (source("geo_class"), "majority"), "soil class": (source("soil_class"), "majority")}
    else:
        folder = setup / (value(values, "directories_general.dir_Morpho", index) or "")
        named = {"elevation": ((folder / "dem.asc", None), "mean"), "slope": ((folder / "slope.asc", None), "mean"),
                 "geology class": ((folder / "geology_class.asc", None), "majority"),
                 "soil class": ((next(iter(sorted(folder.glob("soil_class*.asc"))), folder / "soil_class.asc"), None),
                                "majority")}
    try:
        path, variable, _ = grid._l0_source(run, "land_cover")
        named["land cover class"] = ((path, variable), "majority")
    except (KeyError, IndexError, TypeError):
        pass
    return {name: (src[0], src[1], how) for name, (src, how) in named.items() if src and Path(src[0]).is_file()}


def static_attributes(run, axes, iy, ix) -> dict:
    """Mean or majority of each L0 input inside the L1 cell (unreadable inputs skipped)."""
    # ponytail: reads each L0 raster whole per click (seconds on a large DEM, on a task);
    # read only the cell's window if that becomes slow.
    left, right = sorted(axes.x_bnds[ix])
    bottom, top = sorted(axes.y_bnds[iy])
    found = {}
    for name, (path, variable, how) in _static_sources(run).items():
        try:
            data, x, y = grid.read_l0(path, variable)
        except grid._UNREADABLE:
            continue
        inside = np.ma.compressed(data[np.ix_((y > bottom) & (y < top), (x > left) & (x < right))])
        if not inside.size:
            continue
        if how == "mean":
            found[name] = float(inside.mean())
        else:
            classes, counts = np.unique(inside, return_counts=True)
            found[name] = float(classes[np.argmax(counts)])
    return found


def _stat_map(root, name, stat, years):
    data = np.load(Path(root) / "stats" / f"{name}.npz")
    if stat == "sum":
        return data["sum"] / years
    if stat == "max":
        return data["max"]
    with np.errstate(invalid="ignore", divide="ignore"):
        return data["sum"] / data["n"]


def cell(root, run, axes, iy, ix, with_static=True) -> dict:
    """{"values", "z", "percent", "shares", "static", "phrases"} of one cell."""
    root = Path(root)
    index = load_index(root)
    intervals = np.load(root / "bounds_mhm.npy")
    years = float((intervals[-1, 1] - intervals[0, 0]) / np.timedelta64(1, "D")) / 365.25
    domain = np.load(root / "weights" / "domain.npy") > 0
    values, z, percent = {}, {}, {}
    wanted = {**{k: (v, "sum") for k, v in FLUXES.items()}, **STATES}
    for label, (name, stat) in wanted.items():
        if name not in index["variables"]:
            continue
        field = _stat_map(root, name, stat, years)
        basin = field[domain & np.isfinite(field)]
        values[label] = float(field[iy, ix])
        if basin.size > 1 and basin.std() > 0:
            z[label] = float((field[iy, ix] - basin.mean()) / basin.std())
            percent[label] = float(100 * (field[iy, ix] - basin.mean()) / abs(basin.mean())) if basin.mean() else np.nan
    q_total = values.get("Q")
    shares = {}
    for name in SHARES:
        if name in index["variables"] and q_total:
            shares[f"{name}_share"] = float(_stat_map(root, name, "sum", years)[iy, ix] / q_total)
    static = static_attributes(run, axes, iy, ix) if with_static and run is not None else {}
    for name, number in static.items():
        if name in ("elevation", "slope"):
            values[name] = number
    if with_static and run is not None and {"elevation", "slope"} & set(static):
        _static_z(run, axes, domain, static, z)
    return {"values": values, "z": z, "percent": percent, "shares": shares, "static": static,
            "phrases": phrases(z, shares)}


def _static_z(run, axes, domain, static, z):
    """z of the cell's mean elevation and slope against the domain's L1 cell means."""
    for name in ("elevation", "slope"):
        if name not in static:
            continue
        path, variable, _ = _static_sources(run)[name]
        try:
            data, x, y = grid.read_l0(path, variable)
        except grid._UNREADABLE:
            continue
        valid = ~np.ma.getmaskarray(data)
        total = grid.basin_weights(axes, valid, x, y)
        filled = np.ma.filled(data, 0.0).astype(float)
        rows, cols = np.nonzero(valid)
        sums = np.zeros(axes.shape)
        ix = np.floor((x[cols] - axes.x_bnds.min()) / axes.cell).astype(int)
        iy = np.floor((axes.y_bnds.max() - y[rows]) / axes.cell).astype(int)
        keep = (ix >= 0) & (ix < axes.shape[1]) & (iy >= 0) & (iy < axes.shape[0])
        np.add.at(sums, (iy[keep], ix[keep]), filled[rows[keep], cols[keep]])
        factor = round(axes.cell / abs(x[1] - x[0])) ** 2
        with np.errstate(invalid="ignore", divide="ignore"):
            means = sums / (total * factor)
        basin = means[domain & (total > 0)]
        if basin.size > 1 and basin.std() > 0:
            z[name] = float((static[name] - basin.mean()) / basin.std())


def phrases(z, shares, rule_list=None) -> list[str]:
    """The phrases whose conditions hold, strongest first; or 'no dominant driver'."""
    terms = {**z, **shares}
    matched = []
    for rule in rule_list or rules():
        conditions = []
        for name, text in rule["when"].items():
            symbol, _, number = text.strip().partition(" ")
            if name not in terms or not OPERATORS[symbol](terms[name], float(number)):
                break
            conditions.append(abs(terms[name]) if name in z else 0.0)
        else:
            matched.append((sum(conditions), rule["says"]))
    return [text for _, text in sorted(matched, key=lambda item: -item[0])] or ["No dominant driver."]
