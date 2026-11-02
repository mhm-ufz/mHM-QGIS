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
