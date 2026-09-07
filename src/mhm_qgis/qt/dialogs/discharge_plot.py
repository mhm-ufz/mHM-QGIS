"""A reusable, Qt-only observation plot window."""
from qgis.PyQt.QtWidgets import QDialog, QVBoxLayout


class DischargePlotDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        import pyqtgraph as pg

        self.resize(900, 500)
        self.plot = pg.PlotWidget(
            axisItems={"bottom": pg.DateAxisItem(utcOffset=0)},
        )
        self.plot.setLabel("bottom", "Date")
        self.plot.setLabel("left", "Discharge", units="m³/s")
        self.plot.getAxis("left").enableAutoSIPrefix(False)
        self.plot.showGrid(x=True, y=True, alpha=0.2)
        self.curve = self.plot.plot(pen=pg.mkPen("#38a6ff", width=1.5), connect="finite")
        QVBoxLayout(self).addWidget(self.plot)

    def show_series(self, station_id, timestamps, values):
        self.setWindowTitle(f"Discharge — station {station_id}")
        self.curve.setData(timestamps, values, connect="finite")
        self.plot.autoRange()
        self.show()
        self.raise_()
        self.activateWindow()
