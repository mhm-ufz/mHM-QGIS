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
from mhm_qgis.qgis_bridge.layers.compat import input_band
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
    _wait(lambda: "mm/yr" in dialog.textBrowser_postOverview.toPlainText())  # the Overview, in the tab

    _page(dialog, "Map Explorer")
    _wait(lambda: state.widgets.get("map_layer") is not None)
    assert state.ui.horizontalSlider_postTime.maximum() == 61
    layer = state.widgets["map_layer"]
    main = post_map._main_layer(dialog)  # the copy on the host's main canvas
    assert main is not None
    assert state.context.get("variable") == "aET"
    assert layer.bandCount() == 62
    dialog._post.ui.horizontalSlider_postTime.setValue(5)
    _wait(lambda: state.context.get("current_time") == 5)
    assert input_band(layer.renderer()) == 6
    assert input_band(main.renderer()) == 6
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
    assert input_band(layer.renderer()) == 11  # the hidden map caught up when shown


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
    assert table.rowCount() == 1  # Annual
    headers = [table.horizontalHeaderItem(c).text() for c in range(table.columnCount())]
    assert "R (mm)" in headers
    assert dialog._post.ui.comboBox_postBalanceMap.count() > 3
    assert dialog._post.ui.mapCanvas_post.layers()[-1].name() == "OpenStreetMap"  # the shared Map dock's basemap
    assert dialog._post.docks["map"].windowTitle() == f"mHM post: Map: {dialog._post.ui.mapCanvas_post.layers()[-2].name()}"
    times = dialog._post.ui.comboBox_timeBalance
    assert [times.itemData(i) for i in range(times.count())] == ["year", "month", "day"]  # daily output
    times.setCurrentIndex(times.findData("month"))
    times.activated.emit(times.currentIndex())
    assert table.rowCount() == 3
    dialog.comboBox_postResolution.setCurrentText("Month")
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
    """A view's panel is the centre (a page over the canvas), its docks are left tabs
    beside Layers; Map Explorer draws on the main canvas with our tool; turning the
    layout off leaves the window, the project and the map tool as they were."""
    from qgis.core import QgsProject
    from qgis.PyQt.QtWidgets import QDockWidget

    left, right = Qt.DockWidgetArea.LeftDockWidgetArea, Qt.DockWidgetArea.RightDockWidgetArea
    dialog, _ = opened
    window = dialog.map_canvas_window()
    window.show()
    QgsApplication.processEvents()
    before = window.saveState(host.STATE_VERSION)
    layers_dock = window.findChild(QDockWidget, "Layers")
    _open(dialog)
    _page(dialog, "Map Explorer")
    _wait(lambda: dialog._post.widgets.get("map_layer") is not None)
    docks, stack = dialog._post.docks, host.stack(dialog)
    assert dialog.pushButton_postWorkspace.isChecked()
    assert stack.currentWidget() is window.canvas
    assert window.dockWidgetArea(docks["map_controls"]) == left
    assert docks["map_controls"] in window.tabifiedDockWidgets(layers_dock)
    assert docks["map_controls"].isVisible()
    group = QgsProject.instance().layerTreeRoot().findGroup(post_map.TEMPORARY)
    assert "aET" in {node.layer().name() for node in group.findLayers()}
    assert window.canvas.mapTool() is dialog._post.widgets["main_tool"]
    from qgis.core import QgsCoordinateTransform, QgsPointXY

    project = QgsProject.instance()
    project.setCrs(QgsCoordinateReferenceSystem("EPSG:3857"))  # the main canvas follows the project, not the run
    point = QgsCoordinateTransform(QgsCoordinateReferenceSystem("EPSG:32644"), project.crs(),
                                   project).transform(QgsPointXY(503000.0, 2997000.0))
    post_map.clicked(dialog, point.x(), point.y(), None, window.canvas)
    assert dialog._post.context.get("cell") == (1, 1)
    project.setCrs(QgsCoordinateReferenceSystem("EPSG:32644"))

    _page(dialog, "Hydrograph")
    assert stack.currentWidget() is dialog._post.panels["hydrograph"]
    assert docks["map"].isVisible()
    assert not docks["map_controls"].isVisible()
    assert window.canvas.mapTool() is None  # the user's (none here) is back
    stack.setCurrentWidget(window.canvas)  # as QGIS does when a layer is added
    nav = dialog.listWidget_postNavigation
    nav.itemClicked.emit(nav.currentItem())
    assert stack.currentWidget() is dialog._post.panels["hydrograph"]  # selecting the view brings it back

    _page(dialog, "Map Explorer")
    assert window.dockWidgetArea(docks["cell_inspector"]) == right  # declared on the right
    window.addDockWidget(left, docks["cell_inspector"])  # the user moves it
    _page(dialog, "Water balance")
    _page(dialog, "Map Explorer")
    assert window.dockWidgetArea(docks["cell_inspector"]) == left  # remembered
    dialog.pushButton_postResetLayout.click()
    assert window.dockWidgetArea(docks["cell_inspector"]) == right  # declared again
    dialog.pushButton_postWorkspace.setChecked(False)
    assert dialog._post.docks == {}
    assert dialog._post.panels == {}
    assert dialog._post.toolbar is None
    assert stack.count() == 1
    assert stack.currentWidget() is window.canvas
    assert QgsProject.instance().layerTreeRoot().findGroup(post_map.TEMPORARY) is None
    assert window.canvas.mapTool() is None
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert window.saveState(host.STATE_VERSION) == before
    assert host.settings().value(f"{host.SETTINGS}/user_layout") is None


