"""mHM's own input checks as pure functions; each returns the problems mHM would stop on.

v5 is mHM 5.13 (`src/`), v6 is mHM 6 with FORCES. Arrays are float with NaN for no data,
rows top-down; `mask` is the DEM's valid cells on the same grid.
"""
from __future__ import annotations

import math
import re
from datetime import timedelta

import numpy as np

from ..grid.model import integer_resolution_factor

# read_nc bounds per forcing file (v5 meteo/mo_meteo_handler.f90:671-806)
FORCING_BOUNDS = {
    "pre": (0, 2000), "tavg": (-100, 100), "pet": (0, 1000), "tmin": (-100, 100), "tmax": (-100, 100),
    "net_rad": (-500, 1500), "eabs": (0, 15000), "windspeed": (0, 250), "ssrd": (0, 1500),
    "strd": (0, 1500), "tann": (-100, 100),
}
_UNITS = re.compile(r"(days|hours|minutes|seconds) since \d{4}-\d{1,2}-\d{1,2}( \d{1,2}:\d{1,2}(:\d{1,2})?)?")
_DAY, _HOUR = timedelta(days=1), timedelta(hours=1)


def _nint(value):
    return math.floor(value + 0.5)  # Fortran nint/anint (half away from zero) for value >= 0


def resolutions(v6, l0, l1=None, l11=None, l2=None) -> list[str]:
    """Cell sizes as the namelist and header.txt hold them, against L0 and each other."""
    problems = []
    for name, value in (("L1 (hydrology)", l1), ("L11 (routing)", l11), ("L2 (meteorology)", l2)):
        # v5 common/mo_common_grid.f90:534-543, v6 FORCES check_factor: |anint(r) - r| <= 1e-7
        if value is not None and integer_resolution_factor(value, l0) is None:
            problems.append(f"{name} cell size {value!r} is not a whole multiple of L0 {l0!r} "
                            f"(ratio {value / l0!r}, mHM allows 1e-7): mHM stops with "
                            "'Two resolutions size do not confirm'.")
    if l1 and l11 and not v6:
        # v5 common/mo_common_mHM_mRM_read_config.f90:281-291
        factor = l11 / l1
        if _nint(100 * factor) > 100 and _nint(math.fmod(factor, 2.0) * 100) != 0:
            problems.append(f"L11/L1 is {factor:g}: mHM 5 needs routing equal to the hydrology cell or an "
                            "even multiple of it ('Resolution of routing is not a multiple of hydrological "
                            "model resolution').")
    for name, value in (("L11", l11), ("L2", l2)) if v6 and l1 else ():
        # v6 FORCES scaler init: the two grids nest in either direction
        if value and not (integer_resolution_factor(value, l1) or integer_resolution_factor(l1, value)):
            problems.append(f"{name} cell size {value!r} and L1 {l1!r} do not nest (ratio {value / l1:g}): "
                            "mHM 6 stops with 'Two resolutions size do not confirm'.")
    return problems


def classes(name, values, mask, allowed=None, source="its lookup table") -> list[str]:
    """No data inside the DEM mask, and only IDs `allowed` (v5 MPR/mo_read_wrapper.f90:402-410,
    mo_mpr_startup.f90:189, mRM/mo_mrm_init.f90:561-574; v6 MPR LUT checks)."""
    inside = np.asarray(values)[..., mask]
    problems = []
    if holes := int(np.isnan(inside).sum()):
        problems.append(f"{name}: {holes} cells inside the DEM mask hold no data; mHM stops (masking problem).")
    if allowed is not None:
        unknown = sorted(set(np.unique(inside[~np.isnan(inside)]).astype(int).tolist()) - set(allowed))
        if unknown:
            problems.append(f"{name}: IDs {unknown[:10]} are not in {source}; mHM stops ('Class … is missing').")
    return problems


