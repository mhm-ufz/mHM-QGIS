# -*- coding: utf-8 -*-
"""Canvas half of `groupBox_outputDisplay`.

Pick a variable an mHM run produced and a moment, and show that timestep as a
raster. What is available and what one step looks like is decided by
:mod:`mhm_qgis.core.post`; this module only drives the widgets and the canvas.
"""
from __future__ import annotations

from qgis.PyQt import QtCore
from qgis.PyQt.QtWidgets import QMessageBox

from ...core.post import (
    available_variables,
    resolve_output,
    simulation_steps,
    step_for_when,
)
from .canvas import show_dataarray

_DATE_FORMAT = "yyyy-MM-dd"


def _as_qdatetime(value) -> QtCore.QDateTime:
    return QtCore.QDateTime(
        QtCore.QDate(value.year, value.month, value.day),
        QtCore.QTime(value.hour, value.minute),
    )


def _steps(dialog) -> list:
    """Return the timesteps of the open project's output, if any."""
    if not dialog.project_folder:
        return []
    return simulation_steps(dialog.project_folder)


def _display_crs(dialog):
    """Return the CRS to place the output with.

    mHM writes no CRS, so the project's own CRS decides placement. A run driven
    by this plugin writes on that grid; anything else has to be told.
    """
    try:
        crs = dialog.get_crs()
    except Exception:
        return None
    if crs is None or not crs.isValid():
        return None
    return crs.authid() or crs.toWkt()


def refresh(dialog) -> None:
    """Repopulate the variables and re-range the time controls.

    mHM is launched into a terminal and never reports back, so there is no
    completion signal to hang this on -- it is called when a project is opened,
    when the Outputs tab is shown, and again before displaying.
    """
    combo = dialog.comboBox_outputVarDisplay
    slider = dialog.horizontalSlider_ouputTimeSelector
    editor = dialog.dateTimeEdit_outputVarDisplay

    variables = available_variables(dialog.project_folder) if dialog.project_folder else []
    previous = combo.currentData()
    names = [variable.name for variable in variables]
    combo.blockSignals(True)
    combo.clear()
    for variable in variables:
        combo.addItem(variable.display_label, variable.name)
    if previous in names:
        combo.setCurrentIndex(names.index(previous))
    combo.blockSignals(False)

    steps = _steps(dialog)
    enabled = bool(variables) and bool(steps)
    for widget in (combo, slider, editor, dialog.pushButton_outputVarDisplay):
        widget.setEnabled(enabled)
    if not enabled:
        dialog.label_startTimeSimulationPeriod.setText("")
        dialog.label_endTimeSimulationPeriod.setText("")
        return

    first, last = steps[0], steps[-1]
    dialog.label_startTimeSimulationPeriod.setText(first.date().isoformat())
    dialog.label_endTimeSimulationPeriod.setText(last.date().isoformat())

    editor.blockSignals(True)
    editor.setDisplayFormat(_DATE_FORMAT)
    editor.setMinimumDateTime(_as_qdatetime(first))
    editor.setMaximumDateTime(_as_qdatetime(last))
    editor.setDateTime(_as_qdatetime(first))
    editor.blockSignals(False)

    slider.blockSignals(True)
    slider.setMinimum(0)
    slider.setMaximum(len(steps) - 1)
    slider.setValue(0)
    slider.blockSignals(False)


def slider_moved(dialog, value: int) -> None:
    """Move the date editor to the step the slider selected."""
    steps = _steps(dialog)
    if not steps:
        return
    index = max(0, min(int(value), len(steps) - 1))
    editor = dialog.dateTimeEdit_outputVarDisplay
    editor.blockSignals(True)
    editor.setDateTime(_as_qdatetime(steps[index]))
    editor.blockSignals(False)


def date_changed(dialog, value=None) -> None:
    """Move the slider to the step at or before the chosen moment.

    The steps are not evenly spaced -- a monthly run leaves 28 to 31 day gaps --
    so the position is searched for rather than calculated.
    """
    steps = _steps(dialog)
    if not steps:
        return
    chosen = dialog.dateTimeEdit_outputVarDisplay.dateTime().toPyDateTime()
    slider = dialog.horizontalSlider_ouputTimeSelector
    slider.blockSignals(True)
    slider.setValue(step_for_when(steps, chosen))
    slider.blockSignals(False)


def display_selected_layer(dialog, checked=False) -> None:
    """Show the selected output variable at the selected step."""
    if not dialog.project_folder:
        QMessageBox.warning(dialog, "Display Output", "Select a project folder first.")
        return
    name = dialog.comboBox_outputVarDisplay.currentData()
    if not name:
        refresh(dialog)
        name = dialog.comboBox_outputVarDisplay.currentData()
    if not name:
        QMessageBox.warning(
            dialog,
            "Display Output",
            "No mHM output was found for this project. Run the model first.",
        )
        return
    crs = _display_crs(dialog)
    if crs is None:
        QMessageBox.warning(
            dialog,
            "Display Output",
            "mHM output carries no projection of its own. Set the project CRS "
            "so the raster can be placed.",
        )
        return
    step = dialog.horizontalSlider_ouputTimeSelector.value()
    raster = resolve_output(dialog.project_folder, name, step, crs=crs)
    if raster is None:
        QMessageBox.warning(
            dialog, "Display Output", f"{name} could not be read from the output."
        )
        return
    show_dataarray(dialog, raster.data, raster.name, when=raster.when)


__all__ = ["date_changed", "display_selected_layer", "refresh", "slider_moved"]
