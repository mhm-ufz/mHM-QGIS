"""Post-processing: turn a finished mHM run into something displayable.

Owns the question "what did this run produce, and what does one timestep of it
look like". Reads through :mod:`mhm_qgis.core.handlers.file.netcdf`; the answer is
handed to :mod:`mhm_qgis.qgis_bridge.display` to put on the canvas.
"""
from __future__ import annotations

import bisect
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ...core.handlers.store.paths import output_folder
from ..handlers.file.netcdf import (
    GridVariable,
    grid_variables,
    output_files,
    read_slice,
    time_axis,
)


@dataclass(frozen=True)
class DisplayRaster:
    """One timestep of one output variable, ready for the canvas."""

    data: object
    name: str
    variable: str
    when: datetime


def output_source(project_folder) -> Path | None:
    """Return the NetCDF this project's run wrote, if there is one.

    v6 names its files in the namelist while v5.13 only names the directory, so
    the folder is globbed rather than either convention being assumed.
    """
    candidates = output_files(output_folder(project_folder))
    return candidates[0] if candidates else None


def available_variables(project_folder) -> list[GridVariable]:
    """Return the displayable variables of this project's output."""
    source = output_source(project_folder)
    return grid_variables(source) if source is not None else []


def find_variable(project_folder, name: str) -> GridVariable | None:
    """Return one output variable by name."""
    return next(
        (v for v in available_variables(project_folder) if v.name == name), None
    )


def simulation_steps(project_folder) -> list[datetime]:
    """Return every timestep the output covers.

    The spacing is whatever the run used -- daily in one setup, monthly in
    another -- so the steps are listed rather than derived from a period.
    """
    source = output_source(project_folder)
    if source is None:
        return []
    variables = grid_variables(source)
    if not variables:
        return []
    return time_axis(source, variables[0].time_dim)


def simulation_period(project_folder) -> tuple[datetime, datetime] | None:
    """Return the first and last timestep of the output."""
    steps = simulation_steps(project_folder)
    return (steps[0], steps[-1]) if steps else None


def step_for_when(steps: list[datetime], when) -> int:
    """Return the index of the step at or before a moment, clamped."""
    if not steps:
        return 0
    if isinstance(when, datetime):
        moment = when
    else:
        moment = datetime(when.year, when.month, when.day)
    position = bisect.bisect_right(steps, moment) - 1
    return max(0, min(position, len(steps) - 1))


def resolve_output(project_folder, name: str, step: int, crs=None) -> DisplayRaster | None:
    """Return one timestep of one output variable, georeferenced by `crs`.

    mHM output carries no CRS of its own, so `crs` decides where the raster
    lands; without it the grid is returned unplaced.
    """
    source = output_source(project_folder)
    variable = find_variable(project_folder, name)
    if source is None or variable is None:
        return None
    steps = time_axis(source, variable.time_dim)
    if not steps:
        return None
    step = max(0, min(int(step), len(steps) - 1))
    data = read_slice(source, variable, step, crs=crs)
    return DisplayRaster(
        data=data,
        name=f"{variable.label} ({variable.name})",
        variable=variable.name,
        when=steps[step],
    )


__all__ = [
    "DisplayRaster",
    "available_variables",
    "find_variable",
    "output_source",
    "resolve_output",
    "simulation_period",
    "simulation_steps",
    "step_for_when",
]
