"""The categorical input-type handlers write back to the main dialog.

Each handler opens a modal child dialog. These tests stub the child so the
handler's bookkeeping runs against a real `MhmQgisDialog`, which is where a
child dialog shadowing the parent shows up as an `AttributeError`.
"""

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")



# isort: off
pytest.importorskip("qgis.core", reason="QGIS runtime libraries are required")

from qgis.PyQt.QtWidgets import QApplication, QDialog  # noqa: E402

from mhm_qgis.qt.controllers import main as main_controller  # noqa: E402
from mhm_qgis.qt.dialogs import advanced_inputs  # noqa: E402
from mhm_qgis.qt.dialogs.mhm_qgis_main import MhmQgisDialog  # noqa: E402
from mhm_qgis.core.handlers.lookup import (  # noqa: E402
    LandUseInput,
    LandUsePeriod,
    SoilHorizon,
    SoilInput,
)
# isort: on

_APPLICATION = None


def _app():
    global _APPLICATION
    _APPLICATION = QApplication.instance() or _APPLICATION or QApplication([])
    return _APPLICATION


def _stub(result):
    """Return a child-dialog factory that accepts and yields `result`."""

    class _Child:
        def __init__(self, *_args, **_kwargs):
            pass

        def exec_(self):
            return QDialog.DialogCode.Accepted

        def selected_config(self):
            return result

        def selected_input(self):
            return result

    return _Child


def _dialog(tmp_path):
    dialog = MhmQgisDialog()
    dialog.project_folder = str(tmp_path)
    return dialog


def _lookup_config(tmp_path):
    return SimpleNamespace(
        input_path=str(tmp_path / "soil.tif"),
        lookup_table=str(tmp_path / "soil.csv"),
        mapping_field="source",
        class_field="class",
        classdefinition_path="",
    )


def test_lookup_selection_lands_on_the_main_dialog(tmp_path, monkeypatch):
    _app()
    dialog = _dialog(tmp_path)
    config = _lookup_config(tmp_path)
    monkeypatch.setattr(
        main_controller, "SingleLayerInputDialog", _stub(config)
    )

    dialog.handle_categorical_type(
        "geology", "Single Categorical Layer (with Lookup Table)"
    )

    assert dialog.categorical_lookup_config("geology") == {
        "input_path": config.input_path,
        "lookup_table": config.lookup_table,
        "mapping_field": config.mapping_field,
        "class_field": config.class_field,
    }
    dialog.close()


def test_single_categorical_land_use_lands_on_the_main_dialog(
    tmp_path, monkeypatch
):
    _app()
    dialog = _dialog(tmp_path)
    config = _lookup_config(tmp_path)
    monkeypatch.setattr(
        main_controller, "SingleLayerInputDialog", _stub(config)
    )

    dialog.handle_categorical_type(
        "lc", "Single Categorical Layer (with Lookup Table)"
    )

    assert dialog.categorical_lookup_config("lc") is not None
    assert "land_cover" not in dialog._advanced_inputs
    dialog.close()


def test_historical_land_use_lands_on_the_main_dialog(tmp_path, monkeypatch):
    _app()
    dialog = _dialog(tmp_path)
    scene = tmp_path / "lc_1990.tif"
    scene.touch()
    value = LandUseInput(
        (LandUsePeriod(1990, 2000, scene),),
        Path(tmp_path / "lc.csv"),
        "source",
        "class",
    )
    monkeypatch.setattr(
        advanced_inputs, "HistoricalLandUseDialog", _stub(value)
    )

    dialog.handle_categorical_type(
        "lc", "Multi Historical Layers (with Lookup Table)"
    )

    assert dialog._advanced_inputs["land_cover"] is value
    dialog.close()


def test_multi_horizon_soil_lands_on_the_main_dialog(tmp_path, monkeypatch):
    _app()
    dialog = _dialog(tmp_path)
    layer = tmp_path / "horizon.tif"
    layer.touch()
    value = SoilInput(
        (SoilHorizon(1, 0.0, 0.3, layer, layer, layer, layer),), "g/cm3"
    )
    monkeypatch.setattr(
        advanced_inputs, "MultiHorizonSoilDialog", _stub(value)
    )

    dialog.handle_categorical_type(
        "soil", "Multi-horizon Multi-variate Layers"
    )

    assert dialog._advanced_inputs["soil"] is value
    dialog.close()
