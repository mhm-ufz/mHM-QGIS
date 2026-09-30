"""mHM-ready morphology input dialog."""

from pathlib import Path

from qgis.PyQt import QtWidgets

from ..bindings.mhm_ready_morph_input import bind_mhm_ready
from ..ui import load_form
from .input_selection import (
    ReadyInputConfig,
    _combo_path,
    _combo_raster_details,
    _ensure_raster_details,
    _populate_file_combo,
    _restore_raster_details,
)


class MhmReadyInputDialog(
    QtWidgets.QDialog, load_form("mhm_ready_morph_input.ui")
):
    def __init__(self, project_folder, kind, parent=None, initial=None):
        super().__init__(parent)
        self.setupUi(self)
        self.kind = str(kind)
        title = {
            "lc": "Land Use",
            "soil": "Soil",
            "geology": "Geology",
            "lai": "LAI",
        }[self.kind]
        self.groupBox_morphFileInput.setTitle(title)
        initial = initial or {}
        _populate_file_combo(
            self.comboBox_inputFile,
            project_folder,
            "land_cover" if self.kind == "lc" else self.kind,
            initial.get("input_path", "") if isinstance(initial, dict) else "",
        )
        _restore_raster_details(self.comboBox_inputFile, initial)
        self.groupBox_classDefinitionInput.setChecked(
            bool(initial.get("classdefinition_path"))
        )
        _populate_file_combo(
            self.comboBox_classDefinitionInputFile,
            project_folder,
            "lookup",
            initial.get("classdefinition_path", "")
            if isinstance(initial, dict)
            else "",
        )
        bind_mhm_ready(self, title)

    def selected_config(self) -> ReadyInputConfig | None:
        source = _combo_path(self.comboBox_inputFile)
        definition = (
            _combo_path(self.comboBox_classDefinitionInputFile)
            if self.groupBox_classDefinitionInput.isChecked()
            else ""
        )
        if not source or not Path(source).is_file():
            return None
        if definition and not Path(definition).is_file():
            return None
        details = _combo_raster_details(self.comboBox_inputFile)
        return ReadyInputConfig(
            source,
            definition,
            details.get("variable", ""),
            details.get("source_crs", ""),
        )

    def accept(self):
        if not _ensure_raster_details(self, self.comboBox_inputFile):
            return
        if self.selected_config() is None:
            QtWidgets.QMessageBox.warning(
                self, "Missing Input", "Select all required input files."
            )
            return
        super().accept()


__all__ = ["MhmReadyInputDialog"]

