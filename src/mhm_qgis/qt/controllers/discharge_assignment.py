# -*- coding: utf-8 -*-
"""Widget state for the two discharge-assignment forms.

`discharge_table_assignment_dialog.ui` and
`domain_and_discharge_table_assignment_dialog.ui` are the same dialog with and
without the domain column, and share one implementation, so they share one
controller rather than one re-exporting the other.
"""
from __future__ import annotations

import os
from pathlib import Path

from qgis.core import QgsMapLayer, QgsProject, QgsVectorLayer
from qgis.PyQt.QtWidgets import QComboBox, QFileDialog, QMessageBox


def _row_type():
    """Import lazily: `dialogs.discharge_assignment` imports this module at load time."""
    from ..dialogs.discharge_assignment import _AssignmentRow

    return _AssignmentRow

def _configure_row(dialog, row: _AssignmentRow) -> None:
    record = dialog._initial_records.get(row.outlet_id, {})
    row.id_value.setText(row.outlet_id)
    dialog._populate_discharge_layers(row.discharge)

    is_gauge = bool(
        record.get(
            "is_gauged",
            record.get("gauged", dialog.default_is_gauge),
        )
    )
    row.gauge.setChecked(is_gauge)

    source = str(record.get("discharge_file", "") or "")
    if source:
        dialog._select_discharge_source(row.discharge, source)
    row.gauge.toggled.connect(
        lambda enabled, current=row: dialog._set_discharge_enabled(
            current, enabled
        )
    )
    row.browse.clicked.connect(
        lambda checked=False, current=row: dialog._browse_discharge(current)
    )
    dialog._set_discharge_enabled(row, is_gauge)


def _populate_discharge_layers(dialog, combo: QComboBox) -> None:
    combo.clear()
    combo.addItem("", None)
    try:
        layers = QgsProject.instance().mapLayers().values()
    except Exception:
        layers = ()
    for layer in layers:
        try:
            if layer.isValid() and layer.type() == QgsMapLayer.LayerType.VectorLayer:
                combo.addItem(layer.name(), layer)
        except Exception:
            continue


def _select_discharge_source(dialog, combo: QComboBox, source: str) -> None:
    resolved = dialog._resolve_input_source(source)
    target = dialog._normal_source(resolved)
    for index in range(combo.count()):
        layer = combo.itemData(index)
        if layer is not None and dialog._normal_source(layer.source()) == target:
            combo.setCurrentIndex(index)
            return
    if os.path.isfile(resolved):
        layer = QgsVectorLayer(resolved, Path(resolved).name, "ogr")
        if layer.isValid():
            combo.addItem(layer.name(), layer)
            combo.setCurrentIndex(combo.count() - 1)


def _set_discharge_enabled(row: _AssignmentRow, enabled: bool) -> None:
    row.discharge.setEnabled(bool(enabled))
    row.browse.setEnabled(bool(enabled))


def _browse_discharge(dialog, row: _AssignmentRow) -> None:
    project_folder = getattr(dialog.parent(), "project_folder", "") or ""
    path, _ = QFileDialog.getOpenFileName(
        dialog,
        "Select discharge data",
        str(project_folder),
        "Discharge data (*.csv *.txt);;All files (*)",
    )
    if not path:
        return
    layer = QgsVectorLayer(path, Path(path).name, "ogr")
    if not layer.isValid():
        QMessageBox.warning(
            dialog,
            "Invalid discharge table",
            f"Could not read the selected table:\n{path}",
        )
        return
    target = dialog._normal_source(path)
    for index in range(row.discharge.count()):
        candidate = row.discharge.itemData(index)
        if (
            candidate is not None
            and dialog._normal_source(candidate.source()) == target
        ):
            row.discharge.setCurrentIndex(index)
            return
    row.discharge.addItem(layer.name(), layer)
    row.discharge.setCurrentIndex(row.discharge.count() - 1)
