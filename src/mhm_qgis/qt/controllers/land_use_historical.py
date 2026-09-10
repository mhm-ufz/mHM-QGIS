# -*- coding: utf-8 -*-
"""Widget state for `land_use_historical_input.ui`.

Row construction stays on the dialog: each row's browse button closes over the
combo adapter built with it, so it can only be wired where the row is made.
"""
from __future__ import annotations

from pathlib import Path

from qgis.PyQt.QtWidgets import QFileDialog, QMessageBox

from qgis.PyQt import QtWidgets

from ...core.handlers import lookup
from ...core.handlers.lookup import LandUseInput, MAX_LAND_USE_PERIODS
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
            index, QtWidgets.QTableWidgetItem(f"Layer {index + 1}")
        )
    dialog._update_bounds()


def set_all_time(dialog) -> None:
    """Configure the same form for one all-time land-use layer."""
    dialog.set_layer_count(1)
    dialog._set_cell(dialog.tableWidget_landUseTimeInputs, 0, 0, 1900)
    dialog._set_cell(dialog.tableWidget_landUseTimeInputs, 0, 1, 2100)
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
    for index, period in enumerate(periods):
        dialog._set_cell(
            dialog.tableWidget_landUseTimeInputs,
            index,
            0,
            period.get("start_year"),
        )
        dialog._set_cell(
            dialog.tableWidget_landUseTimeInputs,
            index,
            1,
            period.get("end_year"),
        )
        dialog._select_path(dialog._rows[index].adapter, period.get("file_path"))
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


def _update_bounds(dialog, *_args) -> None:
    for index, row in enumerate(dialog._rows):
        row.start_label.setText(
            dialog._cell_text(dialog.tableWidget_landUseTimeInputs, index, 0)
        )
        row.end_label.setText(
            dialog._cell_text(dialog.tableWidget_landUseTimeInputs, index, 1)
        )


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


def _browse_lookup(dialog) -> None:
    path, _ = QtWidgets.QFileDialog.getOpenFileName(
        dialog,
        "Select Land-use Lookup Table",
        str(dialog.project_folder),
        "Lookup tables (*.csv *.txt);;All files (*)",
    )
    if path:
        dialog._select_combo_path(dialog.comboBox_lookupTableInput, path)
