# -*- coding: utf-8 -*-
"""Convert live QGIS layers and extents into values accepted by ``core.grid``."""
from __future__ import annotations

from pathlib import Path

from ...core.grid import raster_resolution_info as resolution_info
from ...core.handlers.store.layout import MERGED_MASK_NAME
from ...core.handlers.store.paths import temp_morphology_folder


def crs_unit(crs) -> str:
    if crs is None or not crs.isValid():
        return ""
    if crs.isGeographic():
        return "deg"
    try:
        from qgis.core import QgsUnitTypes

        return QgsUnitTypes.toAbbreviatedString(crs.mapUnits()) or "m"
    except Exception:
        return "m"


def crs_string(crs) -> str:
    if crs is None or not crs.isValid():
        return ""
    if crs.authid():
        return crs.authid()
    to_wkt = getattr(crs, "toWkt", None)
    return to_wkt() if callable(to_wkt) else ""


def extent_bounds(extent) -> tuple[float, float, float, float]:
    return (
        float(extent.xMinimum()),
        float(extent.xMaximum()),
        float(extent.yMinimum()),
        float(extent.yMaximum()),
    )


def raster_resolution_info(layer) -> dict | None:
    """Read exact square-cell metadata from a QGIS raster layer."""
    if layer is None or not layer.isValid():
        return None
    extent = layer.extent()
    width, height = int(layer.width()), int(layer.height())
    if width <= 0 or height <= 0:
        return None
    unit = crs_unit(layer.crs())
    return resolution_info(
        extent_bounds(extent),
        width,
        height,
        unit=unit,
        crs=crs_string(layer.crs()),
    )


def merged_domain_bounds(project_folder, target_crs):
    """Return merged active-domain bounds in ``target_crs`` and their source path."""
    from qgis.core import QgsCoordinateReferenceSystem, QgsRectangle, QgsVectorLayer

    from ...core.handlers.raster.tasks import domain_mask_bounds

    folder = Path(temp_morphology_folder(project_folder))
    candidates = (
        folder / "Watersheds" / MERGED_MASK_NAME,
        folder / "Watersheds" / "4_watershed_merged_vector.shp",
        folder / "4_watershed_merged_vector.shp",
    )
    for path in candidates:
        if not path.exists():
            continue
        if path.suffix.lower() == ".tif":
            mask = domain_mask_bounds(path)
            if mask:
                source_crs = QgsCoordinateReferenceSystem(mask["projection"] or "")
                rectangle = QgsRectangle(*mask["bounds"])
                return extent_bounds(_transform_extent(rectangle, source_crs, target_crs)), str(path)
            continue
        layer = QgsVectorLayer(str(path), "Watershed_Merged", "ogr")
        if layer.isValid():
            return extent_bounds(_transform_extent(layer.extent(), layer.crs(), target_crs)), str(path)
    return None, ""


def _transform_extent(extent, source_crs, target_crs):
    from qgis.core import QgsCoordinateTransform, QgsProject

    if source_crs is None or target_crs is None:
        return extent
    if not source_crs.isValid() or not target_crs.isValid() or source_crs == target_crs:
        return extent
    transform = QgsCoordinateTransform(source_crs, target_crs, QgsProject.instance())
    transform.setBallparkTransformsAreAppropriate(True)
    return transform.transformBoundingBox(extent)


__all__ = [
    "crs_string",
    "crs_unit",
    "extent_bounds",
    "merged_domain_bounds",
    "raster_resolution_info",
]
