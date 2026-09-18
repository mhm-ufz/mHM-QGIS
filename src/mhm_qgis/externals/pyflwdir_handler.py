# -*- coding: utf-8 -*-
"""Everything the plugin asks of pyflwdir.

One flow grid is built from the filled DEM and every derived product -- flow
direction, upstream area, stream order, the stream network, basins -- is read
off that one grid. Building it is the expensive part, so callers pass the
context around rather than rebuilding it per product.

`pyflwdir` and its companions (numpy, affine, osgeo) are imported inside the
functions: they are optional in a user's QGIS Python environment, and importing
them at module scope would land on every plugin load.
"""
from __future__ import annotations

import math

# pyflwdir needs a finite nodata, so every DEM is restated onto this value
# before the flow grid is built. -9999 is also what mHM writes.
NODATA = -9999.0
# pyflwdir's own "no direction" code, and what mHM expects for an unset cell.
FLOW_DIRECTION_NODATA = 247

PACKAGES = ("numpy", "pyflwdir", "affine", "osgeo")


class MissingDependencies(RuntimeError):
    """Raised when the optional morphology stack is not installed."""

    def __init__(self, missing):
        self.missing = list(missing)
        super().__init__(
            "Python morphology processing requires these packages in the QGIS "
            f"Python environment: {', '.join(self.missing)}."
        )


def missing_packages() -> list[str]:
    """Return the names of the morphology packages that cannot be imported."""
    missing = []
    for name, label in (
        ("numpy", "numpy"),
        ("pyflwdir", "pyflwdir"),
        ("affine", "affine"),
        ("osgeo", "GDAL Python bindings (osgeo)"),
    ):
        try:
            __import__(name)
        except ImportError:
            missing.append(label)
    return missing


def dependencies() -> dict:
    """Return the morphology stack, raising `MissingDependencies` if incomplete."""
    missing = missing_packages()
    if missing:
        raise MissingDependencies(missing)

    import numpy as np
    import pyflwdir as pfd
    from affine import Affine
    from osgeo import gdal, ogr, osr

    return {
        "np": np, "pfd": pfd, "Affine": Affine,
        "gdal": gdal, "ogr": ogr, "osr": osr,
    }


def is_geographic(projection) -> bool:
    """Report whether a WKT projection is a geographic (lat/lon) CRS."""
    if not projection:
        return False
    from osgeo import osr

    spatial_ref = osr.SpatialReference()
    return bool(
        spatial_ref.ImportFromWkt(projection) == 0 and spatial_ref.IsGeographic()
    )


def cell_area_m2(reference) -> float:
    """Approximate a raster cell's area in square metres.

    Channel thresholds are expressed in cells, so a lat/lon grid has to be
    converted before it can be compared against an area in m2.
    """
    geotransform = reference["geotransform"]
    width = abs(float(geotransform[1]))
    height = abs(float(geotransform[5]))
    if is_geographic(reference.get("projection")):
        center_lat = geotransform[3] + geotransform[5] * reference["rows"] / 2.0
        lat_m = height * 110574.0
        lon_m = width * 111320.0 * math.cos(math.radians(center_lat))
        return max(abs(lat_m * lon_m), 1.0)
    return max(width * height, 1.0)


def normalise_dem(array, nodata):
    """Restate a DEM onto `NODATA`, returning the array and its invalid mask.

    Non-finite cells and cells matching the declared nodata both become
    `NODATA`, because pyflwdir treats NaN as data.
    """
    import numpy as np

    dem = array.astype(np.float32, copy=True)
    invalid = ~np.isfinite(dem)
    if nodata is not None and np.isfinite(nodata):
        invalid |= np.isclose(dem, nodata)
    dem[invalid] = NODATA
    return dem, invalid


def flwdir_context(reference, *, is_direction=False) -> dict:
    """Build the flow grid from an already-read D8 or filled-DEM raster.

    `reference` is a raster read as `{array, nodata, geotransform, projection,
    rows, cols}`. Taking it rather than a path keeps this module free of raster
    IO, which lives in `core/handlers/raster`.
    """
    deps = dependencies()
    transform = deps["Affine"].from_gdal(*reference["geotransform"])
    options = {"transform": transform, "latlon": is_geographic(reference.get("projection"))}
    if is_direction:
        invalid = reference["array"] == reference["nodata"]
        direction = reference["array"].astype("uint8")
        direction[invalid] = FLOW_DIRECTION_NODATA
        flwdir = deps["pfd"].from_array(direction, ftype="d8", **options)
    else:
        dem, invalid = normalise_dem(reference["array"], reference["nodata"])
        flwdir = deps["pfd"].from_dem(dem, nodata=NODATA, **options)
    return {
        "flwdir": flwdir,
        "reference": reference,
        "invalid": invalid,
        "nodata": NODATA,
        "transform": transform,
        "deps": deps,
        "np": deps["np"],
    }


def upstream_area_m2(context):
    """Return upstream area in m2 as float32, with invalid cells set to nodata."""
    np = context["np"]
    values = context["flwdir"].upstream_area(unit="m2").astype(np.float32)
    values[context["invalid"]] = NODATA
    return values


def stream_mask(cells, threshold_cells):
    """Return the boolean mask of cells draining at least `threshold_cells`."""
    return cells >= threshold_cells


def stream_order(context, mask, method: str = "strahler"):
    """Return the stream order of every cell inside `mask`."""
    return context["flwdir"].stream_order(method, mask=mask)


def stream_features(context, mask, **attributes):
    """Return the stream network as GeoJSON-like features."""
    return context["flwdir"].streams(mask=mask, **attributes)


def basins(context, **kwargs):
    """Return the basin map for the given outlet specification."""
    return context["flwdir"].basins(**kwargs)


def cell_for_point(context, x: float, y: float):
    """Return the (row, col) of a map coordinate, or None when outside the grid."""
    reference = context["reference"]
    geotransform = reference["geotransform"]
    col = int((x - geotransform[0]) / geotransform[1])
    row = int((y - geotransform[3]) / geotransform[5])
    if 0 <= row < reference["rows"] and 0 <= col < reference["cols"]:
        return row, col
    return None


__all__ = [
    "FLOW_DIRECTION_NODATA",
    "MissingDependencies",
    "NODATA",
    "PACKAGES",
    "basins",
    "cell_area_m2",
    "cell_for_point",
    "dependencies",
    "flwdir_context",
    "is_geographic",
    "missing_packages",
    "normalise_dem",
    "stream_features",
    "stream_mask",
    "stream_order",
    "upstream_area_m2",
]
