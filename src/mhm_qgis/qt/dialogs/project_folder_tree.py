"""Read-only, lazy project tree backed by Qt's filesystem model."""
from qgis.PyQt.QtWidgets import QDialog

try:
    from qgis.PyQt.QtWidgets import QFileSystemModel
except ImportError:  # Qt 6 moved this model to QtGui.
    from qgis.PyQt.QtGui import QFileSystemModel

from ...core.handlers.store.paths import workspace_folder
from ..ui import load_form


class ProjectFolderTreeDialog(QDialog, load_form("project_folder_tree.ui")):
    def __init__(self, project_folder, parent=None):
        super().__init__(parent)
        self.setupUi(self)
        self.setWindowTitle("Project structure")
        self.model = QFileSystemModel(self)
        self.model.setReadOnly(True)
        self.model.setRootPath(str(project_folder))
        self.treeView_projectFolder.setModel(self.model)
        self.treeView_projectFolder.setRootIndex(self.model.index(str(project_folder)))
        self.treeView_projectFolder.expand(self.model.index(workspace_folder(project_folder)))
        self.treeView_projectFolder.setColumnWidth(0, 360)
