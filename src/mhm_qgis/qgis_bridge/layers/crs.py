"""CRS questions that are answered by opening a layer."""
from __future__ import annotations

from .loader import open_layer


def crs_of(path, *, is_raster: bool = True):
    """Return the CRS of the raster or vector at `path`, or None if unreadable."""
    layer = open_layer(path, "crs_probe", is_raster=is_raster)
    if layer is None:
        return None
    crs = layer.crs()
    return crs if crs.isValid() else None


def transform_between(source_crs, target_crs, log=None):
    """Return a ballpark transform between two CRSs, or None when none is needed.

    None when either CRS is unusable or the two are the same -- the caller then
    needs no transform at all, so None is the ordinary answer, not a failure.

    Equality is the strict CRS comparison rather than `authid()`: two CRSs can
    share an authid (or both have none, for a custom definition) while differing,
    and building a transform that turns out to be the identity is harmless,
    whereas skipping a needed one silently misplaces coordinates.
    """
    from qgis.core import QgsCoordinateTransform, QgsProject

    if source_crs is None or target_crs is None:
        return None
    if not source_crs.isValid() or not target_crs.isValid():
        return None
    if source_crs == target_crs:
        return None

    transform = QgsCoordinateTransform(source_crs, target_crs, QgsProject.instance())
    transform.setBallparkTransformsAreAppropriate(True)
    if log is not None:
        log(f"Transforming from {source_crs.authid()} to {target_crs.authid()}.")
    return transform


def transform_to_raster(source_layer, raster_path, log=None):
    """Return a transform from `source_layer` onto the raster's CRS.

    None when either CRS is unusable or the two already agree -- the caller
    then needs no transform at all, so None is the ordinary answer rather than
    a failure.
    """
    target_crs = crs_of(raster_path)
    if target_crs is None:
        return None
    return transform_between(source_layer.crs(), target_crs, log=log)


__all__ = ["crs_of", "transform_between", "transform_to_raster"]
