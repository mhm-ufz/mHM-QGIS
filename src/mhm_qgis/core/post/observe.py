"""The run against gridded observations (Tier B, §7.8): observation operators.

observation -> temporal harmonisation (the coarser record's intervals; a window is
used only when the finer record covers it completely) -> spatial harmonisation
(exact block mean onto the coarser grid) -> units (unknown units are refused) ->
mask intersection -> per-cell maps, spatial-pattern scores per window and a basin
comparison. Fluxes are summed over a window and states averaged (time-weighted):
mhm-tools' `resample_to_reference_windows` averages everything, which is right for
states only, so it is not used (checked in P6). Runs in the native worker.

TWS anomaly and snow cover are mhm-tools evaluations; they need regular lat/lon
grids, so projected runs are refused with that reason.
"""
from __future__ import annotations

import json
import warnings
from pathlib import Path

import numpy as np

from .catalog import read_bounds
from .grid import Axes, _edges

UNITS = {"mm": ("amount", 1.0), "kg m-2": ("amount", 1.0), "kg/m2": ("amount", 1.0), "m": ("amount", 1000.0),
         "mm d-1": ("rate", 1.0), "mm/d": ("rate", 1.0), "mm day-1": ("rate", 1.0), "mm/day": ("rate", 1.0),
         "kg m-2 s-1": ("rate", 86400.0), "m3 m-3": ("fraction", 1.0), "m3/m3": ("fraction", 1.0),
         "1": ("fraction", 1.0), "-": ("fraction", 1.0), "%": ("fraction", 0.01), "counts": ("count", 1.0),
         "cph": ("count", 1.0)}
# adapter: what the model side is, and what the observation's units must express
ADAPTERS = {"et": {"model": "aET", "kind": "flux", "expects": ("amount", "rate")},
            "swe": {"model": "snowpack", "kind": "state", "expects": ("amount",)},
            "soil_moisture": {"model": "SWC_L", "kind": "state", "expects": ("fraction",)},
            "neutrons": {"model": "neutrons", "kind": "state", "expects": ("count",)}}
MIN_PAIRS = 6
AXES = (("x", "y"), ("lon", "lat"), ("longitude", "latitude"), ("easting", "northing"))


def gridded_axes(handle, variable):
    """(x name, y name) of a variable's 1-D axes."""
    dims = handle[variable].dimensions
    for x, y in AXES:
        if x in dims and y in dims and x in handle.variables and y in handle.variables:
            return x, y
    raise ValueError(f"{variable} has no 1-D x/y, lon/lat or easting/northing axes.")


def _bounds(path, variable):
    try:
        return read_bounds(path, "time")
    except (ValueError, KeyError):
        import netCDF4

        with netCDF4.Dataset(path) as handle:
            time = handle["time"]
            stamps = np.asarray(netCDF4.num2date(time[:], " ".join(time.units.split()),
                                                 getattr(time, "calendar", "standard"),
                                                 only_use_cftime_datetimes=False,
                                                 only_use_python_datetimes=True), dtype="datetime64[s]")
        if len(stamps) < 2:
            raise ValueError(f"{Path(path).name} has one time step and no time bounds.") from None
        step = np.median(np.diff(stamps))
        return np.stack([stamps, np.r_[stamps[1:], stamps[-1] + step]], axis=1)


def windows_of(fine, coarse):
    """For each fine interval the coarse window holding it (-1: none), and which
    windows the fine record covers completely."""
    index = np.searchsorted(coarse[:, 0], fine[:, 0], "right") - 1
    inside = (index >= 0) & (fine[:, 1] <= coarse[index.clip(0), 1])
    index[~inside] = -1
    seconds = (fine[:, 1] - fine[:, 0]) / np.timedelta64(1, "s")
    covered = np.bincount(index[inside], weights=seconds[inside], minlength=len(coarse))
    return index, covered >= (coarse[:, 1] - coarse[:, 0]) / np.timedelta64(1, "s") - 1e-6


def _bins(x_fine, y_fine, x_coarse, y_coarse):
    """Index of the coarse cell holding each fine cell (flat), -1 outside."""
    def axis(values, centres):
        step = centres[1] - centres[0]
        index = np.floor((values - (centres[0] - step / 2)) / step).astype(int)
        return np.where((index >= 0) & (index < len(centres)), index, -1)

    ix, iy = axis(x_fine, x_coarse), axis(y_fine, y_coarse)
    flat = iy[:, None] * len(x_coarse) + ix[None, :]
    flat[(iy[:, None] < 0) | (ix[None, :] < 0)] = -1
    return flat.ravel()


