"""The Designer form owns its pages directly inside the step tabs."""
import pytest

pytest.importorskip("qgis.core", reason="QGIS runtime libraries are required")

from mhm_qgis.core.handlers.store.layout import METEO_VARIABLES
from mhm_qgis.qt import project_tree
from mhm_qgis.qt.dialogs.mhm_qgis_main import MhmQgisDialog


def test_each_step_owns_its_controls():
    dialog = MhmQgisDialog()
    for tab, button in (
        (dialog.tab_project, dialog.lineEdit_ProjectFolder),
        (dialog.page_gauges, dialog.comboBox_pourPointInput),
        (dialog.page_meteo, dialog.tableWidget_meteoInputs),
        (dialog.page_hydro, dialog.pushButton_observationDisplay_discharge),
        (dialog.page_execution, dialog.pushButton_execute_mHM),
        (dialog.page_outputs, dialog.pushButton_postRefresh),
    ):
        dialog.stackedWidget_project.setCurrentWidget(tab)
        assert tab.isAncestorOf(button)
    assert not hasattr(dialog, "stackedWidget")
    dialog.close()


def test_work_type_switches_configuration_page():
    dialog = MhmQgisDialog()
    dialog.comboBox_mhmWorkType.setCurrentIndex(1)
    assert dialog.stackedWidget_configuration.currentWidget() is dialog.page_multiDomain
    assert project_tree.item(dialog, "inputs").isHidden()  # a combined setup prepares no inputs
    dialog.comboBox_mhmWorkType.setCurrentIndex(0)
    assert dialog.stackedWidget_configuration.currentWidget() is dialog.page_singleDomain
    assert not project_tree.item(dialog, "inputs").isHidden()
    dialog.close()


def test_resolution_fields_preserve_exact_positive_values_for_click_validation():
    dialog = MhmQgisDialog()
    exact = 1 / 1200
    dialog.lineEdit_L1Resolution.setText(repr(exact))
    assert dialog.current_l1_resolution() == exact
    dialog.lineEdit_L1Resolution.setText(repr(2 * exact))
    assert dialog.current_l1_resolution() == 2 * exact
    dialog.lineEdit_L1Resolution.setText("invalid")
    assert dialog.current_l1_resolution() is None
    dialog.lineEdit_L1Resolution.setText("0.5")
    assert dialog.current_l1_resolution() == 0.5
    assert not dialog.comboBox_L1ResolutionUnit.isEnabled()
    dialog.close()


def test_reopening_the_dem_refreshes_it(tmp_path):
    gdal = pytest.importorskip("osgeo.gdal")
    from mhm_qgis.qt.controllers import main

    dem = str(tmp_path / "dem.tif")
    gdal.GetDriverByName("GTiff").Create(dem, 2, 2, 1).FlushCache()
    dialog, seen = MhmQgisDialog(), []
    dialog._input_adapters["dem"].layerChanged.connect(seen.append)
    main.add_file_input(dialog, "dem", dem, manual=True)
    main.add_file_input(dialog, "dem", dem, manual=True, source_crs="EPSG:3035")
    assert len(seen) == 2
    dialog.close()


def test_the_multiplier_spin_boxes_hold_the_integer_multiple_of_l0():
    dialog = MhmQgisDialog()
    l0 = 1 / 1200
    dialog._grid_l0_info = {"resolution": l0}
    for level in ("L1", "L11", "L2"):
        field, spin = (getattr(dialog, f"lineEdit_{level}Resolution"),
                       getattr(dialog, f"spinBox_{level}ResolutionMultiplier"))
        spin.setValue(3)
        assert field.text() == repr(3 * l0)
        field.setText(repr(4.6 * l0))
        assert spin.value() == 5
        field.editingFinished.emit()
        assert field.text() == repr(5 * l0)
    dialog.close()


def test_meteo_combo_offers_every_prepared_forcing():
    # Precipitation has its own page; every other forcing is added here (netrad
    # was missing, which made PET cases 2 and 3 unpreparable).
    dialog = MhmQgisDialog()
    combo = dialog.comboBox_meteoVar
    assert {combo.itemText(i) for i in range(combo.count())} == set(METEO_VARIABLES) - {"pre"}
    dialog.close()


