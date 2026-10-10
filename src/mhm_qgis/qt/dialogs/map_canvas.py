"""Standalone map window: the project's layer tree beside one map canvas.

A `QMainWindow`, so post-processing docks and toolbars can sit around it the way
they do around QGIS Desktop's main window (`qt/docks/host.py`); its central widget
is the tree and canvas, which docks never replace.
"""
from __future__ import annotations

from pathlib import Path

from qgis.core import QgsLayerTreeModel, QgsProject
from qgis.gui import QgsLayerTreeMapCanvasBridge, QgsLayerTreeView, QgsMapCanvas
from qgis.PyQt import QtCore, QtWidgets

from ...qgis_bridge import layers


class MapCanvasWindow(QtWidgets.QMainWindow):
    def __init__(self, parent=None):
        super().__init__(parent, QtCore.Qt.WindowType.Window)
        self.setObjectName("mhmQgisMapWindow")
        self.setWindowTitle("mHM QGIS Map")
        self.setAcceptDrops(True)
        root = QgsProject.instance().layerTreeRoot()

        self.tree = QgsLayerTreeView()
        # An unparented model is garbage-collected under the view and crashes it.
        model = QgsLayerTreeModel(root, self.tree)
        model.setFlag(QgsLayerTreeModel.Flag.AllowNodeReorder)
        model.setFlag(QgsLayerTreeModel.Flag.AllowNodeChangeVisibility)
        self.tree.setModel(model)
        self.canvas = QgsMapCanvas()
        # Follows tree order and visibility, and zooms to the first layer added.
        self.bridge = QgsLayerTreeMapCanvasBridge(root, self.canvas, self)
        self._follow_project_crs()
        QgsProject.instance().crsChanged.connect(self._follow_project_crs)
        if QgsProject.instance().mapLayers():
            self.canvas.zoomToFullExtent()

        self.cursor_label = QtWidgets.QLabel()
        self.time_label = QtWidgets.QLabel()
        self.canvas.xyCoordinates.connect(self._show_cursor)
        status = self.statusBar()
        status.addWidget(self.cursor_label)
        status.addPermanentWidget(self.time_label)

        splitter = QtWidgets.QSplitter()
        splitter.addWidget(self.tree)
        splitter.addWidget(self.canvas)
        splitter.setStretchFactor(1, 1)
        self.setCentralWidget(splitter)
        self.resize(1200, 800)

    def _follow_project_crs(self):
        self.canvas.setDestinationCrs(QgsProject.instance().crs())

    def _show_cursor(self, point):
        self.cursor_label.setText(f"{point.x():.10g}, {point.y():.10g}")

    def set_time(self, when=None):
        self.time_label.setText(when.isoformat() if when is not None else "")

    # The canvas and tree ignore file drags, so Qt hands them up to this window.
    def dragEnterEvent(self, event):
        if any(url.isLocalFile() for url in event.mimeData().urls()):
            event.acceptProposedAction()

    dragMoveEvent = dragEnterEvent

    def dropEvent(self, event):
        log = getattr(self.parent(), "log_message", None)
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if not path or layers.find_by_source(path) is not None:
                continue
            name = Path(path).stem
            layer = (layers.open_layer(path, name)
                     or layers.open_layer(path, name, is_raster=False))
            if layer is not None:
                layers.add(layer)
            elif log is not None:
                log(f"ERROR: Could not load dropped file: {path}")
        event.acceptProposedAction()


__all__ = ["MapCanvasWindow"]
