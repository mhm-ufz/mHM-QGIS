"""Before mHM runs: what it would stop on in a saved setup, by Set stage (`rules` holds the checks).

Reads the first domain's namelist values and prepared files, and the period and gauges Set
saved; a file the namelist names but the setup lacks is skipped (`datamodel.check` reports it).
"""
from __future__ import annotations

import math
from datetime import date, timedelta
from functools import cached_property
from pathlib import Path

import numpy as np

from .. import datamodel
from ..grid.model import integer_resolution_factor, read_header_file
from ..handlers.state import processing
from ..handlers.state.nml_settings import load_settings
from ..handlers.store import layout
from . import rules

LAND_COVER_IDS = (1, 2, 3)  # forest, sealed, pervious
D8 = (0, 1, 2, 4, 8, 16, 32, 64, 128)
# v5 forcing file stem -> its directories_mhm folder
V5_FORCING = {"pre": "dir_precipitation", "tavg": "dir_temperature", "pet": "dir_referenceet",
              "tmin": "dir_mintemperature", "tmax": "dir_maxtemperature", "net_rad": "dir_netradiation",
              "eabs": "dir_absvappressure", "windspeed": "dir_windspeed", "ssrd": "dir_radiation",
              "strd": "dir_radiation", "tann": "dir_temperature"}
TIMES = ("sim_start", "eval_start", "sim_end")
V6_FORCING = ("pre", "pet", "temp", "tann", "tmin", "tmax", "ssrd", "strd", "netrad", "eabs", "wind")
_BLOCK = 366  # time steps read at once


def check(setup_folder, stage=None) -> list[str]:
    """mHM's objections to the saved setup: one stage of STAGES, or all of them."""
    setup = _Setup(setup_folder)
    return list(dict.fromkeys(problem for name in ([stage] if stage else STAGES) for problem in STAGES[name](setup)))


class _Setup:
    def __init__(self, folder):
        self.folder = Path(folder)
        self.v6 = layout.is_v6(self.folder)
        self.nml = datamodel.read_namelists(self.folder)

    def get(self, group, key, default=None):
        """The first domain's value."""
        value = self.nml.get(group, {}).get(key, default)
        return value[0] if isinstance(value, list) and value else value

    def path(self, group, key, name=""):
        """A namelist file (v5: a folder and `name`), or None when unset or absent."""
        value = self.get(group, key)
        path = self.folder / str(value) / name if value else None
        return path if path and path.is_file() else None

    def morph(self, name):
        return self.path("directories_general", "dir_morpho", name)

    @cached_property
    def dem(self):
        """(values, cell size) of the DEM, or None."""
        path = self.path("config_input", "dem_path") if self.v6 else self.morph("dem.asc")
        return (read(path, self.get("config_input", "dem_var")), cell(path)) if path else None

    @cached_property
    def domain(self):
        """The L0 cells mHM computes: v6 its morph mask (fdir and the rest are cut to it), else the DEM's."""
        path = self.v6 and self.path("config_input", "morph_mask_path")
        return read(path, self.get("config_input", "morph_mask_var")) == 1 if path else ~np.isnan(self.dem[0])

    @cached_property
    def settings(self):
        return load_settings(self.folder)

    @cached_property
    def period(self):
        """(first, last) simulated day, warming included: Set Periods' dates, else the namelist's;
        None before either holds one."""
        if (time := self.settings.get("time") or {}).get("sim_start"):
            return tuple(date.fromisoformat(str(time[key])[:10]) for key in ("sim_start", "sim_end"))
        if self.v6:
            times = [self.get("config_time", key) for key in ("sim_start", "sim_end")]
            return tuple(date.fromisoformat(str(time)[:10]) for time in times) if all(times) else None
        if not (per := self.get("time_periods", "eval_per")):
            return None
        start = date(per["yStart"], per["mStart"], per["dStart"])
        return start - timedelta(days=int(self.get("time_periods", "warming_days", 0))), \
            date(per["yEnd"], per["mEnd"], per["dEnd"])

    def forcings(self):
        """(name, file, variable) of every forcing file the namelist points at."""
        if self.v6:
            return [(name, path, self.get("config_input", f"{name}_var")) for name in V6_FORCING
                    if (path := self.path("config_input", f"{name}_path"))]
        return [(name, path, name) for name, folder in V5_FORCING.items()
                if (path := self.path("directories_mhm", folder, f"{name}.nc"))]


def _variable(dataset, name):
    """`name`, else the dataset's first gridded variable, rows top-down."""
    data = dataset[name] if name in dataset else next(
        v for v in dataset.data_vars.values() if v.ndim >= 2 and not {"bnds", "nv", "bounds"} & set(v.dims))
    y = data.dims[-2]
    if y in dataset.coords and dataset[y].values[0] < dataset[y].values[-1]:
        data = data.isel({y: slice(None, None, -1)})
    return data


def read(path, variable=None):
    """A grid as float, NaN for no data, (…, y, x) top-down."""
    if path.suffix == ".asc":
        values = np.loadtxt(path, skiprows=6)
        values[values == read_header_file(path)["nodata_value"]] = np.nan
        return values
    import xarray as xr

    with xr.open_dataset(path) as dataset:
        return _variable(dataset, variable).values.astype(float)


