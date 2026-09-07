# -*- coding: utf-8 -*-
"""Put a prepared raster into the QGIS map canvas."""
from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path

from .. import layers


def select_band(layer, band: int, log=None) -> bool:
    """Point a raster layer at one band, keeping the current contrast."""
    if layer is None or not band:
        return False
    try:
        from qgis.core import QgsSingleBandGrayRenderer

        renderer = layer.renderer()
        # Reusing the renderer keeps one stretch across the whole series; a
        # fresh one would re-scale per band and flatten the differences.
        if isinstance(renderer, QgsSingleBandGrayRenderer):
            renderer.setGrayBand(band)
        else:
            layer.setRenderer(QgsSingleBandGrayRenderer(layer.dataProvider(), band))
        layer.triggerRepaint()
        return True
    except Exception as error:
        if log is not None:
            log(f"WARNING: Could not select band {band}: {error}")
        return False


def show_raster(dialog, path, name, *, variable=None, band=None, is_raster=True):
    """Reuse by name, refreshing when the source or its file stamp changes.

    A time scrubber calls this on every step, and `layers.load` does not
    uniquify names, so reloading would both stack duplicates in the layer tree
    and re-open the file each tick.
    """
    source = layers.source_uri(path, variable)
    try:
        stat = Path(path).stat()
        stamp = f"{stat.st_mtime_ns}:{stat.st_size}"
    except OSError:
        stamp = ""
    signature = f"{source}:{stamp}"
    layer = layers.find_by_name(name)
    if layer is None:
        layer = layers.load(
            source, name,
            is_raster=is_raster, log=getattr(dialog, "log_message", None))
    elif (layer.source() != source
          or layer.customProperty("mhm_qgis/display_source", "") != signature):
        layer = layers.refresh_source(
            layer, source, name, is_raster=is_raster,
            log=getattr(dialog, "log_message", None),
        )
    if layer is not None:
        layer.setCustomProperty("mhm_qgis/display_source", signature)
    if layer is not None and band:
        select_band(layer, band, log=getattr(dialog, "log_message", None))
    return layer


def _materialised_path(name: str) -> Path:
    """Return a stable scratch path for one layer name.

    Reusing the same file per layer keeps the scratch directory bounded while a
    slider is scrubbed, instead of leaving one raster behind per step.
    """
    digest = hashlib.sha1(name.encode("utf-8")).hexdigest()[:12]
    folder = Path(tempfile.gettempdir()) / "mhm_qgis_display"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{digest}.tif"


def show_dataarray(dialog, data, name: str):
    """Show a georeferenced DataArray, reusing the loaded layer by name.

    mHM output carries no CRS, so it cannot be addressed through a
    ``NETCDF:"path":var`` URI the way prepared forcing can -- GDAL would place it
    at the origin with unit pixels. The caller attaches a CRS and the array is
    written to a scratch raster instead.
    """
    log = getattr(dialog, "log_message", None)
    if data.rio.crs is None and log is not None:
        log(f"WARNING: {name} has no CRS; the raster cannot be placed correctly.")
    target = _materialised_path(name)
    try:
        data.rio.to_raster(target)
    except Exception as error:
        if log is not None:
            log(f"ERROR: Could not prepare {name} for display: {error}")
        return None

    layer = layers.find_by_name(name)
    if layer is None:
        return layers.load(str(target), name, log=log)
    # The file behind the layer was just rewritten in place.
    layer.dataProvider().reloadData()
    layer.triggerRepaint()
    return layer


__all__ = [
    "select_band",
    "show_dataarray",
    "show_raster",
]
