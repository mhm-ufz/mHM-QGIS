# -*- coding: utf-8 -*-
"""Resolve prepared meteorology forcing for the display selector.

Kept free of QGIS so time-coordinate selection stays testable; the canvas half lives
in :mod:`mhm_qgis.qgis_bridge.display.meteo`.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from functools import lru_cache

from ..handlers.file.netcdf import time_axis
from ..post import step_for_when
from pathlib import Path

from ..handlers.store.layout import METEO_VARIABLES, expected_meteo_outputs

#: Historical forcing files used days since this epoch.
TIME_EPOCH = date(1900, 1, 1)

#: Only variables present on disk are offered.
DISPLAY_VARIABLES = METEO_VARIABLES

VARIABLE_LABELS = {
    "pre": "Precipitation",
    "tavg": "Mean temperature",
    "tmin": "Minimum temperature",
    "tmax": "Maximum temperature",
    "pet": "Potential evapotranspiration",
}


@dataclass(frozen=True)
class MeteoOutput:
    """One timestep of one prepared forcing variable."""

    path: Path
    variable: str
    band: int
    when: datetime
    name: str


def meteo_output_path(project_folder, variable: str) -> Path | None:
    """Return the prepared NetCDF for one variable, when it exists on disk."""
    paths = expected_meteo_outputs(project_folder).get(variable)
    if paths is None:
        return None
    netcdf = Path(paths["netcdf"])
    return netcdf if netcdf.is_file() else None


def available_meteo_variables(project_folder) -> list[str]:
    """Return the forcing variables actually prepared for this project.

    `expected_meteo_outputs` names every variable including `pet`, which is only
    written when the run asked for it, so each candidate is checked on disk.
    """
    return [
        variable
        for variable in DISPLAY_VARIABLES
        if variable in METEO_VARIABLES
        and meteo_output_path(project_folder, variable) is not None
    ]



@lru_cache(maxsize=16)
def _cached_steps(path, modified, size):
    """Decode each unchanged file's actual time coordinate once."""
    return tuple(time_axis(path))


def meteo_steps(path):
    """Return native timestamps, including subdaily and calendar intervals."""
    if not path:
        return ()
    path = Path(path)
    stat = path.stat()
    return _cached_steps(str(path), stat.st_mtime_ns, stat.st_size)


def meteo_time_range(path):
    """Return the first and last prepared timestamps."""
    steps = meteo_steps(path)
    return (steps[0], steps[-1]) if steps else None


def project_time_range(project_folder):
    """Return the first available variable's time span."""
    for variable in available_meteo_variables(project_folder):
        span = meteo_time_range(meteo_output_path(project_folder, variable))
        if span:
            return span
    return None


def resolve_meteo_output(project_folder, variable: str, when):
    """Resolve a band using the file's actual time coordinate."""
    path = meteo_output_path(project_folder, variable)
    steps = meteo_steps(path)
    if isinstance(when, date) and not isinstance(when, datetime):
        when = datetime.combine(when, datetime.min.time())
    if not steps or not steps[0] <= when <= steps[-1]:
        return None
    index = step_for_when(steps, when)
    label = VARIABLE_LABELS.get(variable, variable)
    return MeteoOutput(path, variable, index + 1, steps[index], f"{label} ({variable})")


__all__ = [
    "DISPLAY_VARIABLES",
    "MeteoOutput",
    "VARIABLE_LABELS",
    "available_meteo_variables",
    "meteo_output_path",
    "meteo_time_range",
    "project_time_range",
    "resolve_meteo_output",
    "meteo_steps",
]
