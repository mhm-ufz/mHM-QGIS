"""The categorical input-type handlers write back to the main dialog.

A type's radio embeds its child dialog in the page; OK applies it. These tests stub
the child so the bookkeeping runs against a real `MhmQgisDialog`, which is where a
child dialog shadowing the parent shows up as an `AttributeError`.
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")



# isort: off
pytest.importorskip("qgis.core", reason="QGIS runtime libraries are required")

from qgis.PyQt.QtWidgets import QApplication, QMessageBox

from mhm_qgis.qt.controllers import categorical
from mhm_qgis.qt.dialogs.mhm_qgis_main import MhmQgisDialog
from mhm_qgis.core.handlers.lookup import SoilHorizon, SoilInput
from mhm_qgis.qt.dialogs.input_selection import _populate_file_combo, _populate_layer_combo
# isort: on

_APPLICATION = None


def _app():
    global _APPLICATION
    _APPLICATION = QApplication.instance() or _APPLICATION or QApplication([])
    return _APPLICATION


def _dialog(tmp_path):
    dialog = MhmQgisDialog()
    dialog.project_folder = str(tmp_path)
    return dialog


def _lookup_inputs(dialog, kind, tmp_path):
    """A categorical layer file and a lookup table with `source` and `class` columns on `kind`'s page."""
    layer, table = tmp_path / f"{kind}.tif", tmp_path / f"{kind}.csv"
    layer.touch()
    table.write_text("source,class\n1,1\n", encoding="utf-8")
    _populate_layer_combo(dialog._lookup_adapters[kind], str(tmp_path), kind, str(layer))
    _, _, combo, _, mapping, class_field = categorical._widgets(dialog, categorical._LOOKUP_WIDGETS[kind])
    _populate_file_combo(combo, str(tmp_path), "lookup", str(table))
    mapping.setCurrentText("source")
    class_field.setCurrentText("class")
    return layer, table


def test_a_radio_shows_its_page_and_set_commits_the_lookup(tmp_path):
    _app()
    dialog = _dialog(tmp_path)
    layer, table = _lookup_inputs(dialog, "geology", tmp_path)
    dialog.radioButton_geologyLookup.click()
    assert dialog.stackedWidget_geology.currentWidget() is dialog.page_categoricalGeologyInput
    assert categorical.categorical_input_mode(dialog, "geology") == ""  # nothing before Set
    dialog.pushButton_setGeology.click()
    assert dialog.categorical_lookup_config("geology") == {
        "input_path": str(layer.resolve()), "lookup_table": str(table.resolve()),
        "mapping_field": "source", "class_field": "class", "variable": "", "source_crs": ""}
    assert categorical.categorical_input_mode(dialog, "geology") == "Single Categorical Layer (with Lookup Table)"
    dialog.close()


def test_one_land_use_period_is_a_lookup_and_several_are_historical(tmp_path, monkeypatch):
    _app()
    dialog = _dialog(tmp_path)
    layer, _ = _lookup_inputs(dialog, "lc", tmp_path)
    dialog.radioButton_landUseCategorical.click()
    dialog.pushButton_setLandUse.click()
    assert dialog.categorical_lookup_config("lc")["input_path"] == str(layer.resolve())
    assert "land_cover" not in dialog._advanced_inputs

    dialog.checkBox_multiPeriodCategoricalLandUseInput.setChecked(True)
    dialog.pushButton_categoricalLandUseInputAdd.click()
    dialog.pushButton_categoricalLandUseInputAdd.click()
    table = dialog.tableWidget_categoricalLandUseInput
    assert table.rowCount() == 2
    table.cellWidget(1, 3).click()  # ✖
    assert table.rowCount() == 1
    value = object()
    monkeypatch.setattr(categorical, "_land_use_periods", lambda _dialog: value)
    dialog.pushButton_setLandUse.click()
    assert dialog._advanced_inputs["land_cover"] is value
    assert categorical.uses_advanced_categorical_input(dialog, "lc")
    dialog.close()


def test_multi_horizon_soil_and_the_layer_tables(tmp_path, monkeypatch):
    _app()
    dialog = _dialog(tmp_path)
    layer = tmp_path / "horizon.tif"
    layer.touch()
    value = SoilInput((SoilHorizon(1, 0.0, 300.0, layer, layer, layer, layer),), "g/cm3")
    monkeypatch.setattr(dialog._soil_horizons, "selected_input", lambda: value)
    dialog.radioButton_soilMultiHorizon.click()
    dialog.pushButton_setSoil.click()
    assert dialog._advanced_inputs["soil"] is value

    warned, started = [], []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warned.append(args[2]))
    monkeypatch.setattr(categorical, "require_common_grid", lambda _dialog: None)
    monkeypatch.setattr(dialog.morphology_tasks, "start_ready_input", lambda kind, failed: started.append(kind))
    dialog.radioButton_soilReady.click()
    assert not dialog.lineEdit_upperDepth.isEnabled()
    dialog.checkBox_multiClassLayerSoil.setChecked(True)
    assert dialog.lineEdit_upperDepth.isEnabled()
    _populate_file_combo(dialog.comboBox_demInput_mhmReadySoilInput, str(tmp_path), "soil", str(layer))
    definition = tmp_path / "soil_classdefinition.txt"
    definition.touch()
    _populate_file_combo(dialog.comboBox_soilClassDefinitionInputFile, str(tmp_path), "lookup", str(definition))
    for upper, lower in (("0", "300"), ("300", "1000")):
        dialog.lineEdit_upperDepth.setText(upper)
        dialog.lineEdit_lowerDepth.setText(lower)
        dialog.pushButton_mhmReadySoilInputAdd.click()
    dialog.tableWidget_mhmReadySoilInput.cellWidget(1, 3).click()  # ✖
    dialog.pushButton_setSoil.click()
    assert "at least two rows" in warned[-1]
    dialog.pushButton_mhmReadySoilInputAdd.click()
    dialog.pushButton_setSoil.click()
    assert dialog._categorical_ready_configs["soil"]["layers"] == [
        [str(layer.resolve()), 0.0, 300.0], [str(layer.resolve()), 300.0, 1000.0]]
    assert started == ["soil"]
    dialog.close()


def test_saved_types_of_older_versions_restore_to_the_new_radios():
    assert categorical.saved_mode("lc", "Multi Historical Periods (with Lookup Table)") == categorical._PERIODS
    assert categorical.saved_mode("lc", "single_categorical_layer_(with_lookup_table)") == categorical._PERIODS
    assert categorical.saved_mode("lai", "mHM ready") == "mHM ready (Class Layer)"
    assert categorical.saved_mode("geology", "mhm ready") == "mHM ready"
