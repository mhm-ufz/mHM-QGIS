# -*- coding: utf-8 -*-
"""Per-outlet gauge and domain assignment dialogs."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from qgis.PyQt.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QLabel,
    QMessageBox,
    QPushButton,
)
from qgis.core import QgsMapLayer, QgsProject, QgsVectorLayer

from ...qt.controllers import discharge_assignment as discharge_controller
from ...core.morphology.hydrology.outlets import OutletAssignment
from ..ui import load_form

Ui_DischargeTableAssignmentDialog = load_form("discharge_table_assignment_dialog.ui")


@dataclass
class _AssignmentRow:
    outlet_id: str
    id_value: QLabel
    gauge: QCheckBox
    discharge: QComboBox
    browse: QPushButton


class _OutletAssignmentRows:
    """Populate a Designer row template once per outlet."""

    default_is_gauge = False

    def _setup_assignment_rows(
        self,
        outlet_ids: list[str],
        initial_records: Mapping[str, Mapping] | None = None,
    ) -> None:
        self._rows: list[_AssignmentRow] = []
        self._initial_records = initial_records or {}
        self._remove_layout_spacers()

        for row_index, outlet_id in enumerate(outlet_ids):
            row = (
                self._designer_row(str(outlet_id))
                if row_index == 0
                else self._create_row(row_index, str(outlet_id))
            )
            self._configure_row(row)
            self._rows.append(row)

        if not outlet_ids:
            for widget in self._designer_row_widgets():
                widget.hide()

    def _remove_layout_spacers(self) -> None:
        for index in range(self.rows_layout.count() - 1, -1, -1):
            item = self.rows_layout.itemAt(index)
            if item is not None and item.spacerItem() is not None:
                self.rows_layout.takeAt(index)

    def _designer_row(self, outlet_id: str) -> _AssignmentRow:
        return _AssignmentRow(
            outlet_id=outlet_id,
            id_value=self.label_pourPointIDValue,
            gauge=self.checkBox_isGauge,
            discharge=self.comboBox_dischargeTableInput,
            browse=self.pushButton_browseDischargeTable1,
        )

    def _designer_row_widgets(self) -> tuple[object, ...]:
        return (
            self.label_pourPointID,
            self.label_pourPointIDValue,
            self.checkBox_isGauge,
            self.comboBox_dischargeTableInput,
            self.pushButton_browseDischargeTable1,
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

        self.rows_layout.addWidget(label, row_index, 0)
        self.rows_layout.addWidget(id_value, row_index, 1)
        self.rows_layout.addWidget(gauge, row_index, 2)
        self.rows_layout.addWidget(discharge, row_index, 3)
        self.rows_layout.addWidget(browse, row_index, 4)
        return _AssignmentRow(
            outlet_id=outlet_id,
            id_value=id_value,
            gauge=gauge,
            discharge=discharge,
            browse=browse,
        )

    def _configure_row(self, *args, **kwargs):
        return discharge_controller._configure_row(self, *args, **kwargs)

    def _populate_discharge_layers(self, *args, **kwargs):
        return discharge_controller._populate_discharge_layers(self, *args, **kwargs)

    def _select_discharge_source(self, *args, **kwargs):
        return discharge_controller._select_discharge_source(self, *args, **kwargs)

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

    @staticmethod
    def _set_discharge_enabled(*args, **kwargs):
        return discharge_controller._set_discharge_enabled(*args, **kwargs)

    def _browse_discharge(self, *args, **kwargs):
        return discharge_controller._browse_discharge(self, *args, **kwargs)

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

    def selected_layers(self) -> dict[str, object | None]:
        """Backward-compatible selected-layer mapping keyed by outlet ID."""
        return {
            record.outlet_id: record.discharge_layer
            for record in self.selected_assignments()
        }


class DischargeTableAssignmentDialog(
    QDialog,
    Ui_DischargeTableAssignmentDialog,
    _OutletAssignmentRows,
):
    """Assign optional discharge tables to all supplied outlets."""

    default_is_gauge = True

    def __init__(
        self,
        station_ids: list[str],
        parent=None,
        initial_records: Mapping[str, Mapping] | None = None,
    ):
        super().__init__(parent)
        self.setupUi(self)
        self._setup_assignment_rows(station_ids, initial_records)


__all__ = [
    "DischargeTableAssignmentDialog",
    "OutletAssignment",
]
