"""Saved gauges drive the station list and observation plot, without QGIS."""
import math
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from mhm_qgis.core.handlers.state.domain_state import save_state
from mhm_qgis.core.handlers.store.layout import streamflow_observation_folder
from mhm_qgis.core.post.graph import discharge_series, stations


def _observation(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "00350 Gauge 00350\nnodata -9999\nn 1\n"
        "start 2020 01 01 00 00\nend 2020 01 05 00 00\n"
        "2020 01 01 00 00 0\n2020 01 02 00 00 -9999\n"
        "2020 01 03 00 00 4.5\n2020 01 05 00 00 7\n"
    )
    return path


def test_saved_gauges_and_missing_observations(tmp_path):
    folder = Path(streamflow_observation_folder(tmp_path))
    path = _observation(folder / "00350.txt")
    _observation(folder / "stale.txt")
    save_state(tmp_path, {"outlets": {
        "00350": {"is_gauged": True}, "500": {"is_gauged": False},
    }})
    assert stations(tmp_path) == [{"id": "00350", "path": str(path)}]
    x, y = discharge_series(path)
    assert x[0] == datetime(2020, 1, 1, tzinfo=UTC).timestamp()
    assert len(x) == 5
    assert y[0] == 0
    assert y[2] == 4.5
    assert y[4] == 7
    assert math.isnan(y[1])
    assert math.isnan(y[3])
    with pytest.raises(FileNotFoundError):
        discharge_series(folder / "missing.txt")
    path.write_text("not observations\n")
    with pytest.raises(ValueError, match="Not an mHM"):
        discharge_series(path)


def test_discharge_button_opens_reusable_plot_and_project_change_clears_stations(tmp_path):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("qgis.core", reason="QGIS runtime libraries are required")
    pytest.importorskip("pyqtgraph")
    from qgis.PyQt.QtWidgets import QApplication

    from mhm_qgis.qt.dialogs.mhm_qgis_main import MhmQgisDialog

    _app = QApplication.instance() or QApplication([])  # keep a created application alive
    path = _observation(Path(streamflow_observation_folder(tmp_path)) / "00350.txt")
    save_state(tmp_path, {"outlets": {"00350": {"is_gauged": True}}})
    dialog = MhmQgisDialog()
    dialog.project_folder = str(tmp_path)
    dialog.load_input_state()
    assert dialog.comboBox_displayOutletStation.currentText() == "00350"
    assert dialog.comboBox_displayOutletStation.currentData() == str(path)
    dialog.pushButton_observationDisplay_discharge.click()
    plot = dialog._discharge_plot
    assert plot.isVisible()
    assert "00350" in plot.windowTitle()
    assert plot.plot.getAxis("bottom").labelText == "Date"
    assert plot.plot.getAxis("left").labelUnits == "m³/s"
    assert math.isnan(plot.curve.getData()[1][1])
    dialog.pushButton_observationDisplay_discharge.click()
    assert dialog._discharge_plot is plot
    dialog.project_folder = str(tmp_path / "other")
    dialog.load_input_state()
    assert dialog.comboBox_displayOutletStation.count() == 0
    assert not dialog.pushButton_observationDisplay_discharge.isEnabled()
    assert not plot.isVisible()
    dialog.close()
    # Delete while `plot` is still referenced: left to the cycle collector, pyqtgraph's
    # C++ items can outlive their Python wrappers and segfault the session teardown.
    from qgis.PyQt.QtCore import QCoreApplication, QEvent

    dialog.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
