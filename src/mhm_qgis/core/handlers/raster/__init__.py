# -*- coding: utf-8 -*-
"""QGIS-free raster and watershed jobs, safe to run in a worker.

GDAL and NumPy only -- no QGIS, no Qt -- so these run inside a `QgsTask` or the
`native_worker` child process without dragging the QGIS runtime along. Flow
grids come from `externals/pyflwdir_handler`; this module writes the files.

Every L0 operation is an integer window copy with nodata padding, never a
resample: `crop_aligned_l0_raster` and `mask_aligned_l0_raster` raise on a
misaligned or wrong-CRS input rather than silently shifting the data.
"""
from .tasks import (
    crop_aligned_l0_raster,
    delineate_domains_file,
    delineate_outlet_file,
    dem_derivative_files,
    hydrology_files,
    mask_aligned_l0_raster,
    write_domain_dem_ascii,
)

__all__ = [
    "crop_aligned_l0_raster",
    "delineate_domains_file",
    "delineate_outlet_file",
    "dem_derivative_files",
    "hydrology_files",
    "mask_aligned_l0_raster",
    "write_domain_dem_ascii",
]
