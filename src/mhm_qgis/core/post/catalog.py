"""One run: its files, variables, time intervals and the checks it passes.

Files are resolved from the namelists that ran (`outputs.yml` `files`), relative to
the run's root, so current and archived runs read alike. Variables are what the file
holds joined with the output data model (`outputs.yml` `outputs`); time comes from
`time_bnds` only (F5, F18), never from the stamp or the unit text.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

import numpy as np

from ..datamodel import output_registry, switch_key
from ..handlers.store.paths import schemas_folder, version_key
from .runs import RunRef

AREA_TOLERANCE = 0.005  # F15: the domain area agrees with ConfigFile.log within 0.5 %


@dataclass(frozen=True)
class Variable:
    name: str                 # in the file; the registry key when not written
    output: str               # outputs.yml key; "" when the data model does not know it
    label: str
    group: str
    kind: str                 # flux, state or "" (unknown: instant and mean only)
    aggregate: tuple
    weight: str
    file: str = "mhm"
    layer: int | None = None
    units: str = ""
    fill: float | None = None
    present: bool = True
    message: str = ""


def value(values, dotted, index=1):
    """A namelist value by `group.key`, picking the domain's entry from an array."""
    group, _, key = dotted.lower().partition(".")
    found = values.get(group, {}).get(key)
    if isinstance(found, list):
        found = found[min(index, len(found)) - 1] if found else None
    return found


def _locate(ref, values, spec) -> Path | None:
    if "switch" in spec and not value(values, spec["switch"], ref.index):
        return None
    root = ref.setup if spec.get("input") else ref.root
    if "name" in spec:
        folder = root / (value(values, spec["dir"], ref.index) or "")
        names = spec["name"] if isinstance(spec["name"], list) else [spec["name"]]
        found = next((folder / name for name in names if (folder / name).is_file()), folder / names[0])
        return Path(os.path.normpath(found))
    relative = value(values, spec["key"], ref.index)
    return Path(os.path.normpath(root / relative)) if relative else None


def read_bounds(path, variable="time"):
    """The (start, end) of every interval as datetime64[s], from the time bounds."""
    import netCDF4

    with netCDF4.Dataset(path) as handle:
        time = handle[variable]
        name = getattr(time, "bounds", f"{variable}_bnds")
        if name not in handle.variables:
            raise ValueError(f"{Path(path).name} has no {name}; mHM always writes it (F5).")
        units = " ".join(time.units.split())  # discharge.nc pads its units (F18)
        dates = netCDF4.num2date(np.asarray(handle[name][:], dtype="f8"), units,
                                 getattr(time, "calendar", "standard"),
                                 only_use_cftime_datetimes=False, only_use_python_datetimes=True)
    return np.asarray(dates, dtype="datetime64[s]")


def _patterns(version):
    v6 = version_key(version) == "v6"
    for output, spec in output_registry(version)["outputs"].items():
        if spec.get("file") not in ("mhm", "mrm"):
            continue
        nc = spec.get("nc_v6", spec["nc"]) if v6 else spec["nc"]
        yield output, spec, re.compile(re.escape(nc).replace(re.escape("{layer:02d}"), r"(\d{2})") + "$")


