"""The shared state of the Outputs tab: what every post-processing page shows.

One signal, `changed(field)`. A setter that receives the current value emits
nothing, so linked views (map ↔ hydrograph ↔ time bar) never echo each other.
"""
from __future__ import annotations

from qgis.PyQt import QtCore

FIELDS = ("run", "compare_run", "basin", "gauge", "cell", "variable", "layer", "period",
          "current_time", "resolution", "statistic")


def _same(old, new) -> bool:
    try:
        return old is new or (type(old) is type(new) and bool(old == new))
    except (TypeError, ValueError):  # arrays compare element-wise
        return False


class AnalysisContext(QtCore.QObject):
    changed = QtCore.pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._values = dict.fromkeys(FIELDS)

    def get(self, field):
        return self._values[field]

    def set(self, field, value) -> bool:
        if field not in self._values:
            raise KeyError(field)
        if _same(self._values[field], value):
            return False
        self._values[field] = value
        self.changed.emit(field)
        return True

    def clear(self) -> None:
        for field in FIELDS:
            self.set(field, None)