def lai(v6, values, mask, monthly) -> list[str]:
    """Gridded LAI (v5 MPR/mo_prepare_gridded_lai.f90:188-237 and read_nc bounds; v6 MPR:2537-2656)."""
    problems = []
    if monthly and len(values) != 12:
        problems.append(f"LAI: monthly LAI needs 12 slices, the file has {len(values)}.")
    problems += classes("LAI", values, mask)
    if not v6:  # v6 clamps to [1e-10, 30]
        inside = np.asarray(values)[..., mask]
        inside, low = inside[~np.isnan(inside)], 0 if monthly else 1e-10
        if inside.size and (inside.min() < low or inside.max() > 30):
            problems.append(f"LAI: values inside the DEM mask must lie in [{low:g}, 30]; "
                            f"found {inside.min():g} to {inside.max():g}.")
    return problems


def land_cover_years(first, last, start, end) -> list[str]:
    """v5 common/mo_common_read_config.F90:248-257: the scenes span warming start to simulation end."""
    problems = []
    if first > start:
        problems.append(f"Land cover starts in {first}, after the warming period starts ({start}): "
                        "mHM stops ('Land cover for warming period is missing').")
    if last < end:
        problems.append(f"Land cover ends in {last}, before the simulation ends ({end}): "
                        "mHM stops ('Land cover period shorter than modelling period').")
    return problems


def geology_units(classes_count, parameters_count) -> list[str]:
    """v5 mHM/mo_startup.f90:160-165: one GeoParam row per geology class."""
    if classes_count == parameters_count:
        return []
    return [(f"Geology: {classes_count} classes in geology_classdefinition.txt but {parameters_count} "
             "GeoParam rows in mhm_parameter.nml; mHM stops ('Mismatch: Number of geological units').")]


def forcing(v6, name, units, times, start, end, *, step_hours=24, low=None, high=None, holes=()) -> list[str]:
    """A forcing file's time axis and values (v5 common/mo_read_nc.f90; v6 Meteo validate_step and
    input%time_index). `holes` are the step indices with no data inside the meteo mask."""
    problems = []
    if not v6 and not _UNITS.fullmatch(units or ""):
        problems.append(f"{name}: time units {units!r} are not '<days|hours|…> since YYYY-MM-DD[ HH:MM:SS]'.")
    times = np.asarray(times, dtype="datetime64[s]")
    if len(times) < 2:
        return [*problems, f"{name}: needs at least 2 time steps, the file has {len(times)}."]
    steps = set(np.diff(times).tolist())
    allowed = ({_DAY, step_hours * _HOUR} if v6 else {_DAY, _HOUR})
    if len(steps) != 1 or steps.pop() not in allowed:
        problems.append(f"{name}: time steps must all be daily or hourly{' (the model step)' if v6 else ''}.")
    first, last = times[0].astype("datetime64[D]"), times[-1].astype("datetime64[D]")
    if first > np.datetime64(start, "D") or last < np.datetime64(end, "D"):
        problems.append(f"{name}: the file covers {first} to {last}, not the period {start} to {end}.")
    if holes and (not v6 or holes[0] == 0):  # v6 takes its mask from the first step
        problems.append(f"{name}: no data inside the domain at {len(holes)} time steps (first: {holes[0]}).")
    lower, upper = FORCING_BOUNDS.get(name, (None, None))
    if not v6 and lower is not None and low is not None and (low < lower or high > upper):
        problems.append(f"{name}: values {low:g} to {high:g} leave mHM's bounds [{lower}, {upper}].")
    return problems


def gauges(ids, id_grid) -> list[str]:
    """v5 mRM/mo_mrm_read_config.f90:141 and mo_mrm_read_data.f90:193-219."""
    problems = [f"{len(ids)} evaluation gauges; mHM 5 allows at most 200."] if len(ids) > 200 else []
    present = set(np.unique(id_grid[~np.isnan(id_grid)]).astype(int).tolist())
    problems += [f"Gauge ID {gauge} is not in idgauges.asc; mHM stops." for gauge in ids if gauge not in present]
    return problems
