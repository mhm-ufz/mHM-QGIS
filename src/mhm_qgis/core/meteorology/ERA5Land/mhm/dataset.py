"""Dataset opening, standardization, and variable discovery."""
from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path


def open_dataset(path: Path, xr):
    """Open a NetCDF file using the first available xarray backend."""
    last_error = None
    for engine in ("netcdf4", "h5netcdf", "scipy", None):
        try:
            kwargs = {} if engine is None else {"engine": engine}
            return xr.open_dataset(
                path,
                decode_times=True,
                mask_and_scale=True,
                **kwargs,
            )
        except Exception as e:
            last_error = e
    raise RuntimeError(f"Could not open NetCDF file {path}: {last_error}")


def standardize_dataset(ds):
    """Normalize common ERA5 coordinate names and remove scalar metadata vars."""
    rename = {}
    for old, new in (
        ("valid_time", "time"),
        ("latitude", "lat"),
        ("longitude", "lon"),
    ):
        old_exists = old in ds.variables or old in ds.dims
        new_exists = new in ds.variables or new in ds.dims
        if old_exists and not new_exists:
            rename[old] = new
    if rename:
        ds = ds.rename(rename)

    missing = [coord for coord in ("time", "lat", "lon") if coord not in ds.coords]
    if missing:
        raise KeyError(f"Missing coordinate(s): {', '.join(missing)}")

    drop_names = [
        name
        for name in ("number", "expver", "spatial_ref")
        if name in ds.variables and name not in ds.dims
    ]
    if drop_names:
        ds = ds.drop_vars(drop_names, errors="ignore")

    return ds


def find_source_variable(ds, aliases: Iterable[str]) -> str | None:
    """Find the first configured variable alias present in a dataset."""
    for name in aliases:
        if name in ds.data_vars:
            return name
    return None
