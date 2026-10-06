"""The wizard in real QGIS: answers reach the form, the namelist handoff and Run mHM."""
import json

import pytest

pytest.importorskip("qgis.core", reason="QGIS runtime libraries are required")

from qgis.PyQt.QtCore import QCoreApplication, QEvent, Qt
from qgis.PyQt.QtWidgets import QMessageBox

from mhm_qgis import intro as intro_core
from mhm_qgis.core.handlers.state import nml_settings
from mhm_qgis.core.handlers.store import layout
from mhm_qgis.qt.controllers import intro, model
from mhm_qgis.qt.dialogs.intro_wizard import IntroWizard
from mhm_qgis.qt.dialogs.mhm_qgis_main import MhmQgisDialog
from mhm_qgis.vSpecific import build_initial_values

ANSWERS = {"setup_type": "single", "resolution": "standard", "gauges": "with_gauges",
           "meteo": ["pre", "tavg", "tmin", "tmax"]}


def _dialog(tmp_path, version="6.0"):
    dialog = MhmQgisDialog()
    dialog.comboBox_mHMversion.setCurrentText(version)
    (tmp_path / ".temp").mkdir()
    (tmp_path / ".temp" / "mhm_qgis_input_state.json").write_text(json.dumps({"mhm_version": version}))
    layout.forget_project_version()
    dialog.project_folder = str(tmp_path)
    return dialog


def _close(dialog):
    dialog.close()
    dialog.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_wizard_pages_follow_the_schema_and_return_answers():
    wizard = IntroWizard(intro_core.load_questions(), "6", ANSWERS)
    assert len(wizard.pageIds()) == len(intro_core.load_questions()["pages"]) + 1
    assert wizard.answers() == ANSWERS
    fresh = IntroWizard(intro_core.load_questions(), "6").answers()
    assert fresh["setup_type"] == "single"  # first choice preselected
    assert fresh["meteo"] == ["pre"]  # checkbox defaults
    wizard.deleteLater()


def test_answers_reach_the_form_and_the_one_shot_handoff(tmp_path):
    dialog = _dialog(tmp_path)
    dialog._intro_answers, dialog._intro_pending = dict(ANSWERS), True
    intro.apply(dialog)
    values = nml_settings.load_settings(tmp_path)["intro_values"]
    assert values["main"]["config_processes"] == {"routing": 3, "pet": 1, "snow": 1}
    assert not dialog._intro_pending
    initial = build_initial_values("6.0", dialog)
    assert initial["main"]["config_processes"]["pet"] == 1
    assert initial["output"]["output_mhm"]["out_Q"] is True
    combo = dialog.comboBox_meteoVar
    assert combo.currentText() == "tavg"  # the first forcing the processes still need
    assert "PET case 1" in combo.itemData(combo.findText("tmin"), Qt.ItemDataRole.ToolTipRole)
    _close(dialog)


def test_saving_namelists_retires_the_wizard_values(tmp_path, monkeypatch):
    dialog = _dialog(tmp_path)
    nml_settings.update_section(tmp_path, "intro_values", {"main": {"config_processes": {"pet": 1}}})

    def editor(**_kwargs):
        (tmp_path / "mhm.nml").write_text("&config_processes\n  pet = 1\n/\n")

    monkeypatch.setattr("mhm_qgis.externals.nml_tools_handler.launch_profile_editor", editor)
    assert model.edit_namelists(dialog)
    assert nml_settings.load_settings(tmp_path)["intro_values"] == {}
    _close(dialog)


def test_run_lists_missing_inputs_and_no_keeps_mhm_from_starting(tmp_path, monkeypatch):
    dialog = _dialog(tmp_path)
    (tmp_path / "mhm.nml").write_text("&config_processes\n  pet = 1\n/\n")
    logged = []
    monkeypatch.setattr(dialog, "log_message", logged.append)
    asked = []
    monkeypatch.setattr(QMessageBox, "question",
                        lambda *args: asked.append(args) or QMessageBox.StandardButton.No)
    assert not model.inputs_ready(dialog, str(tmp_path))
    assert asked
    assert any("Missing tmin (Minimum air temperature), needed by PET case 1" in line for line in logged)
    _close(dialog)