def regrid(field, bins, size):
    """Block mean of a fine field onto the coarse cells (NaN-aware)."""
    flat = np.asarray(field, "f8").ravel()
    keep = (bins >= 0) & np.isfinite(flat)
    total = np.bincount(bins[keep], weights=flat[keep], minlength=size)
    count = np.bincount(bins[keep], minlength=size)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(count > 0, total / count, np.nan)


def _aggregate(read, intervals, windows, flux, n_windows, shape):
    """Each complete window's sum (flux) or time-weighted mean (state) of the steps."""
    index, complete = windows_of(intervals, windows)
    out = np.full((n_windows, *shape), np.nan, dtype="f4")
    seconds = (intervals[:, 1] - intervals[:, 0]) / np.timedelta64(1, "s")
    for window in np.flatnonzero(complete):
        steps = np.flatnonzero(index == window)
        data = read(steps[0], steps[-1] + 1)
        if flux:
            out[window] = data.sum(axis=0)
        else:
            out[window] = np.tensordot(seconds[steps], data, axes=1) / seconds[steps].sum()
    return out


def soil_weights(values, version, index, depth_mm):
    """{layer: weight} so that Σ SWC_L * weight is the volumetric water over [0, depth]."""
    key = "soildata.soil_depth" if version == "v5.13" else "config_mpr.soil_depth"
    group, _, name = key.partition(".")
    depths = values.get(group, {}).get(name)
    if not depths:
        raise ValueError("The horizon depths (soil_depth) are not in the namelists.")
    lower = [float(d[index - 1] if isinstance(d, list) else d) for d in depths]
    lower = [d for d in lower if d > 0]
    weights, top = {}, 0.0
    for layer, bottom in enumerate(lower, start=1):
        overlap = max(0.0, min(bottom, depth_mm) - top)
        if overlap:
            weights[layer] = overlap / (bottom - top) / depth_mm  # SWC_L / thickness * overlap / depth
        top = bottom
    if top < depth_mm:
        raise ValueError(f"The soil is {top:g} mm deep, shallower than the observation depth {depth_mm:g} mm.")
    return weights


