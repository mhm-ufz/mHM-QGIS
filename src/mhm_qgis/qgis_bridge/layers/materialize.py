"""Materialize QGIS inputs for path-based processing backends."""
from __future__ import annotations

import os
import re
from pathlib import Path
from urllib.parse import unquote, urlparse

from .. import processing
from .compat import create_vector_file_writer


def local_layer_source(layer):
    """Return a plain local source path, excluding provider and sublayer URIs."""
    source = str(layer.source() or "").strip()
    if not source or "|" in source or "?" in source:
        return None

    parsed = urlparse(source)
    windows_drive = re.match(r"^[A-Za-z]:[\\/]", source)
    if parsed.scheme == "file":
        source = unquote(parsed.path)
        if re.match(r"^/[A-Za-z]:/", source):
            source = source[1:]
    elif parsed.scheme and not windows_drive:
        return None

    path = Path(source)
    return str(path) if path.is_file() else None


def remove_vector_dataset(path):
    """Remove a temporary vector dataset and its common sidecars."""
    if not path:
        return
    root, extension = os.path.splitext(path)
    if extension.lower() == ".shp":
        candidates = [
            root + suffix
            for suffix in (".shp", ".shx", ".dbf", ".prj", ".cpg", ".qpj", ".fix")
        ]
    else:
        candidates = [path, f"{path}-wal", f"{path}-shm"]
    for candidate in candidates:
        if os.path.exists(candidate):
            os.remove(candidate)


def materialize_vector_layer(layer, output_path):
    """Copy any valid QGIS vector/table layer to a standalone GeoPackage."""
    remove_vector_dataset(output_path)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    writer = create_vector_file_writer(
        output_path,
        layer.fields(),
        layer.wkbType(),
        layer.crs(),
        driver_name="GPKG",
    )
    if writer.hasError():
        message = writer.errorMessage()
        del writer
        raise RuntimeError(f"Could not materialize vector layer: {message}")

    try:
        for feature in layer.getFeatures():
            if not writer.addFeature(feature):
                raise RuntimeError(
                    f"Could not write feature from vector layer '{layer.name()}'."
                )
    finally:
        del writer

    if not os.path.exists(output_path):
        raise RuntimeError(f"Could not materialize vector layer '{layer.name()}'.")
    return output_path


def materialize_raster_layer(layer, output_path, *, log=None, report=None):
    """Write a provider-backed raster layer to a local GeoTIFF."""
    result = processing.run(
        "gdal:translate",
        {
            "INPUT": layer,
            "TARGET_CRS": None,
            "NODATA": None,
            "COPY_SUBDATASETS": False,
            "OPTIONS": None,
            "EXTRA": "",
            "DATA_TYPE": 0,
            "OUTPUT": str(output_path),
        },
        log=log,
        report=report,
    )
    if not result or not Path(output_path).is_file():
        raise RuntimeError(f"Could not materialize raster layer '{layer.name()}'.")
    return str(output_path)


__all__ = [
    "local_layer_source",
    "materialize_raster_layer",
    "materialize_vector_layer",
    "remove_vector_dataset",
]
