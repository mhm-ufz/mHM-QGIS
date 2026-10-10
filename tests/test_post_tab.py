"""Post P5: the Outputs tab (context, Map Explorer, Hydrograph, balance pages)."""
import json
import threading
import time

import numpy as np
import pytest

pytest.importorskip("qgis.core", reason="QGIS runtime libraries are required")
pytest.importorskip("pyqtgraph")

from post_fixtures import make_run
from qgis.core import QgsApplication, QgsCoordinateReferenceSystem
from qgis.PyQt.QtCore import QCoreApplication, QEvent

from mhm_qgis.core.handlers.store.layout import forget_project_version, input_state_path
from mhm_qgis.core.post import catalog
from mhm_qgis.qt.controllers import post_context, post_hydro, post_map
from mhm_qgis.qt.dialogs.mhm_qgis_main import MhmQgisDialog

netCDF4 = pytest.importorskip("netCDF4")
V5 = {"directories_general": {"dir_out": ["output/"], "dirconfigout": "output/",
                              "mhm_file_restartout": ["restart/mHM_restart_001.nc"]},
      "directories_mhm": {"dir_precipitation": ["data/meteo/pre/"]},
      "mainconfig": {"write_restart": True, "resolution_hydrology": [2000.0]},
      "mainconfig_mhm_mrm": {"timestep": 24}}


def _wait(condition, seconds=30):
    end = time.time() + seconds
    while time.time() < end:
        QgsApplication.processEvents()
        if condition():
            return
        time.sleep(0.01)
    raise AssertionError("timed out")


@pytest.fixture
def opened(tmp_path, monkeypatch):
    truth = make_run(tmp_path, days=62)
    path = input_state_path(tmp_path)
    (tmp_path / ".temp").mkdir(exist_ok=True)
    with open(path, "w", encoding="utf-8") as stream:
        json.dump({"mhm_version": "5.13"}, stream)
    forget_project_version(tmp_path)
    real = catalog.open_run
    monkeypatch.setattr(catalog, "open_run", lambda ref, values=None: real(ref, V5 if values is None else values))
    dialog = MhmQgisDialog()
    dialog.mProjectionSelectionWidget_crs.setCrs(QgsCoordinateReferenceSystem("EPSG:32644"))
    dialog.project_folder = str(tmp_path)
    yield dialog, truth
    _wait(lambda: not dialog.task_coordinator.is_busy())
    dialog.close()
    dialog.deleteLater()  # pyqtgraph items must go with Qt's delete, not the cycle collector
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def _open(dialog):
    post_context.populate(dialog)
    _wait(lambda: dialog._post.root is not None and not dialog.task_coordinator.is_busy())


def _page(dialog, text):
    nav = dialog.listWidget_postNavigation
    nav.setCurrentRow(next(row for row in range(nav.count()) if nav.item(row).text() == text))
    _wait(lambda: not dialog.task_coordinator.is_busy())


def test_the_tab_links_its_views_without_reading_netcdf_on_the_gui_thread(opened, monkeypatch):
    dialog, truth = opened
    real = netCDF4.Dataset

    def guarded(*args, **kwargs):
        assert threading.current_thread() is not threading.main_thread(), "netCDF read on the GUI thread"
        return real(*args, **kwargs)

    monkeypatch.setattr(netCDF4, "Dataset", guarded)
    _open(dialog)
    state = dialog._post
    assert dialog.comboBox_postRun.currentText().startswith("Current (output/)")
    assert dialog.horizontalSlider_postTime.maximum() == 61
    assert "mm/yr" in dialog.textBrowser_postOverview.toPlainText()

    _page(dialog, "Map Explorer")
    _wait(lambda: state.widgets.get("map_layer") is not None)
    layer = state.widgets["map_layer"]
    assert state.context.get("variable") == "aET"
    assert layer.bandCount() == 62
    dialog.horizontalSlider_postTime.setValue(5)
    _wait(lambda: state.context.get("current_time") == 5)
    assert layer.renderer().inputBand() == 6
    post_map.clicked(dialog, 503000.0, 2997000.0)  # row 1, column 1
    assert state.context.get("cell") == (1, 1)
    with real(truth.files["mhm"]) as handle:
        expected = float(handle["aET"][5, 1, 1])
    row = next(r for r in range(dialog.tableWidget_postCellInspector.rowCount())
               if dialog.tableWidget_postCellInspector.item(r, 0).text() == "aET")
    assert float(dialog.tableWidget_postCellInspector.item(row, 1).text()) == pytest.approx(expected, rel=1e-3)
    assert state.widgets["widget_postCellPlot"].listDataItems()

    _page(dialog, "Hydrograph")
    _wait(lambda: dialog.tableWidget_postHydrographMetrics.rowCount() > 0)
    assert dialog.comboBox_postGauge.currentData() == 398
    nse = next(dialog.tableWidget_postHydrographMetrics.item(r, 1).text()
               for r in range(dialog.tableWidget_postHydrographMetrics.rowCount())
               if dialog.tableWidget_postHydrographMetrics.item(r, 0).text() == "NSE")
    assert float(nse) < 1
    post_hydro._cursor_moved(dialog, post_context.seconds(state.intervals[10, 0])[()] + 3600)
    assert state.context.get("current_time") == 10
    assert dialog.horizontalSlider_postTime.value() == 10
    _page(dialog, "Map Explorer")
    assert layer.renderer().inputBand() == 11  # the hidden map caught up when shown


def test_statistic_maps_balance_and_processes(opened):
    dialog, _ = opened
    _open(dialog)
    state = dialog._post
    _page(dialog, "Map Explorer")
    combo = dialog.comboBox_postStatistic
    combo.setCurrentIndex(combo.findData("sum"))
    combo.activated.emit(combo.currentIndex())
    _wait(lambda: state.widgets.get("map_layer") is not None and "sum" in state.widgets["map_layer"].name())
    _page(dialog, "Water balance")
    table = dialog.tableWidget_postWaterBalance
    assert table.rowCount() == 1
    headers = [table.horizontalHeaderItem(c).text() for c in range(table.columnCount())]
    assert "R (mm/yr)" in headers
    assert dialog.comboBox_postBalanceMap.count() > 3
    assert dialog.mapCanvas_postBalance.layers()
    dialog.comboBox_postResolution.setCurrentText("Month")
    assert table.rowCount() == 3
    _page(dialog, "Processes")
    assert len(state.widgets["processes"].ci.items) >= 3


def test_archiving_lists_the_archived_run(opened, monkeypatch):
    dialog, _ = opened
    _open(dialog)
    answers = iter([("baseline", True), ("", True)])
    monkeypatch.setattr(post_context.QInputDialog, "getText", lambda *_args, **_kwargs: next(answers))
    post_context.archive(dialog)
    _wait(lambda: any(ref.name == "baseline" for ref in dialog._post.refs) and not dialog.task_coordinator.is_busy())
    assert dialog.comboBox_postRun.currentText().startswith("baseline")
    assert dialog._post.run.ref.name == "baseline"
    assert np.isfinite(dialog._post.intervals.astype("int64")).all()
