"""A reference raster for a model grid: its path, NetCDF variable and CRS, and the grid they give."""
import math
from pathlib import Path

from qgis.core import QgsCoordinateReferenceSystem
from qgis.PyQt.QtCore import QSignalBlocker
from qgis.PyQt.QtWidgets import QDialog, QMessageBox

from ...core.grid import format_resolution
from ...core.grid.raster import raster_grid
from ...core.handlers.file.netcdf.raster import source
from ...qgis_bridge import layers
from ..bindings.browse_resolution_L2_L1_L11_file import bind
from ..controllers import netcdf_asc_additional_input
from ..ui import load_form
from .file_picker import open_file


class _ReferenceFile:
    """Shared by the L0 and the L1/L11/L2 forms; `info` is the grid in the domain CRS."""

    def _start(self, level, domain_crs, initial, bind_form):
        self.setupUi(self)
        self.setWindowTitle(f"{level} reference file")
        self.level, self.domain_crs = level, domain_crs
        self.path, self.info, self.error = "", None, ""
        self.comboBox_rasterInput.setEditable(True)
        bind_form(self)
        if initial.get("path"):
            self.comboBox_rasterInput.setCurrentText(initial["path"])
            self.comboBox_varName.setCurrentText(initial.get("variable") or "")
            if self.mProjectionSelectionWidget_crsFileInput.isEnabled() and initial.get("crs"):
                self.mProjectionSelectionWidget_crsFileInput.setCrs(QgsCoordinateReferenceSystem(initial["crs"]))
        self.show_grid()

    def browse(self):
        path = open_file(self, f"{self.level} reference raster", "Rasters (*.tif *.tiff *.asc *.nc);;All files (*)")
        if path:
            self.comboBox_rasterInput.setCurrentText(path)

    def read(self, text=""):
        """A typed or browsed path: its variables and CRS (the file's, locked, when it has one)."""
        path = Path(str(text).strip()).expanduser()
        with QSignalBlocker(self.comboBox_varName):
            if path.is_file():
                netcdf_asc_additional_input.initialize(self, path)
                self._ungridded()
            else:
                self.path = ""
                self.comboBox_varName.clear()
        self.show_grid()

    def variable_changed(self, text):
        if self.path:
            netcdf_asc_additional_input.refresh_crs(self, text)
            self._ungridded()
        self.show_grid()

    def _ungridded(self):
        """A NetCDF grid GDAL cannot place (mHM's `xc`/`yc` with 2-D lat/lon): its x/y are read in
        the CRS chosen here, the domain CRS to start with, not the CRS of its lat/lon."""
        from osgeo import gdal

        dataset = gdal.Open(source(self.path, self.comboBox_varName.currentText().strip()))
        if dataset is not None and dataset.GetGeoTransform(can_return_null=True) is None:
            widget = self.mProjectionSelectionWidget_crsFileInput
            with QSignalBlocker(widget):
                widget.setCrs(self.domain_crs)
            widget.setEnabled(True)

    def file_crs(self):
        crs = self.mProjectionSelectionWidget_crsFileInput.crs()
        return layers.crs_string(crs) if crs.isValid() else ""

    def show_grid(self, *_args):
        """The file's cell size in the domain CRS; `error` says why it cannot be used."""
        self.info, self.error = None, "Select a raster file."
        if self.path:
            try:
                self.info = raster_grid(source(self.path, self.comboBox_varName.currentText().strip()),
                                        self.file_crs(), layers.crs_string(self.domain_crs),
                                        layers.crs_unit(self.domain_crs))
                x, y = self.info["x_resolution"], self.info["y_resolution"]
                self.error = "" if math.isclose(x, y, rel_tol=1e-6) else (
                    f"The cells are not square (x {x:.10g}, y {y:.10g}): mHM needs square cells.")
            except (OSError, RuntimeError, ValueError) as error:
                self.info, self.error = None, str(error)
        self.label_rasterResolutionValue.setText(
            format_resolution(self.info["resolution"], self.info["unit"]) if self.info else "")
        self.label_rasterResolutionUnit.setText(self.info["unit"] if self.info else "")
        self.label_rasterResolutionValue.setToolTip(self.error)

    def selection(self):
        return {"path": self.path, "variable": self.comboBox_varName.currentText().strip(), "crs": self.file_crs()}

    def accept(self):
        if self.error or not self.info:
            QMessageBox.warning(self, f"{self.level} reference file", self.error or "Select a raster file.")
            return
        super().accept()


class LevelFileDialog(_ReferenceFile, QDialog, load_form("browse_resolution_L2_L1_L11_file.ui")):
    def __init__(self, level, domain_crs, initial=None, parent=None):
        super().__init__(parent)
        self._start(level, domain_crs, initial or {}, bind)


__all__ = ["LevelFileDialog"]
