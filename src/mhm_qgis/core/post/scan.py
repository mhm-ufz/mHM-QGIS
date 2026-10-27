"""One pass over a run's output file: every product the pages read (F6).

Runs in the native worker (`post-scan`), so a full-file read never touches the GUI
process. Blocks of whole time steps are sized to the memory budget, so each chunk
(1, ny, nx) is decompressed once. Per variable it writes:
- `series/<var>.npy`: float32 (cells, T) memmap, only for the variables asked for;
- `basins/<var>.npy`: (basins, T) weighted means (F8: pervious and sealed states
  carry 1 - fSealed and fSealed; storage amounts are per basin area, intensities
  such as SM_L per weighted area);
- `stats/<var>.npz`: per cell n, sum, sumsq, min, max, and the values of intervals
  0, 1, T-2 and T-1 (per-cell balance over intervals 2..T and its averaging bound);
- `monthly/<var>.npy`: totals per calendar month (fluxes, when no interval spans two
  months), NaN for incomplete months; `annual/<var>_max.npy` and `_min.npy`;
- the 2nd and 98th percentile of a sample, for colours that hold while animating.

Precipitation (`pre`) is not an mHM output (F10): the forcing is read on L2, each L1
cell takes its containing L2 cell, and the forcing steps are summed per output interval.
"""
from __future__ import annotations

import itertools
from pathlib import Path

import numpy as np

from .catalog import read_bounds

SAMPLE_SIZE = 1_000_000


def _calendar(bounds, unit):
    """Index of each interval's month or year, and the labels; None when one spans two."""
    first, last = (bounds[:, 0].astype(f"datetime64[{unit}]"),
                   (bounds[:, 1] - np.timedelta64(1, "s")).astype(f"datetime64[{unit}]"))
    if unit == "M" and (first != last).any():
        return None, []
    index = (first - first.min()).astype(int)
    return index, [str(first.min() + step) for step in range(index.max() + 1)]


def calendar_days(labels) -> np.ndarray:
    """Days in each calendar period (datetime64 of unit D, M or Y); never an average month."""
    return ((labels + 1).astype("datetime64[D]") - labels.astype("datetime64[D]")) / np.timedelta64(1, "D")


def _axis_index(values, centres):
    """Index of the cell (of `centres`, either order) holding each value."""
    step = centres[1] - centres[0]
    return np.floor((values - (centres[0] - step / 2)) / step).astype(int)


def _file_blocks(path, name, cells, steps):
    import netCDF4

    with netCDF4.Dataset(path) as handle:
        var = handle[name]
        for start in range(0, var.shape[0], steps):
            block = np.ma.filled(np.ma.asarray(var[start:start + steps], dtype="f8"), np.nan)
            yield start, block[:, cells[0], cells[1]]


def _forcing_blocks(job, cells, bounds, steps):
    """Forcing P on the L1 cells, summed per output interval, as (first interval, rows)."""
    import netCDF4

    path, variable = job["forcing"]["path"], job["forcing"]["variable"]
    forcing = read_bounds(path)
    interval = np.searchsorted(bounds[:, 0], forcing[:, 0], "right") - 1
    inside = (interval >= 0) & (forcing[:, 1] <= bounds[interval.clip(0), 1])
    seconds = (forcing[:, 1] - forcing[:, 0]) / np.timedelta64(1, "s")
    covered = np.bincount(interval[inside], weights=seconds[inside], minlength=len(bounds))
    complete = covered >= (bounds[:, 1] - bounds[:, 0]) / np.timedelta64(1, "s")
    job["_incomplete"] = int((~complete).sum())  # intervals the forcing does not cover: P missing
    x1, y1 = (np.load(Path(job["cache"]) / f"{name}.npy") for name in ("x", "y"))
    with netCDF4.Dataset(path) as handle:
        var = handle[variable]
        x_name, y_name = var.dimensions[-1], var.dimensions[-2]
        ix = _axis_index(x1[cells[1]], np.asarray(handle[x_name][:], "f8"))
        iy = _axis_index(y1[cells[0]], np.asarray(handle[y_name][:], "f8"))
        pending, rows = None, []
        for start in range(0, var.shape[0], steps):
            keep = inside[start:start + steps]
            if not keep.any():
                continue
            block = np.ma.filled(np.ma.asarray(var[start:start + steps], dtype="f8"), np.nan)[keep][:, iy, ix]
            ids = interval[start:start + steps][keep]
            heads = np.flatnonzero(np.r_[True, ids[1:] != ids[:-1]])
            sums = np.add.reduceat(block, heads, axis=0)
            for index, row in zip(ids[heads], sums, strict=True):
                if not complete[index]:
                    continue
                if pending is not None and pending[0] == index:
                    pending = (index, pending[1] + row)
                    continue
                if pending is not None:
                    rows.append(pending)
                pending = (index, row)
            while len(rows) >= steps:
                yield from _contiguous(rows[:steps])
                rows = rows[steps:]
        rows += [pending] if pending is not None else []
        yield from _contiguous(rows)


