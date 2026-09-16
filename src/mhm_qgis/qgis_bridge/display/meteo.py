# -*- coding: utf-8 -*-
"""Canvas half of `groupBox_meteoDisplay`.

Pick a forcing variable and a date, and show that timestep as a raster. The
slider scrubs the same period; both controls drive one shared step index.
"""
from __future__ import annotations

from qgis.PyQt import QtCore
from qgis.PyQt.QtWidgets import QMessageBox

from ...core.meteorology.display import (
    VARIABLE_LABELS,
    available_meteo_variables,
    meteo_output_path,
    meteo_steps,
    resolve_meteo_output,
)
from ...core.post import step_for_when
from ...core.handlers.file.netcdf import grid_variables, read_slice
from .canvas import show_dataarray

def _as_qdatetime(value):
    return QtCore.QDateTime(value)


def _steps(dialog):
    """Return the selected variable's timestamps."""
    if not dialog.project_folder:
        return ()
    variable = dialog.comboBox_meteoVarDisplay.currentData()
    return meteo_steps(meteo_output_path(dialog.project_folder, variable))


def refresh(dialog) -> None:
    """Repopulate the variables and re-range the date and slider controls.

    Called after meteorology preparation and when a project is loaded, so the
    controls always describe what is actually on disk.
    """
    combo = dialog.comboBox_meteoVarDisplay
    slider = dialog.horizontalSlider_meteoTimeSelector
    editor = dialog.dateTimeEdit_meteoVarDisplay

    variables = (
        available_meteo_variables(dialog.project_folder)
        if dialog.project_folder
        else []
    )
    previous = combo.currentData()
    combo.blockSignals(True)
    combo.clear()
    for variable in variables:
        combo.addItem(VARIABLE_LABELS.get(variable, variable), variable)
    if previous in variables:
        combo.setCurrentIndex(variables.index(previous))
    combo.blockSignals(False)

    steps = _steps(dialog)
    enabled = bool(variables) and bool(steps)
    for widget in (combo, slider, editor, dialog.pushButton_meteoVarDisplay):
        widget.setEnabled(enabled)
    if not enabled:
        dialog.label_startTime.setText("")
        dialog.label_endTime.setText("")
        return

    first, last = steps[0], steps[-1]
    dialog.label_startTime.setText(first.isoformat())
    dialog.label_endTime.setText(last.isoformat())

    editor.blockSignals(True)
    editor.setDisplayFormat("yyyy-MM-dd HH:mm:ss")
    editor.setMinimumDateTime(_as_qdatetime(first))
    editor.setMaximumDateTime(_as_qdatetime(last))
    editor.setDateTime(_as_qdatetime(first))
    editor.blockSignals(False)

    slider.blockSignals(True)
    slider.setMinimum(0)
    slider.setMaximum(max(0, len(steps) - 1))
    slider.setValue(0)
    slider.blockSignals(False)


def slider_moved(dialog, value: int) -> None:
    """Move the date editor to the step the slider selected."""
    steps = _steps(dialog)
    if not steps:
        return
    editor = dialog.dateTimeEdit_meteoVarDisplay
    editor.blockSignals(True)
    editor.setDateTime(_as_qdatetime(steps[max(0, min(int(value), len(steps) - 1))]))
    editor.blockSignals(False)


def date_changed(dialog, value=None) -> None:
    """Move the slider to the step the date editor selected."""
    steps = _steps(dialog)
    if not steps:
        return
    chosen = dialog.dateTimeEdit_meteoVarDisplay.dateTime().toPyDateTime()
    slider = dialog.horizontalSlider_meteoTimeSelector
    slider.blockSignals(True)
    slider.setValue(step_for_when(steps, chosen))
    slider.blockSignals(False)


def display_selected_layer(dialog, checked=False) -> None:
    """Show the selected forcing variable at the selected date."""
    if not dialog.project_folder:
        QMessageBox.warning(dialog, "Display Meteorology", "Select a project folder first.")
        return
    variable = dialog.comboBox_meteoVarDisplay.currentData()
    if not variable:
        QMessageBox.warning(
            dialog,
            "Display Meteorology",
            "No prepared meteorology forcing was found for this project.",
        )
        return
    when = dialog.dateTimeEdit_meteoVarDisplay.dateTime().toPyDateTime()
    output = resolve_meteo_output(dialog.project_folder, variable, when)
    if output is None:
        QMessageBox.warning(
            dialog,
            "Display Meteorology",
            f"{variable} has no data for {when.isoformat()}.",
        )
        return
    # Same render path as the model-output display: one georeferenced slice.
    # Prepared forcing carries its own CRS, so none has to be supplied.
    grid = next(
        (v for v in grid_variables(output.path) if v.name == output.variable), None
    )
    if grid is None:
        QMessageBox.warning(
            dialog, "Display Meteorology", f"{variable} is not a gridded variable."
        )
        return
    data = read_slice(output.path, grid, output.band - 1)
    show_dataarray(dialog, data, output.name)


__all__ = ["date_changed", "display_selected_layer", "refresh", "slider_moved"]
