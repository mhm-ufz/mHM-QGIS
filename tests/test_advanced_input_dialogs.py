"""Offscreen tests for dynamic land-use and soil input rows."""
from __future__ import annotations

import pytest

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")



pytest.importorskip("qgis.core", reason="QGIS runtime libraries are required")

from qgis.PyQt import QtWidgets  # noqa: E402

from mhm_qgis.qt.dialogs.advanced_inputs import (  # noqa: E402
    HistoricalLandUseDialog,
    MultiHorizonSoilDialog,
)


_APPLICATION = None


def _app():
    global _APPLICATION
    _APPLICATION = (
        QtWidgets.QApplication.instance()
        or _APPLICATION
        or QtWidgets.QApplication([])
    )
    return _APPLICATION


def _touch(path, text="raster"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_land_use_dialog_reuses_first_row_and_extracts_periods(tmp_path):
    _app()
    first = _touch(tmp_path / "first.tif")
    second = _touch(tmp_path / "second.tif")
    lookup = _touch(tmp_path / "lookup.csv", "source,class\n1,2\n")
    dialog = HistoricalLandUseDialog(tmp_path)

    assert dialog.spinBox_nLandUseLayers.maximum() == 50
    dialog.set_layer_count(2)
    dialog._set_cell(dialog.tableWidget_landUseTimeInputs, 0, 0, 1990)
    dialog._set_cell(dialog.tableWidget_landUseTimeInputs, 0, 1, 1999)
    dialog._set_cell(dialog.tableWidget_landUseTimeInputs, 1, 0, 2000)
    dialog._set_cell(dialog.tableWidget_landUseTimeInputs, 1, 1, 2010)
    dialog._select_path(dialog._rows[0].adapter, first)
    dialog._select_path(dialog._rows[1].adapter, second)
    dialog._select_combo_path(dialog.comboBox_lookupTableInput, lookup)
    dialog.comboBox_mappingFieldInput.setCurrentText("source")
    dialog.comboBox_classFieldInput.setCurrentText("class")

    value = dialog.selected_input()

    assert len(dialog._rows) == dialog.tableWidget_landUseTimeInputs.rowCount() == 2
    assert [(item.start_year, item.end_year) for item in value.periods] == [
        (1990, 1999),
        (2000, 2010),
    ]
    assert dialog._rows[1].start_label.text() == "2000"
    assert value.lookup_table == lookup


def test_soil_dialog_adds_all_four_rows_and_extracts_unit(tmp_path):
    _app()
    paths = [_touch(tmp_path / f"layer_{index}.tif") for index in range(8)]
    dialog = MultiHorizonSoilDialog(tmp_path)
    assert dialog.spinBox_nSoilHorizons.maximum() == 10
    dialog.set_horizon_count(2)
    dialog.comboBox_bulkDensityUnit.setCurrentText("cg/cm3")
    for row, bounds in enumerate(((0, 50), (50, 200))):
        dialog._set_cell(dialog.tableWidget_soilHorizonDepthInputs, row, 0, bounds[0])
        dialog._set_cell(dialog.tableWidget_soilHorizonDepthInputs, row, 1, bounds[1])
        for column, component in enumerate(
            ("clay", "sand", "silt", "bulk_density")
        ):
            dialog._select_path(
                dialog._soil_rows[component][row].adapter,
                paths[row * 4 + column],
            )

    value = dialog.selected_input()

    assert all(len(rows) == 2 for rows in dialog._soil_rows.values())
    assert value.bulk_density_unit == "cg/cm3"
    assert [(item.upper_depth, item.lower_depth) for item in value.horizons] == [
        (0, 50),
        (50, 200),
    ]
