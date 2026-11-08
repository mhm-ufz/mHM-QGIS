"""The Designer form owns its pages directly inside the step tabs."""
import pytest

pytest.importorskip("qgis.core", reason="QGIS runtime libraries are required")

from mhm_qgis.core.handlers.store.layout import METEO_VARIABLES
from mhm_qgis.qt import project_tree
from mhm_qgis.qt.controllers import grid
from mhm_qgis.qt.dialogs.mhm_qgis_main import MhmQgisDialog


def test_each_step_owns_its_controls():
    dialog = MhmQgisDialog()
    for tab, button in (
        (dialog.tab_project, dialog.lineEdit_ProjectFolder),
        (dialog.page_gauges, dialog.comboBox_pourPointInput),
        (dialog.tab_meteo, dialog.tableWidget_meteoInputs),
        (dialog.tab_hydro, dialog.pushButton_observationDisplay_discharge),
        (dialog.tab_execution, dialog.pushButton_execute_mHM),
        (dialog.tab_outputs, dialog.pushButton_postRefresh),
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
    grid._populate_resolution_input(dialog, "L1", [exact, 2 * exact], unit="°")
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


def test_a_new_crs_resets_l1_l11_and_l2():
    from qgis.core import QgsCoordinateReferenceSystem

    dialog = MhmQgisDialog()
    dialog.lineEdit_L1Resolution.setText("1000")
    dialog.lineEdit_L11Resolution.setText("2000")
    dialog.spinBox_L2ResolutionMultiplier.setValue(4)
    dialog._grid_l2_header = {"cellsize": 4000}
    dialog.mProjectionSelectionWidget_crs.setCrs(QgsCoordinateReferenceSystem("EPSG:3035"))
    assert dialog.lineEdit_L1Resolution.text() == "1000"  # nothing applies before Set
    dialog.pushButton_setCRS.click()
    assert (dialog.lineEdit_L1Resolution.text(), dialog.lineEdit_L11Resolution.text()) == ("", "")
    assert dialog.spinBox_L2ResolutionMultiplier.value() == 1
    assert dialog._grid_l2_header is None
    dialog.close()


def test_l1_and_l11_spin_boxes_hold_the_integer_multiple_of_l0():
    dialog = MhmQgisDialog()
    l0 = 1 / 1200
    dialog._grid_l0_info = {"resolution": l0}
    for level in ("L1", "L11"):
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
    # Precipitation has its own scan; every other forcing is added here (netrad
    # was missing, which made PET cases 2 and 3 unpreparable).
    dialog = MhmQgisDialog()
    combo = dialog.comboBox_meteoVar
    assert {combo.itemText(i) for i in range(combo.count())} == set(METEO_VARIABLES) - {"pre"}
    dialog.close()


def test_set_dem_keeps_a_custom_extent_inside_the_dem_and_a_reload_restores_it(tmp_path, monkeypatch):
    from qgis.core import QgsCoordinateReferenceSystem, QgsRectangle

    dialog = MhmQgisDialog()
    dialog.project_folder = str(tmp_path)
    dialog.mProjectionSelectionWidget_crs.setCrs(QgsCoordinateReferenceSystem("EPSG:32632"))
    dialog._set_crs = "EPSG:32632"
    header = {"ncols": 10, "nrows": 10, "xllcorner": 0.0, "yllcorner": 0.0, "cellsize": 100.0}
    dialog._grid_l0_info = {"header": header, "exact_x_resolution": 100.0}
    warnings = []
    monkeypatch.setattr(grid.QMessageBox, "warning", lambda *args: warnings.append(args[2]))
    box = dialog.mExtentGroupBox_customExtent
    dialog.groupBox_customExtent.setChecked(True)
    box.setOutputExtentFromUser(QgsRectangle(-100, 0, 500, 500), dialog.get_crs())
    dialog.pushButton_setDEM.click()
    assert warnings == ["The custom L0 extent must be a non-empty box inside the DEM."]
    assert dialog._study_extent is None

    box.setOutputExtentFromUser(QgsRectangle(200, 300, 600, 700), dialog.get_crs())
    dialog.pushButton_setDEM.click()
    assert grid.study_bounds(dialog) == (200, 600, 300, 700)
    dialog.close()

    restored = MhmQgisDialog()
    restored.project_folder = str(tmp_path)
    restored.load_input_state()
    assert restored._study_extent == [200, 600, 300, 700]
    assert restored.groupBox_customExtent.isChecked()
    restored._grid_l0_info = dialog._grid_l0_info
    restored.groupBox_customExtent.setChecked(False)
    restored.pushButton_setDEM.click()
    assert grid.study_bounds(restored) == (0, 1000, 0, 1000)
    restored.close()


@pytest.mark.parametrize("key", ["lc_ready", "lc_lookup"])
def test_the_next_land_use_period_runs_from_the_latest_end_to_the_simulation_end(key):
    from qgis.PyQt.QtCore import QDateTime, Qt

    from mhm_qgis.qt.controllers import categorical

    dialog = MhmQgisDialog()
    day = lambda text: QDateTime.fromString(text, Qt.DateFormat.ISODate)  # noqa: E731
    dialog.dateTimeEdit_simulationStartTimeInput.setDateTime(day("1990-01-01T00:00:00"))
    dialog.dateTimeEdit_simulationEndTimeInput.setDateTime(day("2010-01-01T00:00:00"))
    _table, _combo, first, second, _add = categorical._TABLES[key]
    dates = lambda: (getattr(dialog, first).dateTime(), getattr(dialog, second).dateTime())  # noqa: E731
    assert dates() == (day("1990-01-01T00:00:00"), day("2010-01-01T00:00:00"))

    dialog._layer_rows[key].append(["a.tif", "1990-01-01T00:00:00", "2005-01-01T00:00:00"])
    categorical._show_rows(dialog, key)
    assert dates() == (day("2005-01-01T00:00:00"), day("2010-01-01T00:00:00"))
    dialog.dateTimeEdit_simulationEndTimeInput.setDateTime(day("2012-01-01T00:00:00"))
    assert dates() == (day("2005-01-01T00:00:00"), day("2012-01-01T00:00:00"))

    end = getattr(dialog, _table).cellWidget(0, 2)  # the row's period is a date edit like From
    assert end.displayFormat() == "MM/dd/yyyy h:mm Ap"
    end.setDateTime(day("2007-01-01T00:00:00"))
    assert dialog._layer_rows[key][0][2] == "2007-01-01T00:00:00"
    assert dates()[0] == day("2007-01-01T00:00:00")

    dialog._layer_rows[key].clear()
    categorical._show_rows(dialog, key)
    assert dates()[0] == day("1990-01-01T00:00:00")
    assert getattr(dialog, first).displayFormat() == "MM/dd/yyyy h:mm Ap"
