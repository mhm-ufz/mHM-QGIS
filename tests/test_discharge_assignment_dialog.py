"""Focused tests for Designer-backed per-outlet assignment rows."""
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


class _Layer:
    def __init__(self, source="gauge.csv"):
        self._source = source

    def source(self):
        return self._source


def test_basic_dialog_reuses_designer_row_and_adds_only_remaining_rows():
    _app()
    dialog = DischargeTableAssignmentDialog(["001", "2", "3"])

    assert len(dialog._rows) == 3
    assert dialog._rows[0].id_value is dialog.label_pourPointIDValue
    assert dialog._rows[0].gauge is dialog.checkBox_isGauge
    assert dialog._rows[0].id_value.text() == "001"
    assert dialog._rows[1].id_value.objectName() == "label_pourPointIDValue2"
    assert all(row.gauge.isChecked() for row in dialog._rows)
    assert all(row.discharge.isEnabled() for row in dialog._rows)
    dialog.close()


def test_dialog_returns_typed_records_for_every_outlet():
    _app()
    dialog = DischargeTableAssignmentDialog(
        ["001", "2"],
        initial_records={"2": {"is_gauged": False}},
    )
    layer = _Layer()
    first = dialog._rows[0]
    first.discharge.addItem("gauge.csv", layer)
    first.discharge.setCurrentIndex(first.discharge.count() - 1)

    records = dialog.selected_assignments()

    assert records == [
        OutletAssignment("001", True, False, layer),
        OutletAssignment("2", False, False, None),
    ]
    dialog.close()


def test_gauge_checkbox_controls_only_its_discharge_inputs():
    _app()
    dialog = DischargeTableAssignmentDialog(["1", "2"])
    first, second = dialog._rows

    second.gauge.setChecked(False)

    assert first.discharge.isEnabled()
    assert first.browse.isEnabled()
    assert not second.discharge.isEnabled()
    assert not second.browse.isEnabled()
    dialog.close()
