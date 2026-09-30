# -*- coding: utf-8 -*-
"""Per-outlet gauge and discharge-table assignment for the DEM-extent domain."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from qgis.core import QgsMapLayer, QgsProject, QgsVectorLayer
from qgis.PyQt.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QLabel,
    QMessageBox,
    QPushButton,
)

from ...core.morphology.hydrology.outlets import OutletAssignment
from ..ui import load_form
from .file_picker import open_file

Ui_DischargeTableAssignmentDialog = load_form("discharge_table_assignment_dialog.ui")


@dataclass
class _AssignmentRow:
    outlet_id: str
    id_value: QLabel
    gauge: QCheckBox
    discharge: QComboBox
    browse: QPushButton


class DischargeTableAssignmentDialog(QDialog, Ui_DischargeTableAssignmentDialog):
    """Assign optional discharge tables to all supplied outlets.

    The Designer form holds the first row; one row per further outlet is built
    here, and each row's wiring is made where the row is built.
    """

    def __init__(
        self,
        station_ids: list[str],
        parent=None,
        initial_records: Mapping[str, Mapping] | None = None,
    ):
        super().__init__(parent)
        self.setupUi(self)
        self._rows: list[_AssignmentRow] = []
        self._initial_records = initial_records or {}
        for index in range(self.rows_layout.count() - 1, -1, -1):
            item = self.rows_layout.itemAt(index)
            if item is not None and item.spacerItem() is not None:
                self.rows_layout.takeAt(index)
        for row_index, outlet_id in enumerate(station_ids):
            row = (
                self._designer_row(str(outlet_id))
                if row_index == 0
                else self._create_row(row_index, str(outlet_id))
            )
            self._configure_row(row)
            self._rows.append(row)
        if not station_ids:
            for widget in (
                self.label_pourPointID,
                self.label_pourPointIDValue,
                self.checkBox_isGauge,
                self.comboBox_dischargeTableInput,
                self.pushButton_browseDischargeTable1,
            ):
                widget.hide()

    def _designer_row(self, outlet_id: str) -> _AssignmentRow:
        return _AssignmentRow(
            outlet_id=outlet_id,
            id_value=self.label_pourPointIDValue,
            gauge=self.checkBox_isGauge,
            discharge=self.comboBox_dischargeTableInput,
            browse=self.pushButton_browseDischargeTable1,
        )

    def _create_row(self, row_index: int, outlet_id: str) -> _AssignmentRow:
        suffix = row_index + 1
        label = QLabel("Pour Point ID:", self.rows_widget)
        label.setObjectName(f"label_pourPointID{suffix}")
        id_value = QLabel(self.rows_widget)
        id_value.setObjectName(f"label_pourPointIDValue{suffix}")
        gauge = QCheckBox("Is Gauge?", self.rows_widget)
        gauge.setObjectName(f"checkBox_isGauge{suffix}")
        discharge = QComboBox(self.rows_widget)
        discharge.setObjectName(f"comboBox_dischargeTableInput{suffix}")
        browse = QPushButton("...", self.rows_widget)
        browse.setObjectName(f"pushButton_browseDischargeTable{suffix}")
        browse.setMaximumWidth(40)
        for column, widget in enumerate((label, id_value, gauge, discharge, browse)):
            self.rows_layout.addWidget(widget, row_index, column)
        return _AssignmentRow(outlet_id, id_value, gauge, discharge, browse)

    def _configure_row(self, row: _AssignmentRow) -> None:
        record = self._initial_records.get(row.outlet_id, {})
        row.id_value.setText(row.outlet_id)
        row.discharge.clear()
        row.discharge.addItem("", None)
        for layer in QgsProject.instance().mapLayers().values():
            if layer.isValid() and layer.type() == QgsMapLayer.LayerType.VectorLayer:
                row.discharge.addItem(layer.name(), layer)
        is_gauge = bool(record.get("is_gauged", True))
        row.gauge.setChecked(is_gauge)
        source = str(record.get("discharge_file", "") or "")
        if source:
            self._select_discharge_source(row.discharge, source)
        row.gauge.toggled.connect(
            lambda enabled, current=row: self._set_discharge_enabled(current, enabled)
        )
        # Dynamic discharge browse button → _browse_discharge
        row.browse.clicked.connect(
            lambda checked=False, current=row: self._browse_discharge(current)
        )
        self._set_discharge_enabled(row, is_gauge)

    def _resolve_input_source(self, source: str) -> str:
        path = str(source).split("|", 1)[0]
        project_folder = getattr(self.parent(), "project_folder", None)
        if path and project_folder and not os.path.isabs(path):
            path = os.path.join(str(project_folder), path)
        return os.path.abspath(path) if path else ""

    @staticmethod
    def _normal_source(source: str) -> str:
        path = str(source or "").split("|", 1)[0].split("?", 1)[0]
        return os.path.normcase(os.path.abspath(path)) if path else ""

    def _select_layer(self, combo: QComboBox, path: str) -> bool:
        """Select the combo entry reading `path`; return whether one exists."""
        target = self._normal_source(path)
        for index in range(combo.count()):
            layer = combo.itemData(index)
            if layer is not None and self._normal_source(layer.source()) == target:
                combo.setCurrentIndex(index)
                return True
        return False

    def _select_discharge_source(self, combo: QComboBox, source: str) -> None:
        resolved = self._resolve_input_source(source)
        if self._select_layer(combo, resolved) or not os.path.isfile(resolved):
            return
        layer = QgsVectorLayer(resolved, Path(resolved).name, "ogr")
        if layer.isValid():
            combo.addItem(layer.name(), layer)
            combo.setCurrentIndex(combo.count() - 1)

    @staticmethod
    def _set_discharge_enabled(row: _AssignmentRow, enabled: bool) -> None:
        row.discharge.setEnabled(bool(enabled))
        row.browse.setEnabled(bool(enabled))

    def _browse_discharge(self, row: _AssignmentRow) -> None:
        path, _ = open_file(
            self,
            "Select discharge data",
            "Discharge data (*.csv *.txt);;All files (*)",
        )
        if not path:
            return
        layer = QgsVectorLayer(path, Path(path).name, "ogr")
        if not layer.isValid():
            QMessageBox.warning(
                self,
                "Invalid discharge table",
                f"Could not read the selected table:\n{path}",
            )
            return
        if not self._select_layer(row.discharge, path):
            row.discharge.addItem(layer.name(), layer)
            row.discharge.setCurrentIndex(row.discharge.count() - 1)

    def selected_assignments(self) -> list[OutletAssignment]:
        """Return one typed assignment for every displayed outlet."""
        return [
            OutletAssignment(
                outlet_id=row.outlet_id,
                is_gauge=row.gauge.isChecked(),
                discharge_layer=(
                    row.discharge.currentData() if row.gauge.isChecked() else None
                ),
            )
            for row in self._rows
        ]


__all__ = [
    "DischargeTableAssignmentDialog",
    "OutletAssignment",
]
