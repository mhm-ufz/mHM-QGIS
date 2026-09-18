"""Populate the NetCDF/ASCII variable and CRS prompt."""
from pathlib import Path

from qgis.core import QgsCoordinateReferenceSystem

from ...core.handlers.file.netcdf.raster import raster_variables, source
from ...qgis_bridge import layers


def initialize(dialog, path):
    dialog.path = str(Path(path).resolve())
    suffix = Path(path).suffix.lower()
    dialog.comboBox_varName.clear()
    if suffix == ".asc":
        dialog.comboBox_varName.addItem(Path(path).stem)
        dialog.comboBox_varName.setEnabled(False)
    else:
        dialog.comboBox_varName.addItems(raster_variables(path))
        dialog.comboBox_varName.setEnabled(True)
    refresh_crs(dialog)


def refresh_crs(dialog, _text=""):
    path = Path(dialog.path)
    crs = _asc_crs(path) if path.suffix.lower() == ".asc" else layers.crs_of(
        source(path, dialog.comboBox_varName.currentText().strip())
    )
    locked = crs is not None and crs.isValid()
    if not locked:
        crs = QgsCoordinateReferenceSystem("EPSG:4326")
    dialog.mProjectionSelectionWidget_crsFileInput.setCrs(crs)
    dialog.mProjectionSelectionWidget_crsFileInput.setEnabled(not locked)


def _asc_crs(path):
    candidates = (
        path.with_suffix(".proj"), Path(f"{path}.proj"),
        path.with_suffix(".prj"), Path(f"{path}.prj"),
    )
    for candidate in candidates:
        if not candidate.is_file():
            continue
        try:
            text = candidate.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeError):
            continue
        crs = QgsCoordinateReferenceSystem()
        if (crs.createFromWkt(text) or crs.createFromString(text)) and crs.isValid():
            return crs
    return layers.crs_of(path)


__all__ = ["initialize", "refresh_crs"]
