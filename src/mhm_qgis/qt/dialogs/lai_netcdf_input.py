"""LAI NetCDF input dialog."""

from pathlib import Path

from qgis.PyQt import QtWidgets

from ..bindings.lai_netcdf_input import bind_lai_netcdf
from ..ui import load_form
from .input_selection import (
    LaiNetcdfConfig,
    _combo_path,
    _combo_raster_details,
    _ensure_raster_details,
    _populate_file_combo,
    _restore_raster_details,
)


class LaiNetcdfInputDialog(
    QtWidgets.QDialog, load_form("lai_netcdf_input_dialog.ui")
):
    def __init__(self, project_folder, parent=None, initial=None):
        super().__init__(parent)
        self.setupUi(self)
        initial = initial or {}
        _populate_file_combo(
            self.comboBox_laiNetCDFInput,
            project_folder,
            "lai",
            initial.get("input_path", "") if isinstance(initial, dict) else "",
        )
        _restore_raster_details(self.comboBox_laiNetCDFInput, initial)
        index = self.comboBox_timestepLAIInput.findText(
            str(initial.get("target_timestep", ""))
        )
        self.comboBox_timestepLAIInput.setCurrentIndex(index)
        bind_lai_netcdf(self)

    def selected_config(self) -> LaiNetcdfConfig | None:
        source = _combo_path(self.comboBox_laiNetCDFInput)
        target = self.comboBox_timestepLAIInput.currentText().strip()
        if (
            not source
            or Path(source).suffix.lower() != ".nc"
            or not Path(source).is_file()
            or not target
        ):
            return None
        details = _combo_raster_details(self.comboBox_laiNetCDFInput)
        return LaiNetcdfConfig(
            source,
            target,
            details.get("variable", ""),
            details.get("source_crs", ""),
        )

    def accept(self):
        if not _ensure_raster_details(self, self.comboBox_laiNetCDFInput):
            return
        if self.selected_config() is None:
            QtWidgets.QMessageBox.warning(
                self,
                "Missing Input",
                "Select a NetCDF file and output temporal resolution.",
            )
            return
        super().accept()


__all__ = ["LaiNetcdfInputDialog"]

