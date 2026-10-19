"""Offscreen tests for dynamic land-use and soil input rows."""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")



pytest.importorskip("qgis.core", reason="QGIS runtime libraries are required")

from qgis.PyQt import QtWidgets
from qgis.PyQt.QtCore import QDateTime

from mhm_qgis.qt.controllers import land_use_historical as land_use_controller
from mhm_qgis.qt.controllers import soil_multi_horizon as soil_controller
from mhm_qgis.qt.dialogs.advanced_inputs import (
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
    from mhm_qgis.core.handlers.state.nml_settings import update_section

    _app()
    update_section(tmp_path, "time", {
        "sim_start": "1990-01-01T00:00",
        "eval_start": "2000-01-01T00:00",
        "sim_end": "2011-01-01T00:00",
    })
    first = _touch(tmp_path / "first.tif")
    second = _touch(tmp_path / "second.tif")
    lookup = _touch(tmp_path / "lookup.csv", "source,class\n1,2\n")
    dialog = HistoricalLandUseDialog(tmp_path)

    assert dialog.spinBox_nLandUseLayers.maximum() == 50
    land_use_controller.set_layer_count(dialog, 2)
    table = dialog.tableWidget_landUseTimeInputs
    table.cellWidget(0, 0).setDateTime(QDateTime.fromString("1990-01-01", "yyyy-MM-dd"))
    table.cellWidget(0, 1).setDateTime(QDateTime.fromString("2000-01-01", "yyyy-MM-dd"))
    table.cellWidget(1, 1).setDateTime(QDateTime.fromString("2011-01-01", "yyyy-MM-dd"))
    assert table.cellWidget(1, 0).dateTime() == table.cellWidget(0, 1).dateTime()
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
    assert dialog._rows[1].start_label.text() == "2000-01-01 00:00"
    assert value.lookup_table == lookup


def test_land_use_lookup_browse_accepts_qt_checked_argument(tmp_path, monkeypatch):
    from mhm_qgis.qt.controllers import land_use_historical

    monkeypatch.setattr(land_use_historical, "open_file", lambda *args: ("", ""))
    land_use_controller._browse_lookup(HistoricalLandUseDialog(tmp_path), False)


def test_soil_dialog_adds_all_four_rows_and_extracts_unit(tmp_path):
    from mhm_qgis.qt.controllers.categorical import soil_input_value

    _app()
    paths = [_touch(tmp_path / f"layer_{index}.tif") for index in range(8)]
    dialog = MultiHorizonSoilDialog(tmp_path)
    assert dialog.spinBox_nSoilHorizons.maximum() == 10
    assert dialog.doubleSpinBox_compositionStepValue.value() == 5.0
    assert dialog.doubleSpinBox_bulkDensityStepValue.value() == 0.1
    dialog.doubleSpinBox_compositionStepValue.setValue(2.5)
    dialog.doubleSpinBox_bulkDensityStepValue.setValue(0.05)
    soil_controller.set_horizon_count(dialog, 2)
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
    assert value.composition_step == 2.5
    assert value.bulk_density_step == 0.05
    assert [(item.upper_depth, item.lower_depth) for item in value.horizons] == [
        (0, 50),
        (50, 200),
    ]
    saved = value.as_dict()
    assert soil_input_value(saved) == value
    restored = MultiHorizonSoilDialog(tmp_path, initial=saved)
    assert restored.selected_input() == value
    del saved["composition_step"], saved["bulk_density_step"]
    soil_controller.set_input(restored, saved)
    assert restored.selected_input() == soil_input_value(saved)
    assert restored.doubleSpinBox_compositionStepValue.value() == 5.0
    assert restored.doubleSpinBox_bulkDensityStepValue.value() == 0.1
    dialog.close()
    restored.close()


def test_land_use_datetime_widgets_share_boundaries_and_clamp_to_simulation(tmp_path):
    from mhm_qgis.core.handlers.state.nml_settings import update_section
    update_section(tmp_path, "time", {"sim_start": "2001-01-01T06:00", "sim_end": "2001-12-31T23:00"})
    dialog = HistoricalLandUseDialog(tmp_path)
    dialog.spinBox_nLandUseLayers.setValue(3)
    dialog.pushButton_addLandUseInputWidgets.click()
    table = dialog.tableWidget_landUseTimeInputs
    assert table.rowCount() == 3
    boundary = QDateTime.fromString("2001-05-03T07:00:00", "yyyy-MM-ddTHH:mm:ss")
    table.cellWidget(1, 0).setDateTime(boundary)
    assert table.cellWidget(0, 1).dateTime() == boundary
    table.cellWidget(2, 1).setDateTime(QDateTime.fromString("2002-01-01", "yyyy-MM-dd"))
    assert table.cellWidget(2, 1).dateTime().toString("yyyy-MM-ddTHH:mm") == "2001-12-31T23:00"
    dialog.close()


def test_land_use_periods_outside_the_simulation_open_clamped(tmp_path):
    from mhm_qgis.core.handlers.state.nml_settings import update_section
    _app()
    update_section(tmp_path, "time", {"sim_start": "1980-01-01T00:00", "sim_end": "2020-01-01T00:00"})
    layer = _touch(tmp_path / "lu.tif")
    periods = [{"start_time": "1980-01-01T00:00:00", "end_time": "2010-12-30T00:00:00", "file_path": str(layer)},
               {"start_time": "2010-12-30T00:00:00", "end_time": "2025-12-30T00:00:00", "file_path": str(layer)}]
    dialog = HistoricalLandUseDialog(tmp_path, initial={"periods": periods})
    table = dialog.tableWidget_landUseTimeInputs
    assert table.cellWidget(1, 0).dateTime().toString("yyyy-MM-dd") == "2010-12-30"
    assert table.cellWidget(1, 1).dateTime().toString("yyyy-MM-dd") == "2020-01-01"
    assert "period 2: 2010-12-30 to 2025-12-30" in dialog.label_landUseDescription.text()
    dialog.close()
