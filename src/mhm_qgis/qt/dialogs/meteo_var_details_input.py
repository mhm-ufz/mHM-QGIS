"""One metadata form shared by every meteorology variable."""
from qgis.PyQt.QtWidgets import QDialog

from ..bindings.meteo_var_details_input import bind
from ..controllers import meteo_var_details_input as controller
from ..ui import load_form
from .input_selection import elide_named_left


class MeteoVarDetailsDialog(QDialog, load_form("meteo_var_details_input.ui")):
    def __init__(self, variable, project_directory, initial=None, parent=None):
        super().__init__(parent)
        self.setupUi(self)
        elide_named_left(self)  # long paths keep their file name in sight
        self.variable = variable
        self.project_directory = project_directory
        self.setup_folder = getattr(parent, "setup_folder", None)  # Scan Files caches its scan there
        self.spec = None
        controller.populate(self, initial or {})
        bind(self)

    def draft(self):
        return controller.draft(self)

    def accept(self):
        if controller.validate(self):
            super().accept()