@pytest.mark.parametrize("key", ["lc_ready", "lc_lookup"])
def test_the_next_land_use_period_runs_from_the_latest_end_to_the_end_of_the_simulation_year(key):
    from qgis.PyQt.QtCore import QDateTime, Qt

    from mhm_qgis.qt.controllers import categorical

    dialog = MhmQgisDialog()
    day = lambda text: QDateTime.fromString(text, Qt.DateFormat.ISODate)  # noqa: E731
    dialog.dateTimeEdit_simulationStartTimeInput.setDateTime(day("1990-01-01T00:00:00"))
    dialog.dateTimeEdit_simulationEndTimeInput.setDateTime(day("2010-01-01T00:00:00"))
    _table, _combo, first, second, _add = categorical._TABLES[key]
    dates = lambda: (getattr(dialog, first).dateTime(), getattr(dialog, second).dateTime())  # noqa: E731
    assert dates() == (day("1990-01-01T00:00:00"), day("2010-12-31T00:00:00"))  # whole years

    dialog._layer_rows[key].append(["a.tif", "1990-01-01T00:00:00", "2005-01-01T00:00:00"])
    categorical._show_rows(dialog, key)
    assert dates() == (day("2005-01-01T00:00:00"), day("2010-12-31T00:00:00"))
    dialog.dateTimeEdit_simulationEndTimeInput.setDateTime(day("2012-01-01T00:00:00"))
    assert dates() == (day("2005-01-01T00:00:00"), day("2012-12-31T00:00:00"))

    end = getattr(dialog, _table).cellWidget(0, 2)  # the row's period is a date edit like From
    assert end.displayFormat() == "yyyy/MM/dd HH:mm"
    end.setDateTime(day("2007-01-01T00:00:00"))
    assert dialog._layer_rows[key][0][2] == "2007-01-01T00:00:00"
    assert dates()[0] == day("2007-01-01T00:00:00")

    dialog._layer_rows[key].clear()
    categorical._show_rows(dialog, key)
    assert dates()[0] == day("1990-01-01T00:00:00")
    assert getattr(dialog, first).displayFormat() == "yyyy/MM/dd HH:mm"


def test_layer_rows_are_checked_in_order_and_land_use_is_fitted_to_the_simulation(tmp_path, monkeypatch):
    from mhm_qgis.core.handlers.state.nml_settings import update_section
    from mhm_qgis.qt.controllers import categorical
    from mhm_qgis.qt.dialogs.input_selection import _populate_file_combo

    dialog = MhmQgisDialog()
    rows = dialog._layer_rows
    rows["soil_ready"] = [["b.tif", "300", "1000"], ["a.tif", "0", "300"]]
    assert [row[1] for row in categorical._rows(dialog, "soil_ready")] == [0.0, 300.0]  # in depth order
    for bad, message in ((["a.tif", "0", "200"], "gaps"), (["a.tif", "100", "300"], "0 mm"),
                         (["a.tif", "nan", "300"], "numbers")):
        rows["soil_ready"][1] = bad
        with pytest.raises(ValueError, match=message):
            categorical._rows(dialog, "soil_ready")

    dialog.setup_folder = str(tmp_path)
    for name in ("a.tif", "b.tif"):
        (tmp_path / name).touch()
    (tmp_path / "lut.csv").write_text("id,class\n1,2\n")
    _populate_file_combo(dialog.comboBox_lookupTableLandUse, dialog.setup_folder, "lookup", str(tmp_path / "lut.csv"))
    dialog.comboBox_mappingFieldLandUse.setCurrentText("id")
    dialog.comboBox_classFieldLandUse.setCurrentText("class")
    monkeypatch.setattr(categorical, "_crs", lambda _dialog, _key: "")
    rows["lc_lookup"] = [[str(tmp_path / "b.tif"), "1985-01-01T00:00:00", "1987-12-31T00:00:00"],
                         [str(tmp_path / "a.tif"), "1980-01-01T00:00:00", "1985-01-01T00:00:00"]]
    with pytest.raises(ValueError, match="Time Period"):
        categorical._land_use_periods(dialog)
    update_section(tmp_path, "time", {"sim_start": "1981-01-01T00:00", "eval_start": "1982-01-01T00:00",
                                      "sim_end": "1987-12-01T00:00"})
    value = categorical._land_use_periods(dialog)
    assert [(period.file_path.name, str(period.start_time), str(period.end_time)) for period in value.periods] == [
        ("a.tif", "1981-01-01 00:00:00", "1985-01-01 00:00:00"), ("b.tif", "1985-01-01 00:00:00", "1987-12-01 00:00:00")]
    dialog.close()
