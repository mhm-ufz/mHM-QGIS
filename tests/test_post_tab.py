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
from qgis.PyQt.QtCore import QCoreApplication, QEvent, QSettings, Qt

from mhm_qgis.core.handlers.store.layout import forget_project_version, input_state_path
from mhm_qgis.core.post import catalog
from mhm_qgis.qt.controllers import post_context, post_hydro, post_map
from mhm_qgis.qt.dialogs.mhm_qgis_main import MhmQgisDialog
from mhm_qgis.qt.docks import host

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
    monkeypatch.setattr(host, "settings", lambda: QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat))
    dialog = MhmQgisDialog()
    dialog.mProjectionSelectionWidget_crs.setCrs(QgsCoordinateReferenceSystem("EPSG:32644"))
    dialog.project_folder = str(tmp_path)
    yield dialog, truth
    _wait(lambda: not dialog.task_coordinator.is_busy())
    post_context.end_workspace(dialog)
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
    _page(dialog, "Overview")
    assert dialog._post.ui.horizontalSlider_postTime.maximum() == 61
    assert "mm/yr" in dialog._post.ui.textBrowser_postOverview.toPlainText()

    _page(dialog, "Map Explorer")
    _wait(lambda: state.widgets.get("map_layer") is not None)
    layer = state.widgets["map_layer"]
    assert state.context.get("variable") == "aET"
    assert layer.bandCount() == 62
    dialog._post.ui.horizontalSlider_postTime.setValue(5)
    _wait(lambda: state.context.get("current_time") == 5)
    assert layer.renderer().inputBand() == 6
    post_map.clicked(dialog, 503000.0, 2997000.0)  # row 1, column 1
    assert state.context.get("cell") == (1, 1)
    with real(truth.files["mhm"]) as handle:
        expected = float(handle["aET"][5, 1, 1])
    row = next(r for r in range(dialog._post.ui.tableWidget_postCellInspector.rowCount())
               if dialog._post.ui.tableWidget_postCellInspector.item(r, 0).text() == "aET")
    assert float(dialog._post.ui.tableWidget_postCellInspector.item(row, 1).text()) == pytest.approx(expected, rel=1e-3)
    assert state.widgets["widget_postCellPlot"].listDataItems()

    _page(dialog, "Hydrograph")
    _wait(lambda: dialog._post.ui.tableWidget_postHydrographMetrics.rowCount() > 0)
    assert dialog._post.ui.comboBox_postGauge.currentData() == 398
    nse = next(dialog._post.ui.tableWidget_postHydrographMetrics.item(r, 1).text()
               for r in range(dialog._post.ui.tableWidget_postHydrographMetrics.rowCount())
               if dialog._post.ui.tableWidget_postHydrographMetrics.item(r, 0).text() == "NSE")
    assert float(nse) < 1
    post_hydro._cursor_moved(dialog, post_context.seconds(state.intervals[10, 0])[()] + 3600)
    assert state.context.get("current_time") == 10
    assert dialog._post.ui.horizontalSlider_postTime.value() == 10
    _page(dialog, "Map Explorer")
    assert layer.renderer().inputBand() == 11  # the hidden map caught up when shown


def test_statistic_maps_balance_and_processes(opened):
    dialog, _ = opened
    _open(dialog)
    state = dialog._post
    _page(dialog, "Map Explorer")
    combo = dialog._post.ui.comboBox_postStatistic
    combo.setCurrentIndex(combo.findData("sum"))
    combo.activated.emit(combo.currentIndex())
    _wait(lambda: state.widgets.get("map_layer") is not None and "sum" in state.widgets["map_layer"].name())
    _page(dialog, "Water balance")
    table = dialog._post.ui.tableWidget_postWaterBalance
    assert table.rowCount() == 1
    headers = [table.horizontalHeaderItem(c).text() for c in range(table.columnCount())]
    assert "R (mm/yr)" in headers
    assert dialog._post.ui.comboBox_postBalanceMap.count() > 3
    assert dialog._post.ui.mapCanvas_post.layers()  # on the shared Map dock
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


