"""The Designer form owns its pages directly inside the step tabs."""
import pytest

pytest.importorskip("qgis.core", reason="QGIS runtime libraries are required")

from mhm_qgis.qt.dialogs.mhm_qgis_main import MhmQgisDialog
from mhm_qgis.qt.controllers import grid


def test_each_step_owns_its_controls():
    dialog = MhmQgisDialog()
    for tab, button in (
        (dialog.tab_geometry, dialog.pushButton_executeAllMorphology),
        (dialog.tab_meteo, dialog.pushButton_executeAllMeteo),
        (dialog.tab_hydro, dialog.pushButton_observationDisplay_discharge),
        (dialog.tab_execution, dialog.pushButton_execute_mHM),
        (dialog.tab_outputs, dialog.pushButton_outputVarDisplay),
    ):
        dialog.tabWidget_steps.setCurrentWidget(tab)
        assert tab.isAncestorOf(button)
    assert not hasattr(dialog, "stackedWidget")
    dialog.close()


def test_work_type_switches_configuration_page():
    dialog = MhmQgisDialog()
    dialog.comboBox_mhmWorkType.setCurrentIndex(1)
    assert dialog.stackedWidget_configuration.currentWidget() is dialog.page_multiDomain
    assert not dialog.tab_geometry.isEnabled()
    dialog.comboBox_mhmWorkType.setCurrentIndex(0)
    assert dialog.stackedWidget_configuration.currentWidget() is dialog.page_singleDomain
    assert dialog.tab_geometry.isEnabled()
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