def cell(path):
    """The cell size as mHM reads it: the ASCII header, or (x[n] - x[1]) / (n - 1)."""
    if path.suffix != ".nc":
        return read_header_file(path)["cellsize"]
    import xarray as xr

    with xr.open_dataset(path) as dataset:
        x = dataset[_variable(dataset, None).dims[-1]].values
        return abs(float(x[-1]) - float(x[0])) / (len(x) - 1)


def _lut(path, column):
    """The IDs in a class table's `column`, from its declared row count after two header lines; None
    without a table."""
    if path is None:
        return None
    lines = path.read_text(encoding="utf-8").splitlines()
    ids = set()
    for line in lines[2:]:
        try:
            ids.add(int(float(line.split()[column])))
        except (IndexError, ValueError):
            break  # notes after the table
    return ids


def _coarse(mask, factor, bottom):
    """Coarse cells holding a valid fine cell; v5 starts blocks top-left, v6 bottom-left."""
    rows, cols = -(-mask.shape[0] // factor) * factor, -(-mask.shape[1] // factor) * factor
    padded = np.zeros((rows, cols), bool)
    top = rows - mask.shape[0] if bottom else 0
    padded[top:top + mask.shape[0], :mask.shape[1]] = mask
    return padded.reshape(rows // factor, factor, cols // factor, factor).any(axis=(1, 3))


def _grid(setup):
    if setup.dem is None:
        return []
    if setup.v6:
        meteo = setup.path("config_input", "pre_path")
        return rules.resolutions(True, setup.dem[1], setup.get("config_resolution", "hydro") or None,
                                 setup.get("config_resolution", "route") or None, cell(meteo) if meteo else None)
    header = setup.path("directories_mhm", "dir_precipitation", "header.txt")
    return rules.resolutions(False, setup.dem[1], setup.get("mainconfig", "resolution_hydrology"),
                             setup.get("mainconfig_mhm_mrm", "resolution_routing"),
                             read_header_file(header)["cellsize"] if header else None)


def _morphology(setup):
    if setup.dem is None:
        return []
    mask, problems = setup.domain, []

    def classes(name, path, variable=None, allowed=None, source="its lookup table"):
        if path:
            problems.extend(rules.classes(name, read(path, variable), mask, allowed, source))

    if setup.v6:
        get, inputs, mpr = setup.get, "config_input", "config_mpr"
        classes("Flow direction", setup.path(inputs, "fdir_path"), get(inputs, "fdir_var"), D8, "the D8 codes")
        classes("Flow accumulation", setup.path(inputs, "facc_path"), get(inputs, "facc_var"))
        classes("Geology", setup.path(inputs, "geo_class_path"), get(inputs, "geo_class_var"),
                _lut(setup.path(mpr, "geo_lut_path"), 1), "geo_lut_path")
        classes("Soil", setup.path(inputs, "soil_horizon_class_path") or setup.path(inputs, "soil_class_path"),
                get(inputs, "soil_class_var"), _lut(setup.path(mpr, "soil_lut_path"), 0), "soil_lut_path")
        classes("Land cover", setup.path(mpr, "land_cover_path"), get(mpr, "land_cover_var"), LAND_COVER_IDS,
                "classes 1-3")
        step = int(get(mpr, "lai_time_step", 0))
        if step == 0:
            classes("LAI class", setup.path(inputs, "lai_class_path"), get(inputs, "lai_class_var"),
                    _lut(setup.path(mpr, "lai_lut_path"), 0), "lai_lut_path")
        elif path := setup.path(mpr, "lai_path"):
            problems += rules.lai(True, read(path, get(mpr, "lai_var")), mask, step == 1)
        return problems
    common = ("directories_general", "dircommonfiles")
    classes("Flow direction", setup.morph("fdir.asc"), None, D8, "the D8 codes")
    classes("Flow accumulation", setup.morph("facc.asc"))
    if int(setup.get("soildata", "iflag_soildb", 0)):
        soil = _lut(setup.path(*common, "soil_classdefinition_iFlag_soilDB_1.txt"), 0)
        for horizon in range(1, int(setup.get("soildata", "nsoilhorizons_mhm", 1)) + 1):
            classes(f"Soil horizon {horizon}", setup.morph(f"soil_class_horizon_{horizon:02d}.asc"), None, soil,
                    "soil_classdefinition_iFlag_soilDB_1.txt")
    else:
        classes("Soil", setup.morph("soil_class.asc"), None,
                _lut(setup.path(*common, "soil_classdefinition.txt"), 0), "soil_classdefinition.txt")
    if table := setup.path(*common, "geology_classdefinition.txt"):
        classes("Geology", setup.morph("geology_class.asc"), None, _lut(table, 1), "geology_classdefinition.txt")
        declared = int(table.read_text(encoding="utf-8").split()[1])
        rows = setup.nml.get("geoparameter", {}).get("geoparam", [])
        problems += rules.geology_units(declared, next((i for i, row in enumerate(rows) if row[0] == -9999), len(rows)))
    for name in setup.nml.get("lcover", {}).get("lcoverfname", []):
        classes(f"Land cover {name}", setup.path("directories_general", "dir_lcover", name), None, LAND_COVER_IDS,
                "classes 1-3")
    step = int(setup.get("lai_data_information", "timestep_lai_input", 0))
    if step == 0:
        classes("LAI class", setup.morph("LAI_class.asc"), None,
                _lut(setup.path(*common, "LAI_classdefinition.txt"), 0), "LAI_classdefinition.txt")
    elif path := setup.path("directories_mpr", "dir_gridded_lai", "lai.nc"):
        problems += rules.lai(False, read(path, "lai"), mask, step == 1)
    return problems


def _forcing(setup, values):
    if setup.period is None:
        return []
    start, end = setup.period
    step = int(setup.get("config_time", "time_step", 24)) if setup.v6 else 24
    mask = None
    if values and setup.dem is not None:
        l2 = [cell(path) for _name, path, _variable in setup.forcings()[:1]]
        factor = l2 and integer_resolution_factor(l2[0], setup.dem[1])
        mask = _coarse(setup.domain, factor, setup.v6) if factor else None
    import xarray as xr

    problems = []
    for name, path, variable in setup.forcings():
        with xr.open_dataset(path) as dataset:
            data, holes, low, high = _variable(dataset, variable), [], None, None
            if mask is not None and data.shape[-2:] != mask.shape:
                problems.append(f"{name}: grid {data.shape[-2:]} does not match the meteo grid {mask.shape} "
                                "mHM derives from the DEM.")
            elif mask is not None:
                for first in range(0, data.shape[0], _BLOCK):
                    block = data[first:first + _BLOCK].values[..., mask]
                    holes += (first + np.flatnonzero(np.isnan(block).any(axis=1))).tolist()
                    if (finite := block[~np.isnan(block)]).size:
                        low = min(finite.min(), low if low is not None else np.inf)
                        high = max(finite.max(), high if high is not None else -np.inf)
            problems += rules.forcing(setup.v6, name, dataset["time"].encoding.get("units"), dataset["time"].values,
                                      start, end, step_hours=step, low=low, high=high, holes=holes)
    return problems


def _period(setup):
    problems = _forcing(setup, values=False)
    if not setup.v6 and setup.period and (scenes := setup.nml.get("lcover", {})).get("lcoveryearstart"):
        start, end = setup.period
        problems += rules.land_cover_years(scenes["lcoveryearstart"][0], scenes["lcoveryearend"][-1],
                                           start.year, end.year)
    return problems


def _gauges(setup):
    grid = None if setup.v6 else setup.morph("idgauges.asc")
    domains = setup.settings.get("domains") or [{}]
    ids = domains[0].get("gauge_ids") or setup.get("evaluation_gauges", "gauge_id", [])
    missing = [f"Gauge {gauge.get('gauge_id')}: {gauge['gauge_path']} is missing; mHM stops ('File does not exist')."
               for gauge in setup.settings.get("gauges") or []
               if gauge.get("gauge_path") and not (setup.folder / gauge["gauge_path"]).is_file()]
    return missing + (rules.gauges([int(gauge) for gauge in ids], read(grid)) if grid else [])


def _stale(setup):
    """What Set saved but the saved namelist does not hold yet: mHM runs the namelist."""
    fix, problems = "open Edit Namelists and save it before running mHM", []
    time = setup.settings.get("time") or {}
    if setup.v6:
        held = tuple(str(setup.get("config_time", key) or "")[:16] for key in TIMES)
        wanted = tuple(str(time.get(key) or "")[:16] for key in TIMES)
    elif per := setup.get("time_periods", "eval_per"):
        evaluation = date(per["yStart"], per["mStart"], per["dStart"])
        held = (str(evaluation - timedelta(days=int(setup.get("time_periods", "warming_days", 0)))),
                str(evaluation), str(date(per["yEnd"], per["mEnd"], per["dEnd"])))
        wanted = tuple(str(time.get(key) or "")[:10] for key in TIMES)
    else:
        held = wanted = ()
    if time.get("sim_start") and all(held) and held != wanted:
        problems.append(f"The namelist simulates {' / '.join(held)} (start / evaluation / end), "
                        f"Set Periods saved {' / '.join(wanted)}: {fix}.")
    l1 = ((processing.section(setup.folder, "grid", {}) or {}).get("l1_header") or {}).get("cellsize")
    held_l1 = setup.get("config_resolution", "hydro") if setup.v6 else setup.get("mainconfig", "resolution_hydrology")
    if l1 and held_l1 and not math.isclose(float(held_l1), float(l1), rel_tol=1e-9):
        problems.append(f"The namelist's L1 cell size is {held_l1}, Set Resolution saved {l1}: {fix}.")
    return problems


STAGES = {"namelist": _stale, "grid": _grid, "morphology": _morphology, "period": _period, "gauges": _gauges,
          "meteo": lambda setup: _forcing(setup, values=True)}
