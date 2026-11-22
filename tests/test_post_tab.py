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
from mhm_qgis.qt import project_tree
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
    dialog.setup_folder = str(tmp_path)
    yield dialog, truth
    _wait(lambda: not dialog.task_coordinator.is_busy())
    post_context.end_workspace(dialog)
    dialog.close()
    dialog.deleteLater()  # pyqtgraph items must go with Qt's delete, not the cycle collector
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def _open(dialog):
    post_context.populate(dialog)
    _wait(lambda: dialog._post.root is not None and not dialog.task_coordinator.is_busy())


def _row(dialog, text):
    """The analysis row titled `text`: they come last (Domain Setup also has a Processes row)."""
    return next(row for row in reversed(list(project_tree._rows(dialog))) if row.text(0) == text)


def _page(dialog, text):
    dialog.treeWidget_projectStructure.setCurrentItem(_row(dialog, text))
    _wait(lambda: not dialog.task_coordinator.is_busy())


def test_closing_the_dialog_takes_its_docks_out_of_the_host_window(opened):
    dialog, _ = opened
    _open(dialog)
    from mhm_qgis.qt.ui import CHECKBOX_STYLE

    assert CHECKBOX_STYLE in dialog.styleSheet()  # every form's checkboxes
    _page(dialog, "Map Explorer")
    _wait(lambda: dialog._post.widgets.get("map_layer") is not None)
    dialog.shutdown()  # a close while busy; reopening the plugin then deletes this dialog
    assert (dialog._post.workspace, dialog._post.docks, dialog._post.panels) == (False, {}, {})
    assert dialog._post.toolbar is None


def test_the_tab_links_its_views_without_reading_netcdf_on_the_gui_thread(opened, monkeypatch):
    dialog, truth = opened
    real = netCDF4.Dataset

    def guarded(*args, **kwargs):
        assert threading.current_thread() is not threading.main_thread(), "netCDF read on the GUI thread"
        return real(*args, **kwargs)

    monkeypatch.setattr(netCDF4, "Dataset", guarded)
    _open(dialog)
    state = dialog._post
    assert dialog.comboBox_postRun.currentText().startswith("Default (output/)")
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
    assert input_band(state.widgets["map_layer"].renderer()) == 11  # the hidden map caught up when shown


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
    _page(dialog, "Processes")
    resolution = dialog._post.ui.comboBox_processesResolution  # the dock's own, like the balance's time
    resolution.setCurrentText("Month")
    resolution.activated.emit(resolution.currentIndex())
    assert len(state.widgets["processes"].ci.items) >= 3


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
    dialog.setup_folder = str(tmp_path)
    yield dialog, truth, tmp_path
    _wait(lambda: not dialog.task_coordinator.is_busy())
    post_context.end_workspace(dialog)
    dialog.close()
    dialog.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_tier_b_pages(opened_long, monkeypatch):
    """Signatures, Events, Climatology (Validation is hidden for now)."""
    import pyqtgraph as pg

    dialog, _truth, _folder = opened_long
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
    curves = state.widgets["widget_postSignaturesFdc"].listDataItems()
    assert curves
    for item in curves:  # discharge on x, exceedance probability on y
        flows, probability = item.getOriginalDataset()
        assert flows.max() > 1
        assert probability.min() >= 0
        assert probability.max() <= 1
    _page(dialog, "Events")
    _wait(lambda: dialog._post.ui.tableWidget_postEvents.rowCount() > 0)
    assert state.widgets["widget_postEventsPlot"].listDataItems()
    _page(dialog, "Climatology")
    plots = list(state.widgets["climatology"].ci.items)
    assert len(plots) >= 3
    band = next(item for item in plots[0].items if isinstance(item, pg.FillBetweenItem))
    assert not band.path().isEmpty()  # the ± one standard deviation band is drawn
    legend = plots[-1].legend  # the annual totals
    assert legend.items
    assert legend.columnCount == len(legend.items)  # in one row
    assert "Validation" not in [row.text(0) for row in project_tree._rows(dialog)]


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
    assert dialog._post.workspace
    assert project_tree.item(dialog, "outputs").toolTip(project_tree.VIEW) == "Shown on the map"  # the eye
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
    tree = dialog.treeWidget_projectStructure
    tree.itemClicked.emit(tree.currentItem(), 0)
    assert stack.currentWidget() is dialog._post.panels["hydrograph"]  # selecting the view brings it back

    _page(dialog, "Map Explorer")
    assert window.dockWidgetArea(docks["cell_inspector"]) == right  # declared on the right
    window.addDockWidget(left, docks["cell_inspector"])  # the user moves it
    _page(dialog, "Water balance")
    _page(dialog, "Map Explorer")
    assert window.dockWidgetArea(docks["cell_inspector"]) == left  # remembered
    outputs = project_tree.item(dialog, "outputs")
    project_tree.button(dialog, "outputs").click()  # ↺
    assert window.dockWidgetArea(docks["cell_inspector"]) == right  # declared again
    project_tree.clicked(dialog, outputs, project_tree.VIEW)  # the eye: layout off
    assert outputs.toolTip(project_tree.VIEW) == "Hidden"
    assert dialog._post.docks == {}
    assert dialog._post.panels == {}
    assert dialog._post.toolbar is not None  # the time toolbar stays: the inputs use it too
    host.drop_time_toolbar(dialog)
    QgsApplication.processEvents()
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
    host.drop_time_toolbar(dialog)
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert calls == ["add"] * 2 + ["remove"] * 2  # Map and Cell inspector
    assert stack.count() == 1
    assert stack.currentWidget() is canvas
    assert main.saveState(host.STATE_VERSION) == before
    main.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_tier_c_views(opened_long, monkeypatch):
    """Extremes, Drought, Summary, Why here (Compare and Anomalies are hidden for now)."""
    from mhm_qgis.qt.controllers import post_advanced

    dialog, _, _folder = opened_long
    real = netCDF4.Dataset

    def guarded(*args, **kwargs):
        assert threading.current_thread() is not threading.main_thread(), "netCDF read on the GUI thread"
        return real(*args, **kwargs)

    monkeypatch.setattr(netCDF4, "Dataset", guarded)
    _open(dialog)
    state = dialog._post
    ui = state.ui
    _page(dialog, "Extremes")
    _wait(lambda: "ten or more years" in ui.label_postExtremesNotes.text())  # 2 full years in 800 days
    assert state.widgets["widget_postExtremesAnnual"].listDataItems()
    assert "Q maximum" in ui.mapCanvas_post.layers()[-2].name()
    assert not ui.comboBox_postMapVariable.isEnabled()  # the view's own map
    assert ui.tableWidget_postReturnLevels.horizontalHeader().defaultAlignment() & Qt.AlignmentFlag.AlignLeft
    kind = ui.comboBox_postExtremeKind
    kind.setCurrentIndex(kind.findData("min"))
    kind.activated.emit(kind.currentIndex())
    _wait(lambda: not dialog.task_coordinator.is_busy())
    assert state.widgets["widget_postExtremesReturn"].listDataItems()  # minima too, by negation

    _page(dialog, "Drought")
    _wait(lambda: state.widgets.get("smi") is not None and not dialog.task_coordinator.is_busy())
    assert "streamflow droughts" in ui.label_postDroughtNotes.text()
    assert ui.mapCanvas_post.layers()[-2].name().startswith("SMI 2000-01")

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
    variable = dialog._post.ui.comboBox_postMapVariable  # the Hydrograph moved the map to its own variables
    variable.setCurrentText("aET")
    variable.activated.emit(variable.currentIndex())
    _wait(lambda: getattr(dialog._post.widgets.get("map_layer"), "name", lambda: "")() == "aET")
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


