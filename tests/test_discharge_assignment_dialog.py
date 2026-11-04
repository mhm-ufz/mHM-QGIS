"""The discharge table: Add, station-ID matching, re-browse and the returned gauges."""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")



# isort: off
pytest.importorskip("qgis.core", reason="QGIS runtime libraries are required")

from qgis.PyQt.QtWidgets import QApplication

from mhm_qgis.qt.dialogs.discharge_assignment import (
    DischargeTableAssignmentDialog,
    OutletAssignment,
)
# isort: on


_APPLICATION = None


def _app():
    global _APPLICATION
    _APPLICATION = QApplication.instance() or _APPLICATION or QApplication([])
    return _APPLICATION


def _table(path):
    path.write_text("date,discharge\n2001-01-01,1.5\n2001-01-03,2.5\n", encoding="utf-8")
    return str(path)


def test_add_with_station_id_matching_fills_the_table_and_the_gauges(tmp_path, monkeypatch):
    from mhm_qgis.qt.dialogs import discharge_assignment

    _app()
    first = _table(tmp_path / "chosen.csv")
    _table(tmp_path / "2.txt")  # found by its station ID
    dialog = DischargeTableAssignmentDialog(["001", "2", "3"])
    monkeypatch.setattr(discharge_assignment, "open_file", lambda *_args: (first, ""))
    dialog.pushButton_browseDischargeTable.click()
    dialog.checkBox_bulkDischargeInputbyStationID.setChecked(True)
    dialog.pushButton_addDischargeInput.click()

    table = dialog.tableWidget_dischargeInput
    assert [table.item(row, 0).text() for row in range(table.rowCount())] == ["001", "2"]
    assert table.item(1, 1).text().endswith("2.txt")
    assert (table.item(0, 3).text(), table.item(0, 4).text()) == ("2001-01-01 00:00:00", "2001-01-03 00:00:00")
    assert [(a.outlet_id, a.is_gauge) for a in dialog.selected_assignments()] == [
        ("001", True), ("2", True), ("3", False)]
    assert isinstance(dialog.selected_assignments()[0], OutletAssignment)

    other = _table(tmp_path / "other.csv")
    monkeypatch.setattr(discharge_assignment, "open_file", lambda *_args: (other, ""))
    table.cellWidget(0, 2).click()  # re-browse the first gauge's table
    assert dialog.selected_assignments()[0].discharge_source.endswith("other.csv")
    dialog.deleteLater()


def test_saved_tables_fill_the_table_and_add_needs_a_table(tmp_path, monkeypatch):
    from mhm_qgis.qt.dialogs import discharge_assignment

    _app()
    saved = _table(tmp_path / "3.csv")
    dialog = DischargeTableAssignmentDialog(
        ["1", "3"], initial_records={"3": {"is_gauged": True, "discharge_file": saved}, "1": {"is_gauged": False}})
    assert dialog.tableWidget_dischargeInput.rowCount() == 1
    warned = []
    monkeypatch.setattr(discharge_assignment.QMessageBox, "warning", lambda *args: warned.append(args[2]))
    dialog.pushButton_addDischargeInput.click()  # no table chosen
    assert warned
    assert dialog.tableWidget_dischargeInput.rowCount() == 1
    dialog.deleteLater()
