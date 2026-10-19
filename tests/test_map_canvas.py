"""The map host: a standalone window beside the dialog, or Desktop's own canvas."""
from datetime import datetime
from types import SimpleNamespace

import pytest

pytest.importorskip("qgis.core", reason="QGIS runtime libraries are required")

from osgeo import gdal, osr
from qgis.core import QgsPointXY, QgsProject
from qgis.gui import QgsMapCanvas
from qgis.PyQt import QtCore, QtGui, QtWidgets

from mhm_qgis.qgis_bridge.display.canvas import show_raster
from mhm_qgis.qt.dialogs.mhm_qgis_main import MhmQgisDialog


def _raster(path):
    dataset = gdal.GetDriverByName("GTiff").Create(str(path), 4, 4, 1, gdal.GDT_Float32)
    dataset.SetGeoTransform((500000, 100, 0, 5000000, 0, -100))
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(32632)
    dataset.SetProjection(srs.ExportToWkt())
    dataset = None
    return path


def _drop(widget, *paths):
    data = QtCore.QMimeData()
    data.setUrls([QtCore.QUrl.fromLocalFile(str(path)) for path in paths])
    args = (
        QtCore.Qt.DropAction.CopyAction,
        data,
        QtCore.Qt.MouseButton.LeftButton,
        QtCore.Qt.KeyboardModifier.NoModifier,
    )
    QtWidgets.QApplication.sendEvent(widget, QtGui.QDragEnterEvent(QtCore.QPoint(5, 5), *args))
    QtWidgets.QApplication.sendEvent(widget, QtGui.QDropEvent(QtCore.QPointF(5, 5), *args))


@pytest.fixture
def project():
    QgsProject.instance().clear()
    yield QgsProject.instance()
    QgsProject.instance().clear()


def test_standalone_window_shares_the_project(project, tmp_path):
    dialog = MhmQgisDialog()
    window = dialog.open_map_canvas()
    assert dialog.open_map_canvas() is window
    assert window.parent() is None

    raster = _raster(tmp_path / "dem.tif")
    vector = tmp_path / "gauges.geojson"
    vector.write_text(
        '{"type": "FeatureCollection", "features": [{"type": "Feature", '
        '"properties": {}, "geometry": {"type": "Point", "coordinates": [9.0, 45.0]}}]}'
    )
    _drop(window.canvas.viewport(), raster, vector)
    _drop(window.canvas.viewport(), raster)
    QtWidgets.QApplication.processEvents()
    assert sorted(layer.name() for layer in project.mapLayers().values()) == ["dem", "gauges"]
    assert {layer.name() for layer in window.canvas.layers()} == {"dem", "gauges"}
    assert window.canvas.mapSettings().destinationCrs() == project.crs()

    model = window.tree.layerTreeModel()
    root = project.layerTreeRoot()
    before = [layer.name() for layer in window.canvas.layers()]
    last = QtCore.QPersistentModelIndex(model.node2index(root.children()[-1]))
    assert model.flags(QtCore.QModelIndex(last)) & QtCore.Qt.ItemFlag.ItemIsDragEnabled
    assert model.dropMimeData(
        model.mimeData([QtCore.QModelIndex(last)]),
        QtCore.Qt.DropAction.MoveAction, 0, 0, QtCore.QModelIndex(),
    )
    # The view deletes the dragged original after a successful move drop.
    model.removeRow(last.row())
    QtWidgets.QApplication.processEvents()
    assert [layer.name() for layer in window.canvas.layers()] == before[::-1]

    hidden = root.children()[0]
    assert model.setData(model.node2index(hidden), 0, QtCore.Qt.ItemDataRole.CheckStateRole)
    QtWidgets.QApplication.processEvents()
    assert hidden.layer() not in window.canvas.layers()

    window.canvas.xyCoordinates.emit(QgsPointXY(500100.5, 4999900.25))
    assert window.cursor_label.text() == "500100.5, 4999900.25"
    show_raster(dialog, raster, "dem", when=datetime(2000, 1, 2, 3))
    assert window.time_label.text() == "2000-01-02T03:00:00"
    show_raster(dialog, raster, "dem")
    assert window.time_label.text() == ""
    dialog.show()
    dialog.close()
    QtWidgets.QApplication.processEvents()
    assert not window.isVisible()


def test_layer_menu_removes_selected_layer(project, tmp_path):
    dialog = MhmQgisDialog()
    window = dialog.open_map_canvas()
    show_raster(dialog, _raster(tmp_path / "dem.tif"), "dem")

    layer = next(iter(project.mapLayers().values()))
    node = project.layerTreeRoot().findLayer(layer.id())
    window.tree.setCurrentIndex(window.tree.node2index(node))
    menu = window.tree.menuProvider().createContextMenu()
    remove = next(action for action in menu.actions() if "Remove" in action.text())
    remove.trigger()
    QtWidgets.QApplication.processEvents()

    assert not project.mapLayers()
    dialog.close()


def test_first_standalone_display_opens_the_window(project, tmp_path):
    dialog = MhmQgisDialog()
    assert dialog._map_window is None
    show_raster(dialog, _raster(tmp_path / "dem.tif"), "dem")
    assert dialog._map_window.isVisible()
    assert [layer.name() for layer in dialog._map_window.canvas.layers()] == ["dem"]
    dialog.close()


def test_desktop_focuses_its_own_canvas(project, tmp_path):
    main_window = QtWidgets.QMainWindow()
    canvas = QgsMapCanvas(main_window)
    main_window.setCentralWidget(canvas)
    dialog = MhmQgisDialog(iface=SimpleNamespace(
        mainWindow=lambda: main_window, mapCanvas=lambda: canvas
    ))
    assert dialog.open_map_canvas() is canvas
    show_raster(dialog, _raster(tmp_path / "dem.tif"), "dem")
    assert dialog._map_window is None
    dialog.close()
