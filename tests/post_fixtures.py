"""Synthetic mHM runs for the post-processing engine tests.

`make_run` writes files with the structure of real mHM output, checked against
Rapti (v5.13, geographic), mhm-debug (v5.13, projected) and the v6 examples:
UNLIMITED time, (1, ny, nx) chunks with deflate 6 and shuffle, `time_bnds`, -9999
fill, y descending. The values come from a known daily water balance per cell,
returned as `Truth` so engine products can be checked exactly:

    S[t+1] = S[t] + P - ET - Q - L          (L: karstic loss, in no output)

States are written as interval means of the end-of-day storage, fluxes as interval
sums (F3). The storage splits so that the area-weighted sum is S for any sealed
fraction fs: S = I + Sn + (1 - fs) * Spv + fs * Ssl, with Spv = Ssl = 0.85 S (F8).
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

FILL = -9999.0
STATES = {"interception": 0.05, "snowpack": 0.10, "SWC_L01": 0.30, "SWC_L02": 0.25,
          "unsatSTW": 0.20, "satSTW": 0.10, "sealedSTW": 0.85}
LAYER_MM = (300.0, 700.0)  # soil horizon thicknesses
POROSITY = 0.45            # SM_L = SWC_L / (POROSITY * thickness): relative saturation, as mHM writes it
RUNOFF = {"QD": 0.1, "QIf": 0.3, "QIs": 0.2, "QB": 0.4}


@dataclass
class Truth:
    days: np.ndarray                  # model days, datetime64[D]
    mask: np.ndarray                  # (ny, nx) valid L1 cells
    p: np.ndarray                     # (T, ny, nx) daily precipitation, mm
    pet: np.ndarray
    et: np.ndarray
    q: np.ndarray
    loss: np.ndarray                  # karstic loss, mm/day
    storage: np.ndarray               # (T + 1, ny, nx) weighted total, start of each day
    f_sealed: np.ndarray              # (ny, nx)
    intervals: list = field(default_factory=list)   # (first day, end day exclusive)
    cell_area_km2: np.ndarray | None = None   # (ny, nx); on the sphere when geographic
    files: dict = field(default_factory=dict)


def _intervals(days, step):
    if step == "day":
        return [(i, i + 1) for i in range(len(days))]
    months = days.astype("datetime64[M]")
    edges = [0, *np.flatnonzero(months[1:] != months[:-1]) + 1, len(days)]
    return list(itertools.pairwise(edges))


def _simulate(rng, shape, n_days, karst):
    p = rng.gamma(0.6, 6.0, (n_days, *shape))
    pet = 2.0 + 1.5 * np.sin(np.arange(n_days) / 58.0)[:, None, None] * np.ones(shape)
    et, q, loss = (np.zeros((n_days, *shape)) for _ in range(3))
    storage = np.zeros((n_days + 1, *shape))
    storage[0] = 120.0
    for t in range(n_days):
        s = storage[t] + p[t]
        et[t] = np.minimum(pet[t], 0.3 * s)
        q[t] = 0.05 * s
        loss[t] = karst * 0.02 * s
        storage[t + 1] = s - et[t] - q[t] - loss[t]
    return p, pet, et, q, loss, storage


def _axes(handle, geographic, ny, nx, cell):
    if geographic:
        names, x0, y0 = ("lon", "lat"), 84.0, 28.5
    else:
        names, x0, y0 = ("easting", "northing"), 500000.0, 3000000.0
    x = x0 + cell * (np.arange(nx) + 0.5)
    y = y0 - cell * (np.arange(ny) + 0.5)  # descending: north first
    handle.createDimension(names[0], nx)
    handle.createDimension(names[1], ny)
    handle.createDimension("bnds", 2)
    for name, values, axis in ((names[0], x, "X"), (names[1], y, "Y")):
        var = handle.createVariable(name, "f8", (name,))
        var.setncatts({"units": "degrees_east" if axis == "X" else "degrees_north"} if geographic
                      else {"units": "m", "standard_name": f"projection_{axis.lower()}_coordinate"})
        var.setncatts({"axis": axis, "bounds": f"{name}_bnds"})
        var[:] = values
        sign = 1 if axis == "X" else -1
        handle.createVariable(f"{name}_bnds", "f8", (name, "bnds"))[:] = np.stack(
            [values - sign * cell / 2, values + sign * cell / 2], axis=1)
    return names


def _time(handle, days, intervals, stamp, unit, dtype="i4", unlimited=True):
    handle.createDimension("time", None if unlimited else len(intervals))
    if "bnds" not in handle.dimensions:
        handle.createDimension("bnds", 2)
    start = days[0]
    scale = 24 if unit == "hours" else 1
    bounds = np.array([[(days[a] - start).astype(int), (days[b - 1] - start).astype(int) + 1]
                       for a, b in intervals]) * scale
    stamps = bounds[:, 1] if stamp == "end" else bounds[:, 0]
    time = handle.createVariable("time", dtype, ("time",))
    time.setncatts({"units": f"{unit} since {start!s} 00:00:00", "standard_name": "time",
                    "axis": "T", "bounds": "time_bnds"})
    time[:] = stamps
    handle.createVariable("time_bnds", dtype, ("time", "bnds"))[:] = bounds
    return bounds


def _grid_var(handle, name, dims, data, mask, dtype, units):
    var = handle.createVariable(name, dtype, dims, zlib=True, complevel=6, shuffle=True,
                                chunksizes=(1, *data.shape[1:]), fill_value=FILL)
    var.setncatts({"missing_value": np.array(FILL, dtype), "units": units, "coordinates": "lat lon"})
    var[:] = np.where(mask, data, FILL)


def make_run(folder, *, version="5.13", geographic=False, shape=(4, 6), days=62, step="day",
             stamp="end", karst=0.05, gauges=(398,), seed=0) -> Truth:
    """Write one synthetic run under `folder` and return the truth it was made from."""
    import netCDF4

    folder = Path(folder)
    rng = np.random.default_rng(seed)
    ny, nx = shape
    model_days = np.arange(np.datetime64("2000-01-01"), np.datetime64("2000-01-01") + days)
    p, pet, et, q, loss, storage = _simulate(rng, shape, days, karst)
    mask = np.ones(shape, bool)
    mask[0, -1] = mask[-1, 0] = False
    f_sealed = np.where(rng.random(shape) < 0.3, rng.uniform(0.05, 0.4, shape), 0.0)
    cell = 1 / 120 if geographic else 2000.0
    if geographic and not version.startswith("6"):
        top = np.radians(28.5 - cell * np.arange(ny))
        row = 6371.0088 ** 2 * np.radians(cell) * (np.sin(top) - np.sin(top - np.radians(cell)))
        area = np.repeat(row[:, None], nx, axis=1)
    else:
        area = np.full(shape, (cell / 1000) ** 2)
    truth = Truth(model_days, mask, p, pet, et, q, loss, storage, f_sealed,
                  _intervals(model_days, step), cell_area_km2=area)
    v6 = version.startswith("6")
    out = folder / "output"
    out.mkdir(parents=True, exist_ok=True)
    dtype, unit = ("f4", "days") if v6 else ("f8", "hours")
    flux_unit = "mm d-1" if step == "day" else "mm month-1"
    end_of_day = storage[1:]

    def interval(series, how):
        return np.stack([getattr(series[a:b], how)(axis=0) for a, b in truth.intervals])

    path = out / ("mhm_output.nc" if v6 else "mHM_Fluxes_States.nc")
    with netCDF4.Dataset(path, "w") as handle:
        x, y = _v6_axes(handle, ny, nx, cell) if v6 else _axes(handle, geographic, ny, nx, cell)
        _time(handle, model_days, truth.intervals, stamp, unit)
        dims = ("time", y, x)
        for name, share in STATES.items():
            _grid_var(handle, name, dims, interval(share * end_of_day, "mean"), mask, dtype, "mm")
        sm_unit = "1" if v6 else "mm mm-1"
        for index, thick in enumerate(LAYER_MM, start=1):
            share = STATES[f"SWC_L{index:02d}"]
            _grid_var(handle, f"SM_L{index:02d}", dims,
                      interval(share * end_of_day / (POROSITY * thick), "mean"), mask, dtype, sm_unit)
        swc = (STATES["SWC_L01"] + STATES["SWC_L02"]) * end_of_day
        _grid_var(handle, "SM_Lall", dims, interval(swc / (POROSITY * sum(LAYER_MM)), "mean"),
                  mask, dtype, sm_unit)
        fluxes = {"PET": pet, "aET": et, "Q": q, "preEffect": p, "recharge": 0.4 * q,
                  **{name: share * q for name, share in RUNOFF.items()}}
        for name, series in fluxes.items():
            _grid_var(handle, name, dims, interval(series, "sum"), mask, dtype, flux_unit)
    truth.files["mhm"] = path

    q_m3s = (q * mask * area * 1e6).sum(axis=(1, 2)) / 1000 / 86400
    if v6:
        truth.files["mrm"] = _v6_mrm(out / "mrm_output.nc", model_days, truth, q, mask, cell)
    else:
        truth.files["discharge"] = _discharge(out / "discharge.nc", model_days, gauges, q_m3s, rng)
        (out / "ConfigFile.log").write_text(
            f"   Domain   1\n     Total[km2]       {area[mask].sum():.3f}\n")
    truth.files["pre"] = _forcing(folder / "data" / "meteo", model_days, p, mask, geographic, cell, v6)
    truth.files["restart"] = _restart(folder / "restart" / "mHM_restart_001.nc", f_sealed, mask, area)
    return truth


def _v6_axes(handle, ny, nx, cell):
    x = 500000.0 + cell * (np.arange(nx) + 0.5)
    y = 3000000.0 - cell * (np.arange(ny) + 0.5)
    handle.createDimension("x", nx)
    handle.createDimension("y", ny)
    handle.createDimension("bnds", 2)
    for name, values, sign in (("x", x, 1), ("y", y, -1)):
        var = handle.createVariable(name, "f8", (name,))
        var.setncatts({"standard_name": f"projection_{name}_coordinate", "units": "m",
                       "axis": name.upper(), "bounds": f"{name}_bnds"})
        var[:] = values
        handle.createVariable(f"{name}_bnds", "f8", (name, "bnds"))[:] = np.stack(
            [values - sign * cell / 2, values + sign * cell / 2], axis=1)
    return "x", "y"


def _v6_mrm(path, days, truth, q, mask, cell):
    """Gridded routed discharge (`discharge`, m3 s-1) as the v6 app writes it; a cell's own runoff."""
    import netCDF4

    with netCDF4.Dataset(path, "w") as handle:
        _v6_axes(handle, *mask.shape, cell)
        _time(handle, days, truth.intervals, "end", "days")
        flow = np.stack([q[a:b].mean(axis=0) for a, b in truth.intervals]) * truth.cell_area_km2 / 86.4
        _grid_var(handle, "discharge", ("time", "y", "x"), flow, mask, "f4", "m3 s-1")
    return path