def _contiguous(rows):
    """Consecutive (interval, row) pairs as blocks; gaps stay NaN in the products."""
    if not rows:
        return
    heads = [0, *(i for i in range(1, len(rows)) if rows[i][0] != rows[i - 1][0] + 1), len(rows)]
    for a, b in itertools.pairwise(heads):
        yield rows[a][0], np.stack([row for _, row in rows[a:b]])


class _Products:
    def __init__(self, job, spec, cells, bounds, series):
        n, steps = len(cells[0]), len(bounds)
        self.spec, self.cells = spec, cells
        self.stats = {"n": np.zeros(n), "sum": np.zeros(n), "sumsq": np.zeros(n),
                      "min": np.full(n, np.nan), "max": np.full(n, np.nan),
                      # intervals 0, 1, T-2 and T-1: storage change and its averaging bound per cell
                      "first": np.full(n, np.nan), "second": np.full(n, np.nan),
                      "penultimate": np.full(n, np.nan), "last": np.full(n, np.nan)}
        self.steps = steps
        self.series = series
        self.months, self.month_labels = _calendar(bounds, "M") if spec["kind"] == "flux" else (None, [])
        if self.months is not None:
            self.monthly = np.zeros((len(self.month_labels), n))
            self.month_seconds = np.zeros(len(self.month_labels))
            self.interval_seconds = (bounds[:, 1] - bounds[:, 0]) / np.timedelta64(1, "s")
        self.years, self.year_labels = _calendar(bounds, "Y")
        self.annual = {"max": np.full((len(self.year_labels), n), np.nan),
                       "min": np.full((len(self.year_labels), n), np.nan)}
        self.weights = job.get("_weights")  # (basins, cells) of w * area
        self.basins = np.full((steps, 0 if self.weights is None else len(self.weights)), np.nan)
        self.sealed, self.scenes = job.get("_sealed"), job.get("_scenes")
        self.stride = max(1, n * steps // SAMPLE_SIZE)
        self.sample = []

    def add(self, start, block):
        stop = start + len(block)
        finite = np.isfinite(block)
        stats = self.stats
        stats["n"] += finite.sum(axis=0)
        stats["sum"] += np.nansum(block, axis=0)
        stats["sumsq"] += np.nansum(block ** 2, axis=0)
        stats["min"] = np.fmin(stats["min"], np.fmin.reduce(block, axis=0))
        stats["max"] = np.fmax(stats["max"], np.fmax.reduce(block, axis=0))
        for key, step in (("first", 0), ("second", 1), ("penultimate", self.steps - 2), ("last", self.steps - 1)):
            if start <= step < stop:
                stats[key] = block[step - start]
        if self.series is not None:
            self.series[:, start:stop] = block.T
        if self.weights is not None and len(self.weights):
            factor = np.ones_like(block)
            if self.spec["weight"] != "cell" and self.sealed is not None:
                sealed = np.nan_to_num(self.sealed[self.scenes[start:stop]])
                factor = 1 - sealed if self.spec["weight"] == "pervious" else sealed
            share = factor if not self.spec.get("amount") else np.ones_like(block)
            with np.errstate(invalid="ignore", divide="ignore"):
                self.basins[start:stop] = (np.nan_to_num(block * factor) @ self.weights.T) / (
                    (finite * share) @ self.weights.T)
        if self.months is not None:
            months = self.months[start:stop]
            np.add.at(self.monthly, months, block)
            np.add.at(self.month_seconds, months, self.interval_seconds[start:stop])
        years = self.years[start:stop]
        np.fmax.at(self.annual["max"], years, block)
        np.fmin.at(self.annual["min"], years, block)
        self.sample.append(block.ravel()[::self.stride])

    def save(self, root, shape):
        name = self.spec["name"]

        def grid(values):
            out = np.full((*values.shape[:-1], *shape), np.nan, dtype=values.dtype)
            out[..., self.cells[0], self.cells[1]] = values
            return out

        for sub in ("stats", "basins", "monthly", "annual"):
            (root / sub).mkdir(exist_ok=True)
        np.savez(root / "stats" / f"{name}.npz", **{key: grid(value) for key, value in self.stats.items()})
        if self.basins.shape[1]:
            np.save(root / "basins" / f"{name}.npy", self.basins.T)
        if self.months is not None:
            months = np.array(self.month_labels, dtype="datetime64[M]")
            calendar = calendar_days(months) * 86400
            self.monthly[self.month_seconds < calendar] = np.nan  # a month the run covers in part
            np.save(root / "monthly" / f"{name}.npy", grid(self.monthly.astype("f4")))
        for key, values in self.annual.items():
            np.save(root / "annual" / f"{name}_{key}.npy", grid(values.astype("f4")))
        sample = np.concatenate(self.sample) if self.sample else np.array([np.nan])
        sample = sample[np.isfinite(sample)]
        return {"range": [float(v) for v in np.percentile(sample, [2, 98])] if sample.size else None,
                "months": self.month_labels if self.months is not None else None,
                "years": self.year_labels, "steps": len(self.basins)}


def run_scan(job, progress=None) -> dict:
    """Build the products of `job["variables"]` and record them, with their
    fingerprints, in the cache's `index.json`."""
    root = Path(job["cache"])
    cells = tuple(np.load(root / f"cells_{job['file']}.npy"))
    bounds = read_bounds(job["path"])
    shape = tuple(job["shape"])
    steps = max(1, int(job.get("budget_mb", 256) * 2 ** 20 // (shape[0] * shape[1] * 8)))
    if job.get("basins"):
        area = np.load(root / "area.npy")[cells]
        job["_weights"] = np.stack([np.load(root / "weights" / f"{name}.npy")[cells] * area
                                    for name in job["basins"]])
    if job.get("sealed"):
        job["_sealed"] = np.load(root / "sealed.npy")[:, cells[0], cells[1]]
        job["_scenes"] = np.load(root / "scenes.npy")
    (root / "series").mkdir(exist_ok=True)
    summary, total = {}, max(1, len(job["variables"]) * -(-len(bounds) // steps))
    done = 0
    for spec in job["variables"]:
        name = spec["name"]
        series = None
        if name in job.get("series", ()):
            series = np.lib.format.open_memmap(root / "series" / f"{name}.npy", "w+", "f4",
                                               (len(cells[0]), len(bounds)))
            if name == "pre":
                series[:] = np.nan  # intervals without forcing stay missing
        products = _Products(job, spec, cells, bounds, series)
        blocks = _forcing_blocks(job, cells, bounds, steps) if name == "pre" \
            else _file_blocks(job["path"], name, cells, steps)
        for start, block in blocks:
            products.add(start, block)
            done += 1
            if progress:
                progress(min(99, 100 * done // total))
        if series is not None:
            series.flush()
            del series
        summary[name] = {**products.save(root, shape), "basins": list(job.get("basins", ())),
                         **{key: spec[key] for key in ("output", "kind", "weight", "aggregate") if key in spec}}
        if name == "pre":
            summary[name]["incomplete"] = job["_incomplete"]
    from . import cache

    index = cache.load_index(root)
    for name, info in summary.items():
        index["variables"][name] = {**info, "fingerprint": job.get("fingerprints", {}).get(name), "file": job["file"]}
    index["problems"], index["domain_source"] = job.get("problems", []), job.get("domain_source")
    index["geographic"] = job.get("geographic", False)
    cache.save_index(root, index)
    if progress:
        progress(100)
    return summary


def derive(job):
    """Small one-off reads for the `post-derive` job; `what` selects one."""
    if job["what"] == "cell":
        import netCDF4

        with netCDF4.Dataset(job["path"]) as handle:  # decompresses every map (F6): background only
            return {name: np.ma.filled(np.ma.asarray(handle[name][:, job["iy"], job["ix"]], "f8"), np.nan).tolist()
                    for name in job["variables"]}
    if job["what"] == "statistic":
        return _period_statistic(job)
    if job["what"] == "observe":
        from .observe import run_job

        return run_job(job)
    if job["what"] == "bankfull":  # mhm-tools (xarray), out of process like the scan
        from ...externals.mhm_tools_handler import bankfull

        return bankfull(job["path"], job["out"], var=job["variable"])
    raise ValueError(f"Unknown derive product: {job['what']}")


def _period_statistic(job):
    """A statistic map over `job["period"]`, read from the file; saved to `job["out"]`.

    Flux means are rates (sum / days), state means are time-weighted (F3, F4).
    Percentiles hold the period in memory and are refused above `budget_mb`."""
    import netCDF4

    intervals = read_bounds(job["path"])
    period = [np.datetime64(value) for value in job["period"]]
    chosen = np.flatnonzero((intervals[:, 0] >= period[0]) & (intervals[:, 1] <= period[1]))
    if not chosen.size:
        raise ValueError("No output interval lies inside the period.")
    days = (intervals[:, 1] - intervals[:, 0]) / np.timedelta64(1, "D")
    stat, flux = job["stat"], job["kind"] == "flux"
    with netCDF4.Dataset(job["path"]) as handle:
        var = handle[job["variable"]]
        if stat == "percentile":
            if chosen.size * var.shape[1] * var.shape[2] * 8 > job.get("budget_mb", 256) * 2 ** 20:
                raise ValueError("Too long a period for a percentile without the series cache.")
            values = np.ma.filled(np.ma.asarray(var[chosen[0]:chosen[-1] + 1], "f8"), np.nan)
            result = np.nanpercentile(values, job["q"], axis=0)
        else:
            total = weighted = squares = 0.0
            low, high = np.inf, -np.inf
            count = 0
            for step in chosen:
                values = np.ma.filled(np.ma.asarray(var[step], "f8"), np.nan)
                total, weighted = total + values, weighted + values * days[step]
                squares, count = squares + values ** 2, count + 1
                low, high = np.fmin(low, values), np.fmax(high, values)
            mean = total / count
            result = {"sum": total, "min": low, "max": high,
                      "mean": total / days[chosen].sum() if flux else weighted / days[chosen].sum(),
                      "std": np.sqrt(np.maximum(squares / count - mean ** 2, 0))}[stat]
    np.save(job["out"], result)
    return job["out"]
