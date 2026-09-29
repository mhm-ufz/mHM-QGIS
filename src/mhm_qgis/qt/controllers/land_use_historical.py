# -*- coding: utf-8 -*-
"""Widget state for `land_use_historical_input.ui`.

Row construction stays on the dialog: each row's browse button closes over the
combo adapter built with it, so it can only be wired where the row is made.
"""
from __future__ import annotations

from pathlib import Path

from qgis.PyQt.QtCore import QDateTime, QSignalBlocker

from qgis.PyQt import QtWidgets

from ...core.handlers import lookup
from ...core.handlers.lookup import LandUseInput, MAX_LAND_USE_PERIODS
from ..dialogs.file_picker import open_file
from ..dialogs.input_selection import scan_project_inputs

def set_layer_count(dialog, count: int) -> None:
    count = min(MAX_LAND_USE_PERIODS, max(1, int(count)))
    dialog.spinBox_nLandUseLayers.setValue(count)
    dialog.tableWidget_landUseTimeInputs.setRowCount(count)
    while len(dialog._rows) < count:
        dialog._rows.append(dialog._new_land_use_row(len(dialog._rows) + 1))
    while len(dialog._rows) > count:
        dialog._delete_row(dialog._rows.pop())
    for index in range(count):
        dialog.tableWidget_landUseTimeInputs.setVerticalHeaderItem(
            index, QtWidgets.QTableWidgetItem(f"Period {index + 1}")
        )
        for column in (0, 1):
            if dialog.tableWidget_landUseTimeInputs.cellWidget(index, column) is not None:
                continue
            editor = QtWidgets.QDateTimeEdit(dialog)
            editor.setDisplayFormat("yyyy-MM-dd HH:mm:ss")
            editor.setCalendarPopup(True)
            lower = QDateTime.fromString(dialog.simulation_start[:19], "yyyy-MM-ddTHH:mm:ss")
            upper = QDateTime.fromString(dialog.simulation_end[:19], "yyyy-MM-ddTHH:mm:ss")
            if not lower.isValid():
                lower = QDateTime.fromString(dialog.simulation_start[:16], "yyyy-MM-ddTHH:mm")
            if not upper.isValid():
                upper = QDateTime.fromString(dialog.simulation_end[:16], "yyyy-MM-ddTHH:mm")
            editor.setDateTimeRange(lower, upper)
            previous = dialog.tableWidget_landUseTimeInputs.cellWidget(index - 1, 1)
            editor.setDateTime((previous.dateTime() if previous else lower) if column == 0 else upper)
            dialog.tableWidget_landUseTimeInputs.setCellWidget(index, column, editor)
            editor.dateTimeChanged.connect(
                lambda value, row=index, col=column: boundary_changed(dialog, row, col, value))
    dialog._update_bounds()


def set_all_time(dialog) -> None:
    """Configure the same form for one all-time land-use layer."""
    dialog.set_layer_count(1)
    dialog.spinBox_nLandUseLayers.setEnabled(False)
    dialog.pushButton_addLandUseInputWidgets.setEnabled(False)
    dialog.tableWidget_landUseTimeInputs.setEnabled(False)
    dialog._update_bounds()


def set_input(dialog, value) -> None:
    data = value.as_dict() if isinstance(value, LandUseInput) else value
    periods = list(data.get("periods", ()))
    if len(periods) > MAX_LAND_USE_PERIODS:
        raise ValueError(
            f"Land use supports at most {MAX_LAND_USE_PERIODS} periods."
        )
    dialog.set_layer_count(len(periods) or 1)
    from ...core.handlers.lookup import LandUsePeriod
    from ...core.handlers.lookup.manifests import period_datetime
    for index, period in enumerate(periods):
        period = LandUsePeriod.from_dict(period)
        if period.start_time < period_datetime(dialog.simulation_start) or period.end_time > period_datetime(dialog.simulation_end):
            raise ValueError("Saved land-use periods exceed the simulation range; update the periods first.")
        for column, value in enumerate((period.start_time, period.end_time)):
            editor = dialog.tableWidget_landUseTimeInputs.cellWidget(index, column)
            with QSignalBlocker(editor):
                editor.setDateTime(QDateTime.fromString(value.isoformat(), "yyyy-MM-ddTHH:mm:ss"))
        dialog._select_path(dialog._rows[index].adapter, period.file_path)
    lookup = str(data.get("lookup_table", "") or "")
    if lookup:
        dialog._select_combo_path(dialog.comboBox_lookupTableInput, lookup)
    dialog._lookup_changed()
    dialog.comboBox_mappingFieldInput.setCurrentText(
        str(data.get("mapping_field", "") or "")
    )
    dialog.comboBox_classFieldInput.setCurrentText(
        str(data.get("class_field", "") or "")
    )
    dialog._update_bounds()


def boundary_changed(dialog, row, column, value):
    """Keep the shared boundary identical in both adjoining periods."""
    other_row, other_column = (row + 1, 0) if column == 1 else (row - 1, 1)
    other = dialog.tableWidget_landUseTimeInputs.cellWidget(other_row, other_column)
    if other is not None:
        with QSignalBlocker(other):
            other.setDateTime(value)
    dialog._update_bounds()


def _update_bounds(dialog, *_args) -> None:
    for index, row in enumerate(dialog._rows):
        for column, label in enumerate((row.start_label, row.end_label)):
            editor = dialog.tableWidget_landUseTimeInputs.cellWidget(index, column)
            label.setText(editor.dateTime().toString("yyyy-MM-dd HH:mm") if editor else "")


def _populate_lookup(dialog) -> None:
    dialog.comboBox_lookupTableInput.clear()
    for item in scan_project_inputs(dialog.project_folder, "lookup"):
        dialog.comboBox_lookupTableInput.addItem(item.label, item.data["path"])
    dialog.comboBox_lookupTableInput.setCurrentIndex(-1)


def _lookup_changed(dialog, *_args) -> None:
    path = dialog.comboBox_lookupTableInput.currentData()
    dialog._lookup_path = str(path or "")
    dialog._lookup_error = ""
    mapping = dialog.comboBox_mappingFieldInput.currentText()
    class_field = dialog.comboBox_classFieldInput.currentText()
    dialog.comboBox_mappingFieldInput.clear()
    dialog.comboBox_classFieldInput.clear()
    if dialog._lookup_path:
        try:
            fields = lookup.columns(dialog._lookup_path)
        except Exception as error:
            dialog._lookup_error = f"Could not read land-use lookup table: {error}"
            fields = []
        dialog.comboBox_mappingFieldInput.addItems(fields)
        dialog.comboBox_classFieldInput.addItems(fields)
    dialog.comboBox_mappingFieldInput.setCurrentText(mapping)
    dialog.comboBox_classFieldInput.setCurrentText(class_field)


def _browse_lookup(dialog, _checked=False) -> None:
    path, _ = open_file(
        dialog,
        "Select Land-use Lookup Table",
        "Lookup tables (*.csv *.txt);;All files (*)",
    )
    if path:
        dialog._select_combo_path(dialog.comboBox_lookupTableInput, path)
