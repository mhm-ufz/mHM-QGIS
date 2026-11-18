"""Populate and validate meteorology metadata without opening data files."""
import calendar
from pathlib import Path

from qgis.core import QgsCoordinateReferenceSystem
from qgis.PyQt.QtCore import QSignalBlocker, Qt
from qgis.PyQt.QtWidgets import QApplication, QMessageBox

from ...core.meteorology.forcing.cache import inspect_meteo_folder_cached
from ...externals.mhm_tools_handler import (
    CADENCES,
    RESETS,
    VARIABLES,
    MeteoFolderSpec,
    get_meteo_file_defaults,
    normalize_unit,
    validate_spec,
)
from ...qgis_bridge.layers import crs_string
from ..dialogs.file_picker import open_file


def populate(dialog, initial):
    """Restore a variable's plain settings in the reusable form."""
    dialog.setWindowTitle(f"{dialog.variable} input")
    dialog.groupBox.setTitle(dialog.variable)
    dialog.comboBox_inputFile.setEditable(True)
    folder, pattern = Path(str(initial.get("folder", ""))), initial.get("pattern", "*.nc")
    matches = sorted(folder.glob(pattern)) if initial.get("folder") and folder.is_dir() else []
    dialog.comboBox_inputFile.addItem(str(matches[0]) if matches else str(initial.get("folder", "")))
    dialog.lineEdit_fileNameGlobDefinition.setText(pattern)
    dialog.comboBox_temporalResolutionInput.setCurrentText(initial.get("temporal_resolution", "Daily"))
    populate_output_resolutions(
        dialog, initial.get("output_temporal_resolution") or
        initial.get("temporal_resolution", "Daily"))
    aggregation = initial.get("aggregation") or "<None>"
    dialog.comboBox_temporalAggregationTypeMeteoOutput.setCurrentText(aggregation)
    update_aggregation_control(dialog)
    dialog.comboBox_unitInput.addItems(VARIABLES[dialog.variable][2])
    dialog.comboBox_unitInput.setCurrentText(normalize_unit(initial.get("unit", VARIABLES[dialog.variable][2][0])))
    dialog.mProjectionSelectionWidget_crs.setCrs(QgsCoordinateReferenceSystem(initial.get("crs") or ""))
    if initial.get("variable"):
        dialog.comboBox_varName.addItem(initial["variable"])
    dialog.groupBox_cumulative.setChecked(bool(initial.get("cumulative")))
    dialog.comboBox_cumulativeInterval.setEnabled(False)  # accumulated at the input step
    populate_resets(dialog, initial.get("cumulative_resolution"), initial.get("cumulative_reset"))


def populate_resets(dialog, interval=None, reset=None):
    """The accumulation is sampled at the input step and resets at a coarser one."""
    step = dialog.comboBox_temporalResolutionInput.currentText()
    dialog.comboBox_cumulativeInterval.setCurrentText(step)
    cadences = list(CADENCES)
    coarser = cadences[cadences.index(step) + 1:] if step in cadences else []
    combo, previous = dialog.comboBox_resetInterval, dialog.comboBox_resetInterval.currentText()
    interval = interval or previous
    with QSignalBlocker(combo):
        combo.clear()
        combo.addItems(coarser)
        combo.setCurrentText(interval if interval in coarser else (coarser[0] if coarser else ""))
    if reset is None and combo.currentText() == previous:  # the same reset unit keeps its choice
        reset = dialog.comboBox_resetTimestamp.currentData()
    populate_reset_timestamps(dialog, reset)


def populate_reset_timestamps(dialog, reset=None):
    """The last hour, day or month a cycle covers, the last one by default."""
    combo, interval = dialog.comboBox_resetTimestamp, dialog.comboBox_resetInterval.currentText()
    values = RESETS.get(interval, range(0))
    combo.clear()
    for value in values:
        label = f"{value:02d}" if interval == "Daily" else calendar.month_abbr[value] if interval == "Yearly" else str(value)
        combo.addItem(label, value)
    combo.setCurrentIndex(values.index(reset) if reset in values else len(values) - 1)


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
    """Pick a NetCDF file; its name becomes the glob, to widen when the record spans files."""
    path, _filter = open_file(dialog, "Meteorology NetCDF file", "NetCDF (*.nc)")
    if path:
        dialog.comboBox_inputFile.setCurrentText(path)
        dialog.lineEdit_fileNameGlobDefinition.setText(Path(path).name)