@pytest.fixture
def opened_long(tmp_path, monkeypatch):
    """A daily run over two years and two months, for the Tier B pages."""
    truth = make_run(tmp_path, days=800)
    path = input_state_path(tmp_path)
    (tmp_path / ".temp").mkdir(exist_ok=True)
    with open(path, "w", encoding="utf-8") as stream:
        json.dump({"mhm_version": "5.13"}, stream)
    forget_project_version(tmp_path)
    real = catalog.open_run
    monkeypatch.setattr(catalog, "open_run", lambda ref, values=None: real(ref, V5 if values is None else values))
    monkeypatch.setattr(host, "settings", lambda: QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat))
    dialog = MhmQgisDialog()
    dialog.mProjectionSelectionWidget_crs.setCrs(QgsCoordinateReferenceSystem("EPSG:32644"))
    dialog.project_folder = str(tmp_path)
    yield dialog, truth, tmp_path
    _wait(lambda: not dialog.task_coordinator.is_busy())
    post_context.end_workspace(dialog)
    dialog.close()
    dialog.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_tier_b_pages(opened_long, monkeypatch):
    from post_fixtures import write_observation

    from mhm_qgis.qt.controllers import post_validation

    dialog, truth, folder = opened_long
    real = netCDF4.Dataset

    def guarded(*args, **kwargs):
        assert threading.current_thread() is not threading.main_thread(), "netCDF read on the GUI thread"
        return real(*args, **kwargs)

    monkeypatch.setattr(netCDF4, "Dataset", guarded)
    _open(dialog)
    state = dialog._post
    _page(dialog, "Signatures")
    _wait(lambda: dialog._post.ui.tableWidget_postSignatures.rowCount() > 0)
    assert dialog._post.ui.label_postSignaturesNotes.text() == ""
    _page(dialog, "Events")
    _wait(lambda: dialog._post.ui.tableWidget_postEvents.rowCount() > 0)
    assert state.widgets["widget_postEventsPlot"].listDataItems()
    _page(dialog, "Climatology")
    assert len(state.widgets["climatology"].ci.items) >= 3
    _page(dialog, "Validation")
    combo = dialog._post.ui.comboBox_postAdapter
    combo.setCurrentIndex(combo.findData("discharge"))
    dialog._post.ui.pushButton_postCompare.click()
    _wait(lambda: dialog._post.ui.tableWidget_postValidation.rowCount() == len(post_validation.GAUGE_SCORES))
    monkeypatch.setattr(netCDF4, "Dataset", real)  # the test writes its observation here
    write_observation(truth, folder / "et.nc")
    monkeypatch.setattr(netCDF4, "Dataset", guarded)
    combo.setCurrentIndex(combo.findData("et"))
    post_validation._observation_chosen(dialog, str(folder / "et.nc"))
    _wait(lambda: dialog._post.ui.comboBox_postObservationVariable.count() == 1)
    dialog._post.ui.pushButton_postCompare.click()
    _wait(lambda: dialog._post.ui.comboBox_postValidationMap.count() > 3 and not dialog.task_coordinator.is_busy())
    assert dialog._post.ui.mapCanvas_post.layers()  # on the shared Map dock
    assert "complete windows" in dialog._post.ui.label_postValidationNotes.text()


def test_each_view_arranges_the_window_and_the_workspace_restores_it(opened):
    dialog, _ = opened
    window = dialog.map_canvas_window()
    window.show()
    QgsApplication.processEvents()
    before = window.saveState(host.STATE_VERSION)
    _open(dialog)
    _page(dialog, "Map Explorer")
    docks = dialog._post.docks
    assert dialog.pushButton_postWorkspace.isChecked()
    assert window.dockWidgetArea(docks["map_controls"]) == Qt.DockWidgetArea.RightDockWidgetArea
    assert window.dockWidgetArea(docks["map"]) == Qt.DockWidgetArea.BottomDockWidgetArea
    assert docks["map"].isVisible()
    assert docks["cell_inspector"].isVisible()
    _page(dialog, "Hydrograph")
    assert window.dockWidgetArea(docks["map"]) == Qt.DockWidgetArea.RightDockWidgetArea
    assert window.dockWidgetArea(docks["hydrograph"]) == Qt.DockWidgetArea.BottomDockWidgetArea
    assert not docks["map_controls"].isVisible()
    assert not docks["cell_inspector"].isVisible()
    _page(dialog, "Map Explorer")
    window.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, docks["cell_inspector"])  # the user moves it
    _page(dialog, "Water balance")
    _page(dialog, "Map Explorer")
    assert window.dockWidgetArea(docks["cell_inspector"]) == Qt.DockWidgetArea.BottomDockWidgetArea  # remembered
    dialog.pushButton_postResetLayout.click()
    assert window.dockWidgetArea(docks["cell_inspector"]) == Qt.DockWidgetArea.RightDockWidgetArea  # declared
    dialog.pushButton_postWorkspace.setChecked(False)
    assert dialog._post.docks == {}
    assert dialog._post.toolbar is None
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert window.saveState(host.STATE_VERSION) == before
    assert host.settings().value(f"{host.SETTINGS}/user_layout") is None