def test_desktop_docks_go_through_iface(tmp_path, monkeypatch):
    """In QGIS Desktop the panels are pages of the canvas's stack, the docks join the
    main window through `iface` as tabs beside Layers, and all leave it as found."""
    from types import SimpleNamespace

    from qgis.gui import QgsMapCanvas
    from qgis.PyQt.QtWidgets import (
        QDockWidget,
        QMainWindow,
        QStackedWidget,
        QTextEdit,
        QVBoxLayout,
        QWidget,
    )

    monkeypatch.setattr(host, "settings", lambda: QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat))
    main = QMainWindow()
    central, stack, canvas = QWidget(), QStackedWidget(), QgsMapCanvas()
    stack.addWidget(canvas)  # as QGIS 4.2 holds its canvas
    QVBoxLayout(central).addWidget(stack)
    main.setCentralWidget(central)
    layers = QDockWidget("Layers", main)
    layers.setObjectName("Layers")
    layers.setWidget(QTextEdit())
    main.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, layers)
    main.resize(1200, 800)  # big enough that the docks do not grow it
    main.show()
    QgsApplication.processEvents()
    calls = []
    iface = SimpleNamespace(
        mainWindow=lambda: main, mapCanvas=lambda: canvas,
        addDockWidget=lambda area, dock: (calls.append("add"), main.addDockWidget(area, dock)),
        removeDockWidget=lambda dock: (calls.append("remove"), main.removeDockWidget(dock)),
        addToolBar=main.addToolBar)
    dialog = SimpleNamespace(iface=iface, _post=post_context.PostState(context=None))
    before = main.saveState(host.STATE_VERSION)
    host.begin(dialog)
    host.time_toolbar(dialog)
    host.apply(dialog, "processes")
    assert stack.currentWidget() is dialog._post.panels["processes"]
    assert dialog._post.docks["map"] in main.tabifiedDockWidgets(layers)
    assert main.findChild(QDockWidget, "mhmPostMapDock") is dialog._post.docks["map"]
    host.end(dialog)
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert calls == ["add"] * 2 + ["remove"] * 2  # Map and Cell inspector
    assert stack.count() == 1
    assert stack.currentWidget() is canvas
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
    assert host.stack(dialog).currentWidget() is state.panels["compare_maps"]  # the three maps in the centre
    assert state.docks["compare"].isVisible()
    combo = ui.comboBox_postCompareRun
    combo.setCurrentIndex(next(i for i in range(combo.count()) if combo.itemText(i).startswith("baseline")))
    combo.activated.emit(combo.currentIndex())
    _wait(lambda: ui.tableWidget_postCompare.rowCount() > 1 and not dialog.task_coordinator.is_busy())
    assert ui.tableWidget_postCompare.verticalHeaderItem(1).text().startswith("Gauge 398")
    assert ui.mapCanvas_postA.layers()[-1].name().startswith("A: ")
    assert ui.mapCanvas_postB.layers()[-1].name().startswith("B: ")
    assert ui.mapCanvas_postDelta.layers()[-1].name().startswith("B - A")
    extent = ui.mapCanvas_postB.extent()
    extent.scale(0.5)
    ui.mapCanvas_postB.setExtent(extent)
    assert ui.mapCanvas_postA.extent().center() == ui.mapCanvas_postB.extent().center()  # linked
    assert ui.mapCanvas_postDelta.scale() == pytest.approx(ui.mapCanvas_postB.scale(), rel=1e-6)
    sizes, scale = ui.splitter_postCompareMaps.sizes(), ui.mapCanvas_postA.scale()
    ui.splitter_postCompareMaps.moveSplitter(sizes[0] // 2, 1)  # A narrower, B wider: the scale stays
    QCoreApplication.processEvents()
    for canvas in (ui.mapCanvas_postA, ui.mapCanvas_postB, ui.mapCanvas_postDelta):
        assert canvas.scale() == pytest.approx(scale, rel=1e-6)

    _page(dialog, "Extremes")
    _wait(lambda: "ten or more years" in ui.label_postExtremesNotes.text())  # 2 full years in 800 days
    assert state.widgets["widget_postExtremesAnnual"].listDataItems()
    assert "Q maximum" in ui.mapCanvas_post.layers()[-2].name()

    _page(dialog, "Drought")
    _wait(lambda: state.widgets.get("smi") is not None and not dialog.task_coordinator.is_busy())
    assert "streamflow droughts" in ui.label_postDroughtNotes.text()
    assert ui.mapCanvas_post.layers()[-2].name().startswith("SMI 2000-01")

    _page(dialog, "Anomalies")
    _wait(lambda: ui.label_postAnomaliesNotes.text() != "")
    if ui.tableWidget_postFindings.rowCount():
        ui.tableWidget_postFindings.selectRow(0)
    assert "anomaly z" in ui.mapCanvas_post.layers()[-2].name()

    _page(dialog, "Summary")
    _wait(lambda: ui.tableWidget_postSummary.rowCount() == 2)
    assert "by basin" in ui.mapCanvas_post.layers()[-2].name()

    state.context.set("cell", (1, 1))
    post_advanced.why_here(dialog)
    _wait(lambda: "Row 1, column 1" in ui.textBrowser_postWhyHere.toPlainText())
    assert state.docks["why_here"].isVisible()
    _page(dialog, "Hydrograph")
    assert not state.docks["why_here"].isVisible()  # not part of the view: hidden with it


def test_export_writes_the_shown_view(opened, tmp_path):
    """Tables re-read as numbers, plots as PNG/SVG/CSV, the map as a georeferenced
    GeoTIFF and a CF netCDF holding the shown time step."""
    import pandas as pd
    from osgeo import gdal

    from mhm_qgis.qt.controllers import post_export

    dialog, truth = opened
    _open(dialog)
    _page(dialog, "Hydrograph")
    _wait(lambda: dialog._post.ui.tableWidget_postHydrographMetrics.rowCount() > 0)
    out = tmp_path / "export"
    written = post_export.export_view(dialog, out)
    _wait(lambda: not dialog.task_coordinator.is_busy())
    metrics = pd.read_csv(out / "hydrograph_HydrographMetrics.csv").set_index("Metric")["Value"]
    assert float(metrics["NSE"]) < 1
    assert {"hydrograph_HydrographPlot.png", "hydrograph_HydrographPlot.svg",
            "hydrograph_HydrographPlot.csv"} <= {path.name for path in written}
    _page(dialog, "Map Explorer")
    _wait(lambda: dialog._post.widgets.get("map_layer") is not None)
    dialog._post.ui.horizontalSlider_postTime.setValue(3)
    _wait(lambda: dialog._post.context.get("current_time") == 3)
    post_export.export_view(dialog, out)
    _wait(lambda: not dialog.task_coordinator.is_busy())
    tif = out / "map_explorer_aET_2000-01-04T00_00.tif"  # the main canvas's map at the shown step, 4th interval
    handle = gdal.Open(str(tif))
    assert handle.GetGeoTransform()[1] == pytest.approx(2000.0)
    assert "32644" in handle.GetProjection() or "UTM" in handle.GetProjection()
    with netCDF4.Dataset(truth.files["mhm"]) as source:
        expected = np.ma.filled(source["aET"][3], np.nan)
    shown = handle.ReadAsArray().astype(float)
    shown[shown == handle.GetRasterBand(1).GetNoDataValue()] = np.nan
    np.testing.assert_allclose(shown, expected, rtol=1e-6)
    with netCDF4.Dataset(tif.with_suffix(".nc")) as exported:
        assert "CF" in exported.getncattr("Conventions")
        assert any(getattr(var, "grid_mapping_name", None) for var in exported.variables.values())