def _discharge(path, days, gauges, q_m3s, rng):
    """Daily gauge pairs, as mRM writes them: start stamps, padded units, -9999 gaps."""
    import netCDF4

    with netCDF4.Dataset(path, "w") as handle:
        _time(handle, days, [(i, i + 1) for i in range(len(days))], "start", "hours", unlimited=False)
        handle["time"].units = f"hours since       {days[0]!s} 00:00:00"
        for gauge in gauges:
            sim = handle.createVariable(f"Qsim_{gauge:010d}", "f8", ("time",), fill_value=FILL)
            obs = handle.createVariable(f"Qobs_{gauge:010d}", "f8", ("time",), fill_value=FILL)
            for var, kind in ((sim, "simulated"), (obs, "observed")):
                var.setncatts({"units": "m3 s-1", "missing_value": FILL,
                               "long_name": f"{kind} discharge at gauge {gauge:010d}"})
            sim[:] = q_m3s
            observed = q_m3s * rng.uniform(0.8, 1.2, q_m3s.shape)
            observed[::7] = FILL
            obs[:] = observed
    return path


def _forcing(folder, days, p, mask, geographic, cell, v6):
    """Precipitation as the plugin prepares it, on L2 = 2 x L1 (F10)."""
    import netCDF4

    path = folder / "pre.nc" if v6 else folder / "pre" / "pre.nc"
    path.parent.mkdir(parents=True, exist_ok=True)
    coarse = p[:, ::2, ::2]
    with netCDF4.Dataset(path, "w") as handle:
        names = _axes(handle, geographic, coarse.shape[1], coarse.shape[2], cell * 2)
        handle.createDimension("time", None)
        start = np.datetime64("1900-01-01")
        hours = (days - start).astype(int) * 24
        time = handle.createVariable("time", "f8", ("time",))
        time.setncatts({"units": "hours since 1900-01-01 00:00:00", "standard_name": "time",
                        "axis": "T", "bounds": "time_bnds"})
        time[:] = hours
        handle.createVariable("time_bnds", "f8", ("time", "bnds"))[:] = np.stack([hours, hours + 24], 1)
        var = handle.createVariable("pre", "f8", ("time", names[1], names[0]), fill_value=FILL)
        var.units = "mm"
        var[:] = coarse
    return path


