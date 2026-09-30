"""NetCDF/ASCII variable and CRS dialog."""

from pathlib import Path

from qgis.PyQt import QtWidgets

from ...qgis_bridge import layers
from ..bindings.netcdf_asc_additional_input import bind
from ..controllers import netcdf_asc_additional_input as controller
from ..ui import load_form
from .input_selection import RasterInputConfig


class RasterDetailsDialog(
    QtWidgets.QDialog, load_form("netcdf_asc_additional_input.ui")
):
    def __init__(self, path, parent=None):
        super().__init__(parent)
        self.setupUi(self)
        self.setWindowTitle(f"Raster input: {Path(path).name}")
        bind(self)
        controller.initialize(self, path)

    def selected_config(self):
        variable = self.comboBox_varName.currentText().strip()
        crs = self.mProjectionSelectionWidget_crsFileInput.crs()
        if not variable or crs is None or not crs.isValid():
            return None
        return RasterInputConfig(self.path, variable, layers.crs_string(crs))

    def accept(self):
        if self.selected_config() is None:
            QtWidgets.QMessageBox.warning(
                self, "Missing Raster Metadata", "Select a variable and a valid CRS."
            )
            return
        super().accept()


__all__ = ["RasterDetailsDialog"]