def test_desktop_docks_go_through_iface(tmp_path, monkeypatch):
    """In QGIS Desktop the docks join the main window through `iface` and leave it as found."""
    from types import SimpleNamespace

    from qgis.PyQt.QtWidgets import QMainWindow, QTextEdit

    monkeypatch.setattr(host, "settings", lambda: QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat))
    main = QMainWindow()
    main.setCentralWidget(QTextEdit())
    main.resize(1200, 800)  # big enough that the docks do not grow it
    main.show()
    QgsApplication.processEvents()
    calls = []
    iface = SimpleNamespace(
        mainWindow=lambda: main,
        addDockWidget=lambda area, dock: (calls.append("add"), main.addDockWidget(area, dock)),
        removeDockWidget=lambda dock: (calls.append("remove"), main.removeDockWidget(dock)),
        addToolBar=main.addToolBar)
    dialog = SimpleNamespace(iface=iface, _post=post_context.PostState(context=None))
    before = main.saveState(host.STATE_VERSION)
    host.begin(dialog)
    host.time_toolbar(dialog)
    host.apply(dialog, "processes")
    assert main.dockWidgetArea(dialog._post.docks["processes"]) == Qt.DockWidgetArea.BottomDockWidgetArea
    assert main.findChild(type(dialog._post.docks["map"]), "mhmPostMapDock") is dialog._post.docks["map"]
    host.end(dialog)
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert calls == ["add"] * 3 + ["remove"] * 3
    assert main.saveState(host.STATE_VERSION) == before
    main.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_tier_c_views(opened_long, monkeypatch):
    """Compare (Map B and Map Δ, extents linked), Extremes, Drought, Anomalies, Summary, Why here."""
    from post_fixtures import make_run

    from mhm_qgis.core.post import runs
    from mhm_qgis.qt.controllers import post_advanced

    dialog, _, folder = opened_long
    runs.archive(folder, "baseline")
    make_run(folder, days=800, seed=1)  # the current run, B's twin with other weather
    real = netCDF4.Dataset

    def guarded(*args, **kwargs):
        assert threading.current_thread() is not threading.main_thread(), "netCDF read on the GUI thread"
        return real(*args, **kwargs)

    monkeypatch.setattr(netCDF4, "Dataset", guarded)
    _open(dialog)
    state = dialog._post
    ui = state.ui
    _page(dialog, "Compare")
    assert {"compare", "map", "map_b", "map_delta"} <= {name for name, dock in state.docks.items() if dock.isVisible()}
    combo = ui.comboBox_postCompareRun
    combo.setCurrentIndex(next(i for i in range(combo.count()) if combo.itemText(i).startswith("baseline")))
    combo.activated.emit(combo.currentIndex())
    _wait(lambda: ui.tableWidget_postCompare.rowCount() > 1 and not dialog.task_coordinator.is_busy())
    assert ui.tableWidget_postCompare.verticalHeaderItem(1).text().startswith("Gauge 398")
    assert ui.mapCanvas_post.layers()[-1].name().startswith("A: ")
    assert ui.mapCanvas_postB.layers()[-1].name().startswith("B: ")
    assert ui.mapCanvas_postDelta.layers()[-1].name().startswith("B - A")
    extent = ui.mapCanvas_postB.extent()
    extent.scale(0.5)
    ui.mapCanvas_postB.setExtent(extent)
    assert ui.mapCanvas_post.extent().center() == ui.mapCanvas_postB.extent().center()  # linked
    assert ui.mapCanvas_postDelta.extent().width() == pytest.approx(ui.mapCanvas_postB.extent().width(), rel=0.2)

    _page(dialog, "Extremes")
    _wait(lambda: "ten or more years" in ui.label_postExtremesNotes.text())  # 2 full years in 800 days
    assert state.widgets["widget_postExtremesAnnual"].listDataItems()
    assert "Q maximum" in ui.mapCanvas_post.layers()[-1].name()

    _page(dialog, "Drought")
    _wait(lambda: state.widgets.get("smi") is not None and not dialog.task_coordinator.is_busy())
    assert "streamflow droughts" in ui.label_postDroughtNotes.text()
    assert ui.mapCanvas_post.layers()[-1].name().startswith("SMI 2000-01")

    _page(dialog, "Anomalies")
    _wait(lambda: ui.label_postAnomaliesNotes.text() != "")
    if ui.tableWidget_postFindings.rowCount():
        ui.tableWidget_postFindings.selectRow(0)
    assert "anomaly z" in ui.mapCanvas_post.layers()[-1].name()

    _page(dialog, "Summary")
    _wait(lambda: ui.tableWidget_postSummary.rowCount() == 2)
    assert "by basin" in ui.mapCanvas_post.layers()[-1].name()

    state.context.set("cell", (1, 1))
    post_advanced.why_here(dialog)
    _wait(lambda: "Row 1, column 1" in ui.textBrowser_postWhyHere.toPlainText())
    assert state.docks["why_here"].isVisible()
    _page(dialog, "Hydrograph")
    assert not state.docks["why_here"].isVisible()  # not part of the view: hidden with it
