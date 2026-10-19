"""Standalone map window: the project's layer tree beside one map canvas.

Built like QGIS Desktop's main window, so post-processing (`qt/docks/host.py`) takes
one path for both: the canvas is page 0 of a `QStackedWidget` (view panels are added
as pages over it) and the layer tree is a left dock named "Layers" (side docks tab
with it).
"""
from __future__ import annotations

from pathlib import Path

from qgis.core import QgsLayerTreeModel, QgsProject
from qgis.gui import (
    QgsLayerTreeMapCanvasBridge,
    QgsLayerTreeView,
    QgsLayerTreeViewMenuProvider,
    QgsMapCanvas,
)
from qgis.PyQt import QtCore, QtGui, QtWidgets

from ...qgis_bridge import layers


class _LayerMenu(QgsLayerTreeViewMenuProvider):
    """Right-click menu from the QGIS libraries' own layer actions (Desktop's fuller
    menu is QGIS application code, not library code)."""

    def __init__(self, view, canvas):
        super().__init__()
        self.view, self.canvas = view, canvas

    def createContextMenu(self):
        actions = self.view.defaultActions()
        menu = QtWidgets.QMenu()
        # Keep Python references to the factory actions for the menu's lifetime.
        menu._qgis_actions = []
        if self.view.currentNode() is not None:
            menu._qgis_actions.extend(
                (
                    actions.actionZoomToLayers(self.canvas, menu),
                    actions.actionRenameGroupOrLayer(menu),
                    actions.actionMoveToTop(menu),
                    actions.actionRemoveGroupOrLayer(menu),
                )
            )
        menu._qgis_actions.append(actions.actionAddGroup(menu))
        menu.addActions(menu._qgis_actions)
        return menu


class MapCanvasWindow(QtWidgets.QMainWindow):
    def __init__(self, parent=None):
        # A window of its own, not the dialog's child, so either can come to the front.
        super().__init__(None, QtCore.Qt.WindowType.Window)
        self.dialog = parent
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
        self.tree.setMenuProvider(_LayerMenu(self.tree, self.canvas))
        self.remove_action = self.tree.defaultActions().actionRemoveGroupOrLayer(
            self.tree
        )
        self.remove_action.setShortcut(
            QtGui.QKeySequence(QtGui.QKeySequence.StandardKey.Delete)
        )
        self.remove_action.setShortcutContext(QtCore.Qt.ShortcutContext.WidgetShortcut)
        self.tree.addAction(self.remove_action)
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

        stack = QtWidgets.QStackedWidget()
        stack.addWidget(self.canvas)
        self.setCentralWidget(stack)
        layers_dock = QtWidgets.QDockWidget("Layers", self)
        layers_dock.setObjectName("Layers")
        layers_dock.setWidget(self.tree)
        self.addDockWidget(QtCore.Qt.DockWidgetArea.LeftDockWidgetArea, layers_dock)
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
        log = getattr(self.dialog, "log_message", None)
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
