"""Display an exact model grid as a lightweight checkerboard raster."""
from __future__ import annotations

import hashlib
import os

from qgis.PyQt.QtWidgets import QMessageBox

from ...core.grid import geotransform_from_header, standardize_header
from .canvas import _materialised_path, show_raster


def _projection_wkt(crs: str) -> str:
    from qgis.core import QgsCoordinateReferenceSystem

    value = QgsCoordinateReferenceSystem(crs)
    if not value.isValid():
        raise ValueError("Select a valid project CRS before displaying the grid.")
    return value.toWkt()


def _grid_raster(level: str, header, crs: str):
    import numpy as np
    from osgeo import gdal

    header = standardize_header(header)
    signature = hashlib.sha1(
        repr(
            (
                header["nrows"],
                header["ncols"],
                header["xllcorner"],
                header["yllcorner"],
                header["cellsize"],
                crs,
            )
        ).encode("utf-8")
    ).hexdigest()
    path = _materialised_path(f"mHM {level} grid")
    existing = gdal.Open(str(path), gdal.GA_ReadOnly) if path.exists() else None
    if existing is not None and existing.GetMetadataItem("MHM_GRID") == signature:
        existing = None
        return path
    existing = None

    temporary = path.with_name(f".{path.name}.tmp.tif")
    temporary.unlink(missing_ok=True)
    dataset = gdal.GetDriverByName("GTiff").Create(
        str(temporary),
        header["ncols"],
        header["nrows"],
        1,
        gdal.GDT_Byte,
        options=["TILED=YES", "COMPRESS=LZW"],
    )
    if dataset is None:
        raise RuntimeError(f"Could not create grid display raster: {temporary}")
    try:
        dataset.SetGeoTransform(geotransform_from_header(header))
        dataset.SetProjection(_projection_wkt(crs))
        dataset.SetMetadataItem("MHM_GRID", signature)
        band = dataset.GetRasterBand(1)
        columns = np.arange(header["ncols"], dtype="uint32") % 2
        block_rows = max(1, min(512, 8_000_000 // header["ncols"]))
        for first in range(0, header["nrows"], block_rows):
            count = min(block_rows, header["nrows"] - first)
            rows = np.arange(first, first + count, dtype="uint32")[:, None]
            values = (32 + 192 * ((rows + columns) % 2)).astype("uint8")
            band.WriteArray(values, 0, first)
        band.FlushCache()
        band = None
        dataset = None
        os.replace(temporary, path)
    except Exception:
        dataset = None
        temporary.unlink(missing_ok=True)
        raise
    return path


def display_selected_grid(dialog) -> None:
    """Create and show the grid selected by the main form."""
    selected = dialog.comboBox_gridVariableToDisplay.currentText().strip()
    if not selected:
        QMessageBox.warning(dialog, "Display Grid", "Select L0, L1, L11/L3, or L2.")
        return
    level = "L11" if selected.startswith("L11") else selected
    try:
        header = dialog.grid_level_headers()[level]
        path = _grid_raster(level, header, dialog.selected_meteo_crs())
        layer = show_raster(dialog, path, f"mHM {selected} grid")
        if layer is not None:
            layer.setOpacity(0.55)
            layer.triggerRepaint()
    except Exception as error:
        QMessageBox.warning(dialog, "Display Grid", str(error))


__all__ = ["display_selected_grid"]
