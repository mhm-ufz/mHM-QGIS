"""Simulation-time widgets and their namelist state."""

from qgis.PyQt.QtCore import QDateTime, QSignalBlocker

from .version import selected as selected_version

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
    if dialog._loading_input_state or not dialog.project_folder:
        return
    from ...core.handlers.state.nml_settings import update_section

    result = _widget_values(dialog)
    update_section(dialog.project_folder, "time", result)
    from ...core.executions.meteo import covers_period
    from .workflow_state import invalidate, invalidate_meteo

    if covers_period(dialog.project_folder, result):
        invalidate(dialog, "run_mhm")
    else:
        invalidate_meteo(dialog)


def restore(dialog):
    from ...core.handlers.state.nml_settings import load_settings

    saved = (
        load_settings(dialog.project_folder).get("time", {})
        if dialog.project_folder
        else {}
    )
    for key, name in TIME_WIDGETS.items():
        widget = getattr(dialog, name)
        widget.setDisplayFormat(
            "yyyy-MM-dd HH:00"
            if selected_version(dialog).startswith("6")
            else "yyyy-MM-dd"
        )
        value = QDateTime.fromString(
            saved.get(key, "2000-01-01T00:00"), "yyyy-MM-ddTHH:mm"
        )
        if value.isValid():
            with QSignalBlocker(widget):
                widget.setDateTime(value)

