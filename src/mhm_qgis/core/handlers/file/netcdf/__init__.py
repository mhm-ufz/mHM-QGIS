# -*- coding: utf-8 -*-
"""Read gridded NetCDF without assuming how it was written.

mHM output does not name its dimensions consistently -- one file uses
``(time, y, x)`` and another ``(time, northing, easting)`` -- encodes time in
whatever unit the run used, and carries no CRS at all. So dimensions are
discovered, the time axis is decoded with its own units, and the caller supplies
the CRS.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

#: Coordinate ``standard_name`` values that identify each spatial axis.
_Y_STANDARD_NAMES = ("projection_y_coordinate", "latitude", "grid_latitude")
_X_STANDARD_NAMES = ("projection_x_coordinate", "longitude", "grid_longitude")
#: Fallback dimension names, used when the file declares no standard names.
_Y_NAMES = ("y", "northing", "lat", "latitude", "rlat")
_X_NAMES = ("x", "easting", "lon", "longitude", "rlon")


@dataclass(frozen=True)
class GridVariable:
    """One time-varying 2-D variable inside a NetCDF file."""

    name: str
    label: str
    units: str
    time_dim: str
    y_dim: str
    x_dim: str

    @property
    def display_label(self) -> str:
        """Return a label naming the variable and its units."""
        return f"{self.label} [{self.units}]" if self.units else self.label


def _dataset(path):
    """Open a NetCDF file for metadata reading."""
    from netCDF4 import Dataset

    return Dataset(str(path))


def _axis_role(dataset, dim: str) -> str | None:
    """Return 'y' or 'x' for one dimension, by standard name then by name."""
    variable = dataset.variables.get(dim)
    if variable is not None:
        standard = getattr(variable, "standard_name", "").lower()
        if standard in _Y_STANDARD_NAMES:
            return "y"
        if standard in _X_STANDARD_NAMES:
            return "x"
        axis = getattr(variable, "axis", "").upper()
        if axis in ("Y", "X"):
            return axis.lower()
    lowered = dim.lower()
    if lowered in _Y_NAMES:
        return "y"
    if lowered in _X_NAMES:
        return "x"
    return None


def _time_dim(dataset, dims) -> str | None:
    """Return the dimension carrying time, by its units then by its name."""
    for dim in dims:
        variable = dataset.variables.get(dim)
        if variable is not None and "since" in getattr(variable, "units", ""):
            return dim
    return next((dim for dim in dims if dim.lower() in ("time", "t")), None)


def grid_variables(path) -> list[GridVariable]:
    """Return the time-varying 2-D variables a file offers for display."""
    found: list[GridVariable] = []
    try:
        dataset = _dataset(path)
    except (ImportError, OSError):
        return found
    with dataset:
        for variable in dataset.variables.values():
            if variable.ndim != 3:
                continue
            dims = variable.dimensions
            time_dim = _time_dim(dataset, dims)
            if time_dim is None:
                continue
            spatial = [dim for dim in dims if dim != time_dim]
            roles = {_axis_role(dataset, dim): dim for dim in spatial}
            if "y" not in roles or "x" not in roles:
                continue
            found.append(
                GridVariable(
                    name=variable.name,
                    label=getattr(variable, "long_name", variable.name),
                    units=getattr(variable, "units", ""),
                    time_dim=time_dim,
                    y_dim=roles["y"],
                    x_dim=roles["x"],
                )
            )
    return found


def time_axis(path, time_dim: str = "time") -> list[datetime]:
    """Return the decoded time steps, honouring the file's own units.

    mHM writes days in one setup and hours in another, at daily or monthly
    spacing, so the axis is decoded rather than derived from a fixed epoch.
    """
    try:
        from netCDF4 import Dataset, num2date
    except ImportError:
        return []
    try:
        dataset = Dataset(str(path))
    except OSError:
        return []
    with dataset:
        variable = dataset.variables.get(time_dim)
        if variable is None or variable.size == 0:
            return []
        values = num2date(
            variable[:],
            getattr(variable, "units", "days since 1900-01-01"),
            getattr(variable, "calendar", "standard"),
            only_use_cftime_datetimes=False,
            only_use_python_datetimes=True,
        )
    return [datetime(v.year, v.month, v.day, v.hour, v.minute, v.second) for v in values]


def read_slice(path, variable: GridVariable, step: int, crs=None):
    """Return one timestep as a georeferenced DataArray.

    ``crs`` is required for files that carry none of their own, which is the
    case for every mHM output; without it the raster cannot be placed.
    """
    import rioxarray  # noqa: F401 - registers the .rio accessor
    import xarray as xr

    with xr.open_dataset(path, decode_coords="all") as dataset:
        data = dataset[variable.name].isel({variable.time_dim: int(step)}).load()
    renames = {}
    if variable.y_dim != "y":
        renames[variable.y_dim] = "y"
    if variable.x_dim != "x":
        renames[variable.x_dim] = "x"
    if renames:
        data = data.rename(renames)
    data = data.rio.set_spatial_dims(x_dim="x", y_dim="y")
    if data.rio.crs is None and crs is not None:
        data = data.rio.write_crs(crs)
    return data


def output_files(folder) -> list[Path]:
    """Return the NetCDF files in one folder, newest first."""
    folder = Path(folder)
    if not folder.is_dir():
        return []
    return sorted(folder.glob("*.nc"), key=lambda p: -p.stat().st_mtime)


__all__ = [
    "GridVariable",
    "grid_variables",
    "output_files",
    "read_slice",
    "time_axis",
]
