"""Selecting a step tab must show that step's stacked page."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from mhm_qgis import standalone

standalone.install(force=True)

# isort: off
from qgis.PyQt.QtWidgets import QApplication  # noqa: E402

from mhm_qgis.qt.dialogs.mhm_qgis_main import MhmQgisDialog  # noqa: E402
# isort: on

_APPLICATION = None


def _app():
    global _APPLICATION
    _APPLICATION = QApplication.instance() or _APPLICATION or QApplication([])
    return _APPLICATION


def test_each_step_tab_selects_its_own_page():
    _app()
    dialog = MhmQgisDialog()
    pairs = (
        (dialog.tab_geometry, dialog.page_geometry),
        (dialog.tab_meteo, dialog.page_meteo),
        (dialog.tab_hydro, dialog.page_hydro),
        (dialog.tab_execution, dialog.page_execution),
    )
    for tab, page in pairs:
        index = dialog.tabWidget_steps.indexOf(tab)
        dialog.tabWidget_steps.setCurrentIndex(index)
        assert dialog.stackedWidget.currentWidget() is page, (
            f"tab {dialog.tabWidget_steps.tabText(index)!r} showed "
            f"{dialog.stackedWidget.currentWidget().objectName()!r}"
        )
    dialog.close()


def test_the_execution_tab_and_page_share_index_three():
    _app()
    dialog = MhmQgisDialog()

    assert dialog.tabWidget_steps.indexOf(dialog.tab_execution) == 3
    assert dialog.stackedWidget.indexOf(dialog.page_execution) == 3

    dialog.tabWidget_steps.setCurrentIndex(3)

    assert dialog.stackedWidget.currentIndex() == 3
    assert dialog.stackedWidget.currentWidget() is dialog.page_execution
    # The page that actually carries the Run mHM controls.
    assert dialog.pushButton_execute_mHM.parent() is dialog.page_execution
    dialog.close()


def test_the_outputs_tab_has_no_page_of_its_own():
    _app()
    dialog = MhmQgisDialog()
    index = dialog.tabWidget_steps.indexOf(dialog.tab_outputs)

    assert dialog.page_for_tab_index(index) is None
    assert dialog.stackedWidget.count() == 4
    dialog.close()
