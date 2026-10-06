"""Populate and validate meteorology metadata without opening data files."""
from pathlib import Path

from qgis.PyQt.QtWidgets import QMessageBox

from ...core.meteorology.forcing.temporal import (
    CADENCES,
    VARIABLES,
    normalize_unit,
    validate_spec,
)
from ...core.meteorology.forcing.types import MeteoFolderSpec
from ..dialogs.file_picker import open_directory


def populate(dialog, initial):
    """Restore a variable's plain settings in the reusable form."""
    dialog.setWindowTitle(f"{dialog.variable} input")
    dialog.groupBox.setTitle(dialog.variable)
    dialog.comboBox_inputFolder.setEditable(True)
    folder = str(initial.get("folder", ""))
    dialog.comboBox_inputFolder.addItem(folder)
    dialog.lineEdit_fileNameGlobDefinition.setText(initial.get("pattern", "*.nc"))
    dialog.comboBox_temporalResolutionInput.setCurrentText(initial.get("temporal_resolution", "Daily"))
    populate_output_resolutions(
        dialog, initial.get("output_temporal_resolution") or
        initial.get("temporal_resolution", "Daily"))
    aggregation = initial.get("aggregation") or "<None>"
    dialog.comboBox_temporalAggregationTypeMeteoOutput.setCurrentText(aggregation)
    update_aggregation_control(dialog)
    dialog.comboBox_unitInput.addItems(VARIABLES[dialog.variable][2])
    dialog.comboBox_unitInput.setCurrentText(normalize_unit(initial.get("unit", VARIABLES[dialog.variable][2][0])))


def populate_output_resolutions(dialog, selected=None):
    """Offer the input cadence and every coarser cadence."""
    combo = dialog.comboBox_temporalResolutionOutput
    input_cadence = dialog.comboBox_temporalResolutionInput.currentText()
    values = list(CADENCES)
    allowed = values[values.index(input_cadence):] if input_cadence in values else []
    combo.blockSignals(True)
    combo.clear()
    combo.addItems(allowed)
    combo.setCurrentText(selected if selected in allowed else input_cadence)
    combo.blockSignals(False)
    update_aggregation_control(dialog)


def update_aggregation_control(dialog):
    combo = dialog.comboBox_temporalAggregationTypeMeteoOutput
    same = (dialog.comboBox_temporalResolutionOutput.currentText()
            == dialog.comboBox_temporalResolutionInput.currentText())
    combo.setEnabled(not same)
    if same:
        combo.setCurrentText("<None>")


def browse(dialog):
    """Select a folder without scanning its contents."""
    folder = open_directory(dialog, "Meteorology folder")
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
            output_temporal_resolution=dialog.comboBox_temporalResolutionOutput.currentText(),
            aggregation=("" if not dialog.comboBox_temporalAggregationTypeMeteoOutput.isEnabled()
                         else dialog.comboBox_temporalAggregationTypeMeteoOutput.currentText()),
            unit=dialog.comboBox_unitInput.currentText()).normalized()
        validate_spec(dialog.spec)
    except (OSError, ValueError) as error:
        QMessageBox.warning(dialog, "Meteorology input", str(error))
        return False
    return True
