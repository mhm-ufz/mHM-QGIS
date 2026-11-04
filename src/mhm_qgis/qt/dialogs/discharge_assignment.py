"""Gauge discharge-table assignment for the DEM-extent domain, as one table."""
from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from qgis.core import QgsMapLayer, QgsProject, QgsVectorLayer
from qgis.PyQt.QtWidgets import QDialog, QMessageBox, QPushButton, QTableWidgetItem

from ...core.handlers.file.discharge import records_from_layer
from ...core.morphology.hydrology.outlets import OutletAssignment
from ..ui import load_form
from .file_picker import open_file

Ui_DischargeTableAssignmentDialog = load_form("discharge_table_assignment_dialog.ui")


class DischargeTableAssignmentDialog(QDialog, Ui_DischargeTableAssignmentDialog):
    """Assign discharge tables to gauges, one table row per gauge.

    A station in the table is a gauge with that table; one not in it is not a gauge.
    """

    def __init__(
        self,
        station_ids: list[str],
        parent=None,
        initial_records: Mapping[str, Mapping] | None = None,
    ):
        super().__init__(parent)
        self.setupUi(self)
        self._station_ids = [str(station) for station in station_ids]
        self._tables: dict[str, object] = {}  # station ID -> discharge layer
        self._browsed = []  # combo item data does not keep a layer alive
        self.comboBox_selectGaugeID.addItems(self._station_ids)
        combo = self.comboBox_dischargeTableInput
        combo.addItem("", None)
        for layer in QgsProject.instance().mapLayers().values():
            if layer.isValid() and layer.type() == QgsMapLayer.LayerType.VectorLayer:
                combo.addItem(layer.name(), layer)
        for station, record in (initial_records or {}).items():
            layer = _table(str(record.get("discharge_file", "") or ""))
            if record.get("is_gauged", True) and layer is not None and str(station) in self._station_ids:
                self._tables[str(station)] = layer
        # pushButton_browseDischargeTable → _browse; pushButton_addDischargeInput → add
        self.pushButton_browseDischargeTable.clicked.connect(self._browse)
        self.pushButton_addDischargeInput.clicked.connect(self.add)
        self._show_tables()

    def _browse(self, _checked=False) -> None:
        layer = self._ask_table()
        if layer is not None:
            self._browsed.append(layer)
            self.comboBox_dischargeTableInput.addItem(layer.name(), layer)
            self.comboBox_dischargeTableInput.setCurrentIndex(self.comboBox_dischargeTableInput.count() - 1)

    def _ask_table(self):
        path, _ = open_file(self, "Select discharge data", "Discharge data (*.csv *.txt);;All files (*)")
        if not path:
            return None
        layer = _table(path)
        if layer is None:
            QMessageBox.warning(self, "Invalid discharge table", f"Could not read the selected table:\n{path}")
        return layer

    def add(self) -> None:
        """The selected table for the selected gauge; with station-ID matching, its folder's
        `<station id>.csv`/`.txt` for every gauge not in the table yet."""
        station = self.comboBox_selectGaugeID.currentText()
        layer = self.comboBox_dischargeTableInput.currentData()
        if not station or layer is None:
            QMessageBox.warning(self, "Discharge table", "Select a gauge and a discharge table first.")
            return
        self._tables[station] = layer
        folder = Path(str(layer.source()).split("|", 1)[0]).parent
        if self.checkBox_bulkDischargeInputbyStationID.isChecked() and folder.is_dir():
            for other in self._station_ids:
                found = next((path for path in (folder / f"{other}.csv", folder / f"{other}.txt") if path.is_file()), None)
                if other not in self._tables and found is not None and (match := _table(found)) is not None:
                    self._tables[other] = match
        self._show_tables()

    def _rebrowse(self, station: str) -> None:
        layer = self._ask_table()
        if layer is not None:
            self._tables[station] = layer
            self._show_tables()

    def _show_tables(self) -> None:
        table = self.tableWidget_dischargeInput
        rows = [station for station in self._station_ids if station in self._tables]
        table.setRowCount(len(rows))
        for index, station in enumerate(rows):
            layer = self._tables[station]
            try:
                records = records_from_layer(layer)
            except (OSError, ValueError):
                records = []
            period = (records[0].timestamp, records[-1].timestamp) if records else ("", "")
            for column, text in ((0, station), (1, str(layer.source())), (3, str(period[0])), (4, str(period[1]))):
                table.setItem(index, column, QTableWidgetItem(text))
            browse = QPushButton("...", table)
            browse.clicked.connect(lambda _checked=False, station=station: self._rebrowse(station))
            table.setCellWidget(index, 2, browse)

    def selected_assignments(self) -> list[OutletAssignment]:
        """Return one typed assignment for every outlet: a gauge exactly when it has a table."""
        return [
            OutletAssignment(outlet_id=station, is_gauge=station in self._tables,
                             discharge_layer=self._tables.get(station))
            for station in self._station_ids
        ]


def _table(path):
    """A discharge table layer at `path` that yields records, or None. Its file is what is read,
    so a .txt table that OGR cannot open still counts."""
    if not path or not os.path.isfile(str(path)):
        return None
    layer = QgsVectorLayer(str(path), Path(path).name, "ogr")
    return layer if layer.isValid() or records_from_layer(layer) else None


__all__ = [
    "DischargeTableAssignmentDialog",
    "OutletAssignment",
]