def compare(job) -> str:
    """The `observe` derive product; writes maps.npz and scores.json to job["out"]."""
    import netCDF4

    from ...externals import mhm_tools_handler as tools
    from . import metrics

    spec = ADAPTERS[job["adapter"]]
    out = Path(job["out"])
    out.mkdir(parents=True, exist_ok=True)
    model_path, obs_path, obs_var = job["model"], job["observation"], job["variable"]
    with netCDF4.Dataset(obs_path) as handle:
        units = " ".join(str(getattr(handle[obs_var], "units", "")).split())
        if units not in UNITS or UNITS[units][0] not in spec["expects"]:
            raise ValueError(f"Observation units '{units}' do not fit {job['adapter']} "
                             f"(expected {' or '.join(spec['expects'])}).")
        ox, oy = gridded_axes(handle, obs_var)
        obs_x, obs_y = (np.asarray(handle[name][:], "f8") for name in (ox, oy))
        transposed = handle[obs_var].dimensions[-1] != ox
    obs_bounds = _bounds(obs_path, obs_var)
    model_bounds = read_bounds(model_path)
    with netCDF4.Dataset(model_path) as handle:
        mx, my = next((x, y) for x, y in (("easting", "northing"), ("lon", "lat"), ("x", "y"))
                      if x in handle.dimensions and y in handle.dimensions)
        model_x, model_y = (np.asarray(handle[name][:], "f8") for name in (mx, my))
    model_cell, obs_cell = abs(model_x[1] - model_x[0]), abs(obs_x[1] - obs_x[0])
    obs_coarser = obs_cell >= model_cell * (1 - 1e-6)
    grid_x, grid_y = (obs_x, obs_y) if obs_coarser else (model_x, model_y)
    shape = (len(grid_y), len(grid_x))
    model_bins = _bins(model_x, model_y, grid_x, grid_y) if obs_coarser else None
    obs_bins = None if obs_coarser else _bins(obs_x, obs_y, grid_x, grid_y)
    use_obs_windows = np.median(np.diff(obs_bounds, axis=1)) >= np.median(np.diff(model_bounds, axis=1))
    windows = obs_bounds if use_obs_windows else model_bounds
    flux = spec["kind"] == "flux"
    terms = job.get("terms") or {spec["model"]: 1.0}

    def model_read(a, b):
        with netCDF4.Dataset(model_path) as handle:
            data = sum(np.ma.filled(np.ma.asarray(handle[name][a:b], "f8"), np.nan) * weight
                       for name, weight in terms.items())
        if model_bins is not None:
            data = np.stack([regrid(step, model_bins, shape[0] * shape[1]).reshape(shape) for step in data])
        return data

    scale = UNITS[units][1]
    rate = UNITS[units][0] == "rate"
    obs_days = (obs_bounds[:, 1] - obs_bounds[:, 0]) / np.timedelta64(1, "D")

    def obs_read(a, b):
        with netCDF4.Dataset(obs_path) as handle:
            data = np.ma.filled(np.ma.asarray(handle[obs_var][a:b], "f8"), np.nan) * scale
        if transposed:
            data = np.swapaxes(data, 1, 2)
        if rate:  # a rate per day becomes the amount over each observation interval
            data = data * obs_days[a:b, None, None]
        if obs_bins is not None:
            data = np.stack([regrid(step, obs_bins, shape[0] * shape[1]).reshape(shape) for step in data])
        return data

    sim = _aggregate(model_read, model_bounds, windows, flux, len(windows), shape)
    obs = _aggregate(obs_read, obs_bounds, windows, flux, len(windows), shape)
    both = np.isfinite(sim) & np.isfinite(obs)
    sim[~both], obs[~both] = np.nan, np.nan
    used = both.reshape(len(windows), -1).any(axis=1)
    if not used.any():
        raise ValueError("The observation and the run share no complete window with data.")
    sim, obs, windows = sim[used], obs[used], windows[used]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        count = both[used].sum(axis=0)
        enough = count >= job.get("min_pairs", MIN_PAIRS)
        difference = sim - obs
        maps = {"bias": np.nanmean(difference, axis=0),
                "relative bias": np.nansum(difference, axis=0) / np.nansum(obs, axis=0),
                "pairs": count.astype("f4")}
        anomalies = (sim - np.nanmean(sim, 0)) * (obs - np.nanmean(obs, 0))
        maps["r"] = np.nanmean(anomalies, 0) / (np.nanstd(sim, 0) * np.nanstd(obs, 0))
        maps["KGE"], maps["RMSE"] = tools.kge_map(sim, obs, job.get("min_pairs", MIN_PAIRS)), \
            tools.rmse_map(sim, obs, job.get("min_pairs", MIN_PAIRS))
        for name in ("bias", "relative bias", "r"):
            maps[name] = np.where(enough, maps[name], np.nan)
        pattern = [tools.pattern_scores(s, o) for s, o in zip(sim, obs, strict=True)]
        basin_sim, basin_obs = np.nanmean(sim.reshape(len(sim), -1), 1), np.nanmean(obs.reshape(len(obs), -1), 1)
    np.savez(out / "maps.npz", x=grid_x, y=grid_y, **{name: np.asarray(v, "f4") for name, v in maps.items()})
    scores = {
        "windows": [[str(a), str(b)] for a, b in windows],
        "pattern": {name: [row[name] for row in pattern] for name in (pattern[0] if pattern else {})},
        "pattern_mean": {name: float(np.nanmean([row[name] for row in pattern])) for name in (pattern[0] or {})},
        "basin": {"sim": basin_sim.tolist(), "obs": basin_obs.tolist(),
                  **{k: v for k, v in metrics.evaluate(basin_sim, basin_obs, min_pairs=3).items()
                     if isinstance(v, (int, float, str))}},
        "grid": "observation" if obs_coarser else "model",
    }
    (out / "scores.json").write_text(json.dumps(scores, default=float), encoding="utf-8")
    return str(out)


def comparison_axes(maps, geographic) -> Axes:
    """The comparison grid of an `observe` result, for writing its maps."""
    x, y = maps["x"], maps["y"]
    if y[0] < y[-1]:
        raise ValueError("Write maps north first.")
    return Axes(x, y, _edges(x), _edges(y), geographic)


def tws_terms(index):
    """The storage variables of a scan and their F8 weight kinds, for the TWS sum."""
    roles = ("interception", "snowpack", "SWC_L", "sealedSTW", "unsatSTW", "satSTW")
    return {name: info.get("weight", "cell") for name, info in index["variables"].items()
            if info.get("output") in roles}