@dataclass
class Run:
    ref: RunRef
    values: dict
    files: dict               # what the run wrote
    inputs: dict              # model inputs it read (the forcing P)

    def forcing(self):
        """(path, variable) of the precipitation forcing, or None."""
        path = self.inputs.get("pre")
        if path is None or not path.is_file():
            return None
        spec = output_registry(self.version)["files"][self.version]["pre"]
        name = spec["var"] if "." not in spec["var"] else value(self.values, spec["var"], self.ref.index)
        return path, name or "pre"

    @property
    def version(self) -> str:
        return version_key(self.ref.version)

    def exists(self, kind) -> bool:
        path = self.files.get(kind)
        return path is not None and path.is_file()

    @cached_property
    def bounds(self):
        """(start, end) per output interval of the mHM file."""
        return read_bounds(self.files["mhm"])

    @property
    def period(self):
        return self.bounds[0, 0], self.bounds[-1, 1]

    @property
    def step_days(self) -> float:
        """The output interval in days (the median; months vary)."""
        return float(np.median(np.diff(self.bounds, axis=1)) / np.timedelta64(1, "D"))

    def _file_variables(self, kind):
        if not self.exists(kind):
            return {}
        import netCDF4

        with netCDF4.Dataset(self.files[kind]) as handle:
            return {name: (getattr(var, "long_name", name), getattr(var, "units", ""),
                           float(var._FillValue) if "_FillValue" in var.ncattrs() else None)
                    for name, var in handle.variables.items()
                    if var.ndim == 3 and var.dimensions[0] == "time"}

    @cached_property
    def variables(self) -> list[Variable]:
        """Every variable the run wrote, then the data model's ones it did not write."""
        patterns = list(_patterns(self.version))
        result, written = [], set()
        for kind in ("mhm", "mrm"):
            for name, (label, units, fill) in self._file_variables(kind).items():
                match = next(((output, spec, found) for output, spec, pattern in patterns
                              if spec["file"] == kind and (found := pattern.match(name))), None)
                if match is None:
                    result.append(Variable(name, "", label, "other", "", ("instant", "mean"), "cell",
                                           kind, units=units, fill=fill))
                    continue
                output, spec, found = match
                written.add(output)
                result.append(Variable(name, output, label, spec["group"], spec["kind"],
                                       tuple(spec["aggregate"]), spec["weight"], kind,
                                       int(found.group(1)) if found.groups() else None, units, fill))
        for output, spec, _ in patterns:
            if output not in written:
                key = switch_key(self.version, output)
                message = f"Not written by this run. Turn on {key} in the output namelist." if key \
                    else "Not written by this run."
                result.append(Variable(output, output, output, spec["group"], spec["kind"],
                                       tuple(spec["aggregate"]), spec["weight"], spec["file"],
                                       present=False, message=message))
        return result

    def log_area_km2(self) -> float | None:
        """The domain area mHM reports at the end of ConfigFile.log (F15)."""
        if not self.exists("log"):
            return None
        totals = re.findall(r"Total\[km2\]\s+([-+.\dEe]+)",
                            self.files["log"].read_text(encoding="utf-8", errors="replace"))
        return float(totals[self.ref.index - 1]) if len(totals) >= self.ref.index else None

    def findings(self) -> list[str]:
        """What keeps this run from being analysed as is; [] when it is sound."""
        if not self.exists("mhm"):
            return [f"No mHM output file: {self.files.get('mhm') or 'not set in the namelists'}."]
        from . import grid

        found = []
        start, end = self.bounds[:, 0], self.bounds[:, 1]
        if not ((end > start).all() and (start[1:] >= end[:-1]).all()):
            found.append("time_bnds are not increasing intervals.")
        axes = grid.axes(self.files["mhm"])
        spec = output_registry(self.version)["files"][self.version]["mhm"]
        resolution = value(self.values, spec["resolution"], self.ref.index)
        if resolution and not np.isclose(axes.cell, float(resolution), rtol=1e-4):
            found.append(f"The output cell size {axes.cell:g} is not the namelist's L1 resolution {resolution}.")
        reported = self.log_area_km2()
        if reported:
            area = float((grid.domain_weights(self, axes)[0] * grid.cell_area_m2(axes)).sum()) / 1e6
            if abs(area - reported) > AREA_TOLERANCE * reported:
                found.append(f"The domain covers {area:.1f} km² on the output grid, but mHM reports "
                             f"{reported:.1f} km²: the grid or mask does not match.")
        if self.exists("discharge") and self.files["discharge"].name == "subdaily_discharge.nc" \
                and int(value(self.values, "mainconfig_mhm_mrm.timestep") or 1) != 1:
            found.append("subdaily_discharge.nc with a model step other than 1 h: its time axis is unverified.")
        return found


def open_run(ref: RunRef, values=None) -> Run:
    """The run of `ref`; `values` are its namelists, read from `ref.namelists` when None."""
    if values is None:
        from ...externals.nml_tools_handler import read_namelists

        values = read_namelists(ref.namelists, schemas_folder(ref.version))
    specs = output_registry(ref.version)["files"][version_key(ref.version)]
    located = {kind: (spec, path) for kind, spec in specs.items() if (path := _locate(ref, values, spec))}
    return Run(ref, values, {kind: path for kind, (spec, path) in located.items() if not spec.get("input")},
               {kind: path for kind, (spec, path) in located.items() if spec.get("input")})