def test_the_map_dock_offers_the_variables_of_the_open_view(opened):
    dialog, _ = opened
    _open(dialog)
    state, combo = dialog._post, None
    _page(dialog, "Hydrograph")
    combo = state.ui.comboBox_postMapVariable
    groups = {v.name: v.group for v in state.run.variables}
    offered = [combo.itemText(i) for i in range(combo.count())]
    assert offered
    assert {groups[name] for name in offered} <= {"runoff", "routing", "snow", "soil"}
    assert state.context.get("variable") in offered
    other = offered[-1]
    combo.setCurrentText(other)
    combo.activated.emit(combo.currentIndex())
    assert state.context.get("variable") == other  # the controls tree and the map follow
    assert state.ui.treeWidget_postVariables.currentItem().text(0) == other
    assert combo.isEnabled()
    _page(dialog, "Map Explorer")
    assert combo.count() > len(offered)  # every written variable


def test_processes_read_a_clicked_cell_without_a_cache_and_the_basin_comes_back(opened):
    dialog, _ = opened
    _open(dialog)
    state = dialog._post
    _page(dialog, "Processes")
    notes = state.ui.label_postProcessesNotes
    assert "basin domain" in notes.text()
    iy, ix = (int(i) for i in np.argwhere(np.load(state.root / "weights" / "domain.npy") > 0)[0])
    state.context.set("cell", (iy, ix))
    _wait(lambda: not dialog.task_coordinator.is_busy() and f"row {iy}, column {ix}" in notes.text()
          and "Reading" not in notes.text())
    assert "series cache" not in notes.text()  # the inspector's one pass, no cache to build
    assert len(state.widgets["processes"].ci.items) >= 1
    dialog.comboBox_postBasin.activated.emit(dialog.comboBox_postBasin.currentIndex())
    assert state.context.get("cell") is None
    assert "basin domain" in notes.text()


def test_quick_hydrograph_runs_mhm_tools_in_the_setup_folder(tmp_path):
    """The precipitation panel is added only when the prepared pre.nc exists."""
    from types import SimpleNamespace

    sent = []
    terminal = SimpleNamespace(run_command=lambda command, cwd: sent.append((command, cwd)))
    dialog = SimpleNamespace(setup_folder=str(tmp_path), open_project_terminal=lambda: terminal)
    post_context.quick_hydrograph(dialog)
    pre = tmp_path / "data" / "meteo" / "pre" / "pre.nc"
    pre.parent.mkdir(parents=True)
    pre.touch()
    post_context.quick_hydrograph(dialog)
    (without, cwd), (with_pre, _cwd) = sent
    assert cwd == str(tmp_path)
    assert f"-m mhm_tools hydrograph -i {tmp_path / 'output'} -o {tmp_path / 'eval'} --show &" in without
    assert f"--precipitation-file {pre} --show &" in with_pre