def _restart(path, f_sealed, mask, area_km2):
    """L1_fSealed and the domain cell areas as MPR writes them (F14)."""
    import netCDF4

    path.parent.mkdir(parents=True, exist_ok=True)
    ny, nx = f_sealed.shape
    with netCDF4.Dataset(path, "w") as handle:
        handle.createDimension("L1_LandCoverPeriods", 1)
        handle.createDimension("ncols1", ny)
        handle.createDimension("nrows1", nx)
        sealed = handle.createVariable("L1_fSealed", "f8", ("L1_LandCoverPeriods", "ncols1", "nrows1"),
                                       fill_value=FILL)
        sealed.long_name = "fraction of Sealed area at level 1"
        sealed[0] = np.where(mask, f_sealed, FILL)
        handle.createVariable("L1_domain_mask", "i4", ("ncols1", "nrows1"))[:] = mask.astype(int)
        area = handle.createVariable("L1_domain_cellarea", "f8", ("ncols1", "nrows1"), fill_value=FILL)
        area[:] = np.where(mask, area_km2, FILL)  # unpack(..., mask, nodata) (mo_common_restart.f90:110)
    return path


def write_observation(truth, path, units="mm", scale=1.0, coarsen=2):
    """The fixture's aET, as monthly totals on a grid `coarsen` times coarser."""
    import netCDF4 as nc

    with nc.Dataset(truth.files["mhm"]) as source:
        aet = np.ma.filled(source["aET"][:], np.nan)
        x, y = source["easting"][:], source["northing"][:]
    days = truth.days
    months = days.astype("datetime64[M]")
    labels = np.unique(months)
    monthly = np.stack([aet[months == m].sum(0) for m in labels])
    ny, nx = monthly.shape[1] // coarsen, monthly.shape[2] // coarsen
    blocks = monthly.reshape(len(labels), ny, coarsen, nx, coarsen)
    coarse = np.nanmean(blocks, axis=(2, 4)) * scale
    with nc.Dataset(path, "w") as handle:
        handle.createDimension("time", len(labels))
        handle.createDimension("x", nx)
        handle.createDimension("y", ny)
        handle.createDimension("bnds", 2)
        starts = (labels.astype("datetime64[D]") - days[0]).astype(int)
        ends = ((labels + 1).astype("datetime64[D]") - days[0]).astype(int)
        time = handle.createVariable("time", "f8", ("time",))
        time.setncatts({"units": f"days since {days[0]} 00:00:00", "bounds": "time_bnds"})
        time[:] = starts
        handle.createVariable("time_bnds", "f8", ("time", "bnds"))[:] = np.stack([starts, ends], 1)
        handle.createVariable("x", "f8", ("x",))[:] = x.reshape(nx, coarsen).mean(1)
        handle.createVariable("y", "f8", ("y",))[:] = y.reshape(ny, coarsen).mean(1)
        var = handle.createVariable("et", "f4", ("time", "y", "x"), fill_value=np.float32(np.nan))
        var.units = units
        var[:] = coarse
    return labels
