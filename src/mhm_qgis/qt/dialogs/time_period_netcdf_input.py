"""The time window of a NetCDF glob, for the Time Period page."""
from qgis.PyQt.QtWidgets import QDialog, QMessageBox

from ..controllers import time
from ..ui import load_form
from .input_selection import elide_named_left


class TimePeriodDialog(QDialog, load_form("time_period_netcdf_input.ui")):
    def __init__(self, source=None, parent=None):
        super().__init__(parent)
        self.setupUi(self)
        elide_named_left(self)  # long paths keep their file name in sight
        time.populate_window(self, source or {})
        self.pushButton_browseTimePeriodNetcdfFile.clicked.connect(lambda: time.browse_window_file(self))
        self.pushButton_scanTimePeriodFiles.clicked.connect(lambda: time.scan_window(self))

    def accept(self):
        if self.found is None:
            QMessageBox.warning(self, "Time period", "Scan the files first.")
            return
        super().accept()
