"""Runtime dependency checks for ERA5-Land mHM forcing preparation."""
from __future__ import annotations


class MissingDependencyError(RuntimeError):
    """Raised when the ERA5-Land forcing converter dependencies are missing."""


def import_dependencies():
    """Import numeric and NetCDF dependencies with a clear QGIS-facing error."""
    missing = []
    try:
        import numpy as np
    except Exception:
        np = None
        missing.append("numpy")
    try:
        import pandas as pd
    except Exception:
        pd = None
        missing.append("pandas")
    try:
        import xarray as xr
    except Exception:
        xr = None
        missing.append("xarray")

    backend_available = False
    for backend in ("h5netcdf", "scipy", "netCDF4"):
        try:
            __import__(backend)
            backend_available = True
            break
        except ImportError:
            continue
    if not backend_available:
        missing.append("netCDF4, h5netcdf, or scipy")

    if missing:
        raise MissingDependencyError(
            "ERA5-Land mHM forcing preparation requires: "
            + ", ".join(missing)
            + "."
        )
    return np, pd, xr
