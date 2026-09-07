# -*- coding: utf-8 -*-
"""Turn a path into a QGIS layer.

`open_layer` builds one and hands back None when it is unusable; `load` also
registers it with the project. Callers pass `log` rather than a dialog, so
nothing below the Qt layer needs to know a dialog exists.
"""
from __future__ import annotations

import os
from pathlib import Path


def source_uri(path, variable: str | None = None) -> str:
    """Return the layer source, addressing a NetCDF variable when named."""
    path = Path(path)
    if variable and path.suffix.lower() == ".nc":
        return f'NETCDF:"{path}":{variable}'
    return str(path)


def raster_source(layer) -> str | None:
    """Return a raster layer's provider source, or None for another layer type."""
    if layer is None:
        return None
    try:
        from qgis.core import QgsRasterLayer
    except ImportError:
        return None
    if not isinstance(layer, QgsRasterLayer):
        return None
    try:
        return str(layer.source() or "") or None
    except Exception:
        return None


def _readable_path(source) -> str:
    """Return the filesystem path behind a source, unwrapping a NetCDF URI."""
    source = str(source)
    if source.startswith('NETCDF:"') and '":' in source:
        return source.split('NETCDF:"', 1)[1].split('":', 1)[0]
    return source


def open_layer(path, name: str, *, is_raster: bool = True):
    """Build a layer from a path, returning None when QGIS cannot read it."""
    try:
        from qgis.core import QgsRasterLayer, QgsVectorLayer
    except ImportError:
        return None
    if not path:
        return None

    layer = (QgsRasterLayer(str(path), name) if is_raster
             else QgsVectorLayer(str(path), name, "ogr"))
    try:
        return layer if layer.isValid() else None
    except Exception:
        return None


def load(path, name: str, *, is_raster: bool = True, log=None):
    """Open a layer and add it to the project, reporting failures through `log`."""
    if not path or not os.path.exists(_readable_path(path)):
        if log is not None:
            log(f"ERROR: Output file not found at {path}")
        return None

    layer = open_layer(path, name, is_raster=is_raster)
    if layer is None:
        if log is not None:
            log(f"ERROR: Failed to load layer: {name}")
        return None

    from . import project

    project.add(layer)
    if log is not None:
        log(f"Layer '{name}' added to project.")
    return layer


def refresh_source(layer, source, name, *, is_raster=True, log=None):
    """Refresh provider metadata, replacing the layer only if its type changes."""
    from qgis.core import QgsRasterLayer
    from . import project

    if isinstance(layer, QgsRasterLayer) != is_raster:
        replacement = open_layer(source, name, is_raster=is_raster)
        if replacement is not None:
            project._project().removeMapLayer(layer.id())
            project.add(replacement)
        layer = replacement
    else:
        layer.setDataSource(source, name, layer.providerType())
    if layer is None or not layer.isValid():
        if log:
            log(f"ERROR: Failed to refresh layer: {name}")
        return None
    layer.triggerRepaint()
    return layer


__all__ = ["load", "open_layer", "raster_source", "refresh_source", "source_uri"]
