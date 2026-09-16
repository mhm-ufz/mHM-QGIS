"""Populate and validate meteorology metadata without opening data files."""
from pathlib import Path
from qgis.PyQt.QtWidgets import QFileDialog, QMessageBox
from ...core.meteorology.forcing.types import MeteoFolderSpec
from ...core.meteorology.forcing.temporal import VARIABLES, normalize_unit, validate_spec


def populate(dialog, initial):
    """Restore a variable's plain settings in the reusable form."""
    dialog.setWindowTitle(f"{dialog.variable} input")
    dialog.groupBox.setTitle(dialog.variable)
    dialog.comboBox_inputFolder.setEditable(True)
    folder = str(initial.get("folder", ""))
    dialog.comboBox_inputFolder.addItem(folder)
    dialog.lineEdit_fileNameGlobDefinition.setText(initial.get("pattern", "*.nc"))
    dialog.comboBox_temporalResolutionInput.setCurrentText(initial.get("temporal_resolution", "Daily"))
    dialog.comboBox_unitInput.addItems(VARIABLES[dialog.variable][2])
    dialog.comboBox_unitInput.setCurrentText(normalize_unit(initial.get("unit", VARIABLES[dialog.variable][2][0])))
    dialog.checkBox_cumulative.setChecked(bool(initial.get("cumulative")))
    dialog.comboBox_cumulativeTemporalResolution.setCurrentText(initial.get("cumulative_resolution") or "Daily")
    dialog.comboBox_cumulativeTemporalResolution.setEnabled(dialog.checkBox_cumulative.isChecked())
    dialog.checkBox_cumulative.setToolTip("Running amounts reset at UTC calendar boundaries. CF interval bounds are required unless ERA timing is recognized.")


def browse(dialog):
    """Select a folder without scanning its contents."""
    folder = QFileDialog.getExistingDirectory(dialog, "Meteorology folder", dialog.comboBox_inputFolder.currentText() or dialog.project_directory or "")
    if folder:
        dialog.comboBox_inputFolder.setCurrentText(folder)


def validate(dialog):
    """Validate user choices; the caller scans only after this dialog accepts."""
    try:
        text = dialog.comboBox_inputFolder.currentText().strip()
        if not text:
            raise ValueError("Select an input folder.")
        folder = Path(text).expanduser()
        pattern = dialog.lineEdit_fileNameGlobDefinition.text().strip()
        if not folder.is_dir() or not pattern or Path(pattern).is_absolute() or ".." in Path(pattern).parts:
            raise ValueError("Select an existing input folder and a relative NetCDF glob.")
        dialog.spec = MeteoFolderSpec(
            dialog.variable, folder.resolve(), pattern=pattern,
            temporal_resolution=dialog.comboBox_temporalResolutionInput.currentText(),
            unit=dialog.comboBox_unitInput.currentText(),
            cumulative=dialog.checkBox_cumulative.isChecked(),
            cumulative_resolution=dialog.comboBox_cumulativeTemporalResolution.currentText()).normalized()
        validate_spec(dialog.spec)
    except (OSError, ValueError) as error:
        QMessageBox.warning(dialog, "Meteorology input", str(error))
        return False
    return True
