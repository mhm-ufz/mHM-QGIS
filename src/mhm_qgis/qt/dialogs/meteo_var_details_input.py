"""One metadata form shared by every meteorology variable."""
from qgis.PyQt.QtWidgets import QDialog
from ..ui import load_form
from ..controllers import meteo_var_details_input as controller
from ..bindings.meteo_var_details_input import bind


class MeteoVarDetailsDialog(QDialog, load_form("meteo_var_details_input.ui")):
    def __init__(self, variable, project_directory, initial=None, parent=None):
        super().__init__(parent)
        self.setupUi(self)
        self.variable = variable
        self.project_directory = project_directory
        self.spec = None
        controller.populate(self, initial or {})
        bind(self)

    def accept(self):
        if controller.validate(self):
            super().accept()