def write_tws(job) -> str:
    """Σ storages with F8 weights (fSealed per scene) per model interval, as a lat/lon
    netCDF `tws` (mm) for mhm-tools' TWSA evaluation (input_format "tws")."""
    import netCDF4

    root, path = Path(job["cache"]), job["model"]
    sealed = np.load(root / "sealed.npy") if (root / "sealed.npy").is_file() else None
    scenes = np.load(root / "scenes.npy") if (root / "scenes.npy").is_file() else None
    target = Path(job["out"]) / "tws.nc"
    target.parent.mkdir(parents=True, exist_ok=True)
    with netCDF4.Dataset(path) as source, netCDF4.Dataset(target, "w") as handle:
        steps = source["time"].shape[0]
        for name in ("time", "lat", "lon"):
            handle.createDimension(name, None if name == "time" else source.dimensions[name].size)
        handle.createDimension("bnds", 2)
        for name in ("time", "lat", "lon"):
            var = handle.createVariable(name, source[name].dtype, (name,))
            var.setncatts({key: source[name].getncattr(key) for key in source[name].ncattrs()})
            var[:] = source[name][:]
        bounds_name = getattr(source["time"], "bounds", "time_bnds")
        bounds = source[bounds_name][:]
        handle.createVariable(bounds_name, source[bounds_name].dtype, ("time", "bnds"))[:] = bounds
        # mhm-tools picks each reference window's steps with an end-inclusive slice, so a
        # stamp on an interval edge falls in two windows: stamp each interval's centre
        handle["time"][:] = bounds.mean(axis=1)
        tws = handle.createVariable("tws", "f4", ("time", "lat", "lon"), fill_value=np.float32(np.nan), zlib=True)
        tws.units = "mm"
        for step in range(steps):
            fraction = 0.0 if sealed is None else np.nan_to_num(sealed[scenes[step]])
            factors = {"cell": 1.0, "pervious": 1 - fraction, "sealed": fraction}
            tws[step] = sum(np.ma.filled(np.ma.asarray(source[name][step], "f8"), np.nan) * factors[weight]
                            for name, weight in job["terms"].items())
    return str(target)


def mhm_tools_adapter(job) -> dict:
    """TWS anomaly or snow-cover evaluation by mhm-tools; geographic runs only."""
    from ...externals import mhm_tools_handler as tools

    if not job.get("geographic"):
        raise ValueError("mhm-tools evaluates TWS anomaly and snow cover on regular lat/lon grids only; "
                         "this run is projected.")
    out = Path(job["out"])
    if job["adapter"] == "tws_anomaly":
        intervals = read_bounds(job["model"])
        first = intervals[0, 0].astype("datetime64[Y]") + (intervals[0, 0] != intervals[0, 0].astype("datetime64[Y]"))
        last = (intervals[-1, 1] - np.timedelta64(1, "s")).astype("datetime64[Y]")
        if (intervals[-1, 1].astype("datetime64[Y]") + 0) != intervals[-1, 1]:
            last = last - 1
        years = (int(str(first)), int(str(last)))  # the whole calendar years of the run
        if years[1] < years[0]:
            raise ValueError("The TWS anomaly needs at least one whole calendar year of output as its baseline.")
        return tools.twsa(write_tws(job), job["observation"], out, ref_var=job.get("variable"), baseline=years)
    return tools.snow_cover(job["model"], job["observation"], out, ref_var=job.get("variable"),
                            threshold=job.get("threshold", 0.0))


def run_job(job):
    """The `observe` derive product: gridded comparison or an mhm-tools adapter."""
    if job["adapter"] in ("tws_anomaly", "snow_cover"):
        return {key: str(path) for key, path in mhm_tools_adapter(job).items()}
    return compare(job)


def depth_terms(values, version, index, depth_mm, layers):
    """`terms` for soil moisture: the SWC_L variables weighted to [0, depth]."""
    weights = soil_weights(values, version, index, depth_mm)
    missing = [layer for layer in weights if f"SWC_L{layer:02d}" not in layers]
    if missing:
        raise ValueError(f"SWC_L{missing[0]:02d} is not written; turn on SWC_L in the output namelist.")
    return {f"SWC_L{layer:02d}": weight for layer, weight in weights.items()}


__all__ = ["ADAPTERS", "UNITS", "compare", "depth_terms", "run_job", "soil_weights", "windows_of"]