def scan_files(dialog):
    """Preselect what the first matching file declares, then check every matching file's metadata
    (one grid, unit and time step, no gap from start to end); Set reuses that scan."""
    values = draft(dialog)
    QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
    try:
        found = get_meteo_file_defaults(MeteoFolderSpec(
            dialog.variable, Path(values["folder"]).expanduser(), pattern=values["pattern"],
            variable=values["variable"]))
    except (OSError, RuntimeError, ValueError) as error:
        QMessageBox.warning(dialog, "Meteorology input", str(error))
        return
    finally:
        QApplication.restoreOverrideCursor()
    dialog.comboBox_varName.clear()
    dialog.comboBox_varName.addItems(found["variables"])
    dialog.comboBox_varName.setCurrentIndex(dialog.comboBox_varName.findText(found.get("variable", "")))  # -1: none
    if "temporal_resolution" in found:  # its signal re-offers the output resolutions
        dialog.comboBox_temporalResolutionInput.setCurrentText(found["temporal_resolution"])
    if "unit" in found:
        dialog.comboBox_unitInput.setCurrentText(found["unit"])
    if "crs" in found:
        dialog.mProjectionSelectionWidget_crs.setCrs(QgsCoordinateReferenceSystem(found["crs"]))
    missing = [label for key, label in (("variable", "variable: pick one and Scan again"),
                                        ("temporal_resolution", "time step"), ("unit", "unit"),
                                        ("crs", "CRS")) if key not in found]
    note = f"Read from {found['file']}" + (f"; not declared: {', '.join(missing)}" if missing else "")
    dialog.pushButton_scanFiles.setToolTip(note)
    if "variable" not in found or not validate(dialog):
        return
    QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
    try:
        metadata = inspect_meteo_folder_cached(dialog.setup_folder, dialog.spec)
    except (OSError, RuntimeError, ValueError, KeyError) as error:
        QMessageBox.warning(dialog, "Meteorology input", str(error))
        return
    finally:
        QApplication.restoreOverrideCursor()
    dialog.pushButton_scanFiles.setToolTip(
        f"{note}. {len(metadata.files)} files: {metadata.start_time[:16]} to {metadata.end_time[:16]}.")


def _folder(text):
    """The picked file's folder; a folder itself (setups saved before files were picked) stays."""
    return text if not text or Path(text).expanduser().is_dir() else str(Path(text).parent)


def draft(dialog):
    """The form's values in `populate`'s shape, unchecked."""
    return {
        "folder": _folder(dialog.comboBox_inputFile.currentText().strip()),
        "pattern": dialog.lineEdit_fileNameGlobDefinition.text().strip(),
        "temporal_resolution": dialog.comboBox_temporalResolutionInput.currentText(),
        "output_temporal_resolution": dialog.comboBox_temporalResolutionOutput.currentText(),
        "aggregation": ("" if not dialog.comboBox_temporalAggregationTypeMeteoOutput.isEnabled()
                        else dialog.comboBox_temporalAggregationTypeMeteoOutput.currentText()),
        "unit": dialog.comboBox_unitInput.currentText(),
        "crs": crs_string(dialog.mProjectionSelectionWidget_crs.crs()),
        "variable": dialog.comboBox_varName.currentText(),
        "cumulative": dialog.groupBox_cumulative.isChecked(),
        "cumulative_resolution": dialog.comboBox_resetInterval.currentText(),
        "cumulative_reset": dialog.comboBox_resetTimestamp.currentData(),
    }


def validate(dialog):
    """Validate user choices; the caller scans only after this dialog accepts."""
    try:
        values = draft(dialog)
        if not values["folder"]:
            raise ValueError("Select an input NetCDF file.")
        folder, pattern = Path(values["folder"]).expanduser(), values["pattern"]
        if not folder.is_dir() or not pattern or Path(pattern).is_absolute() or ".." in Path(pattern).parts:
            raise ValueError("Select an existing NetCDF file and a relative glob.")
        options = {key: value for key, value in values.items() if key != "folder"}
        dialog.spec = MeteoFolderSpec(
            dialog.variable, folder.resolve(), **{**options, "crs": options["crs"] or None}).normalized()
        validate_spec(dialog.spec)
    except (OSError, ValueError) as error:
        QMessageBox.warning(dialog, "Meteorology input", str(error))
        return False
    return True
