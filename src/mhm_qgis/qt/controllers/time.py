"""Simulation-time widgets and their namelist state, and the time window of a NetCDF glob."""

from pathlib import Path

from qgis.PyQt.QtCore import QDateTime, QSignalBlocker, Qt
from qgis.PyQt.QtWidgets import QApplication, QDialog, QMessageBox

from ..dialogs.file_picker import open_file
from .version import selected as selected_version

FORMAT = "yyyy-MM-ddTHH:mm"

TIME_WIDGETS = {
    "sim_start": "dateTimeEdit_simulationStartTimeInput",
    "eval_start": "dateTimeEdit_evaluationStartTimeInput",
    "sim_end": "dateTimeEdit_simulationEndTimeInput",
}


def _widget_values(dialog):
    return {
        key: getattr(dialog, name).dateTime().toString("yyyy-MM-ddTHH:mm")
        for key, name in TIME_WIDGETS.items()
    }


def values(dialog):
    from ...core.handlers.state.nml_settings import validate_times

    return validate_times(_widget_values(dialog), selected_version(dialog))


def save(dialog):
    if dialog._loading_input_state or not dialog.setup_folder:
        return
    from ...core.handlers.state.nml_settings import update_section

    result = _widget_values(dialog)
    update_section(dialog.setup_folder, "time", result)
    from ...core.executions.meteo import covers_period
    from .workflow_state import invalidate, invalidate_meteo

    if covers_period(dialog.setup_folder, result):
        invalidate(dialog, "run_mhm")
    else:
        invalidate_meteo(dialog)
    show_period(dialog)



def set_period(dialog):
    """Set on the Time Period page: the checked simulation period, into nml-settings.json."""
    from qgis.PyQt.QtWidgets import QMessageBox

    try:
        values(dialog)
    except ValueError as error:
        QMessageBox.warning(dialog, "Simulation period", str(error))
        return
    save(dialog)
    dialog.save_input_state()  # the Time Period status: a saved period completes it
    from .workflow_state import report

    report(dialog, "period")


def show_period(dialog):
    """The plugin's time toolbar on the simulation period, a step a day, unless a run's outputs hold it.

    Without QGIS Desktop the toolbar lives in the map window, so it is only updated once it exists.
    """
    if dialog._post.intervals is not None or (dialog.iface is None and dialog._post.toolbar is None):
        return
    import numpy as np

    from . import post_context

    period = _widget_values(dialog)
    days = np.arange(np.datetime64(period["sim_start"][:10]), np.datetime64(period["sim_end"][:10]) + 1)
    if len(days):
        post_context.time_axis(dialog, np.column_stack([days, days]), lambda _index: None)


def restore(dialog):
    from ...core.handlers.state.nml_settings import load_settings

    saved = (
        load_settings(dialog.setup_folder).get("time", {})
        if dialog.setup_folder
        else {}
    )
    for key, name in TIME_WIDGETS.items():
        widget = getattr(dialog, name)
        value = QDateTime.fromString(
            saved.get(key, "2000-01-01T00:00"), "yyyy-MM-ddTHH:mm"
        )
        if value.isValid():
            with QSignalBlocker(widget):
                widget.setDateTime(value)
    evaluation_changed(dialog)
    if saved:
        show_period(dialog)


def warming_changed(dialog):
    """Evaluation starts the warming days after the simulation start."""
    widget = dialog.dateTimeEdit_evaluationStartTimeInput
    with QSignalBlocker(widget):
        widget.setDateTime(dialog.dateTimeEdit_simulationStartTimeInput.dateTime().addDays(
            dialog.spinBox_warmingDays.value()))


def evaluation_changed(dialog):
    """The warming days an edited evaluation start leaves."""
    with QSignalBlocker(dialog.spinBox_warmingDays):
        dialog.spinBox_warmingDays.setValue(dialog.dateTimeEdit_simulationStartTimeInput.dateTime().daysTo(
            dialog.dateTimeEdit_evaluationStartTimeInput.dateTime()))


def show_source(dialog):
    """The Time Period page's file window, as the dialog last scanned it."""
    dialog.label_fileStartTimeValue.setText(dialog._time_source.get("start", ""))
    dialog.label_fileEndTimeValue.setText(dialog._time_source.get("end", ""))


def open_window_dialog(dialog):
    """Scan a NetCDF glob's time window; OK proposes the simulation period from it (Set saves it):
    v6 runs up to the window's end, v5.13 through its last day."""
    from ...externals.mhm_tools_handler import covered_until
    from ..dialogs.time_period_netcdf_input import TimePeriodDialog

    prompt = TimePeriodDialog(dialog._time_source, dialog)
    if prompt.exec() != QDialog.DialogCode.Accepted:
        return
    dialog._time_source = window = prompt.found
    show_source(dialog)
    v6 = selected_version(dialog).startswith("6")
    end = covered_until(window["end"], window["step"]) if v6 else window["end"][:10] + "T00:00"
    for name, text in (("dateTimeEdit_simulationStartTimeInput", window["start"] if v6 else window["start"][:10] + "T00:00"),
                       ("dateTimeEdit_simulationEndTimeInput", end)):
        getattr(dialog, name).setDateTime(QDateTime.fromString(text[:16], FORMAT))
    warming_changed(dialog)
    dialog.save_input_state()


def populate_window(dialog, source):
    """The time-window dialog with the last scanned glob and its window."""
    dialog.comboBox_inputTimePeriodFile.setEditable(True)
    dialog.comboBox_inputTimePeriodFile.addItem(source.get("file", ""))
    dialog.lineEdit_timePeriodFileNameGlobDefinition.setText(source.get("pattern", "*.nc"))
    dialog.comboBox_timeVarName.addItems(source.get("times", []))
    dialog.comboBox_timeVarName.setCurrentText(source.get("time", ""))
    _show_window(dialog, source or None)


def browse_window_file(dialog):
    """Pick a NetCDF file; its name becomes the glob, to widen when the record spans files."""
    path, _filter = open_file(dialog, "NetCDF file with the time axis", "NetCDF (*.nc)")
    if path:
        dialog.comboBox_inputTimePeriodFile.setCurrentText(path)
        dialog.lineEdit_timePeriodFileNameGlobDefinition.setText(Path(path).name)
        dialog.comboBox_timeVarName.clear()


def scan_window(dialog):
    """The time axis of every file the glob matches: its start, end and step, or why not."""
    from ...externals.mhm_tools_handler import get_meteo_time_window

    file = Path(dialog.comboBox_inputTimePeriodFile.currentText().strip()).expanduser()
    pattern = dialog.lineEdit_timePeriodFileNameGlobDefinition.text().strip()
    QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
    try:
        found = get_meteo_time_window(file if file.is_dir() else file.parent, pattern, dialog.comboBox_timeVarName.currentText())
    except (OSError, RuntimeError, ValueError) as error:
        QMessageBox.warning(dialog, "Time period", str(error))
        return
    finally:
        QApplication.restoreOverrideCursor()
    with QSignalBlocker(dialog.comboBox_timeVarName):
        dialog.comboBox_timeVarName.clear()
        dialog.comboBox_timeVarName.addItems(found["times"])
        dialog.comboBox_timeVarName.setCurrentText(found["time"])
    _show_window(dialog, {**found, "file": str(file), "pattern": pattern})


def _show_window(dialog, window):
    dialog.found = window
    for label, key in ((dialog.label_fileStartTimeValue, "start"), (dialog.label_fileEndTimeValue, "end"),
                       (dialog.label_temporalResolutionValue, "step")):
        label.setText(window[key] if window else "")
