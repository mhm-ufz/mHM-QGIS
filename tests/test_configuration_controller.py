"""Focused tests for the nml-tools configuration bridge."""
from __future__ import annotations

import os
import sys
import types
from pathlib import Path

import nml_tools.gui
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")



pytest.importorskip("qgis.core", reason="QGIS runtime libraries are required")

from mhm_qgis.core.handlers.state import processing
from mhm_qgis.core.handlers.store.paths import workspace_folder
from mhm_qgis.dependency_bootstrap import (
    configure_qtpy_api,
    dependency_from_requirement_line,
)
from mhm_qgis.qt.controllers import model as controller_module
from mhm_qgis.qt.controllers.time import TIME_WIDGETS
from mhm_qgis.qt.controllers.version import selected as selected_version


class ComboBox:
    def __init__(self, text: str) -> None:
        self.text = text

    def currentText(self) -> str:
        return self.text


class Terminal:
    def __init__(self) -> None:
        self.calls = []

    def run_command(self, command, **kwargs):
        self.calls.append((command, kwargs))
        return True


class Dialog:
    def __init__(self, project_folder: Path, version: str = "6.0") -> None:
        self.project_folder = str(project_folder)
        self.comboBox_mHMversion = ComboBox(version)
        from types import SimpleNamespace
        stamp = SimpleNamespace(toString=lambda _format: "2001-01-01T00:00")
        for name in TIME_WIDGETS.values():
            setattr(self, name, SimpleNamespace(dateTime=lambda: stamp))
        self.messages = []
        self.saved = 0
        self.terminal = Terminal()
        self._loading_input_state = False
        self.groupBox_editCalibrationNamelists = SimpleNamespace(setEnabled=lambda _enabled: None)
        self.run_name = ""
        self.lineEdit_runName = SimpleNamespace(text=lambda: self.run_name)

    def log_message(self, message) -> None:
        self.messages.append(message)

    def save_input_state(self) -> None:
        self.saved += 1

    def open_project_terminal(self):
        return self.terminal


def test_edit_namelists_forwards_project_values_and_dimensions(
        tmp_path: Path, monkeypatch) -> None:
    dialog = Dialog(tmp_path, "5.13")
    calls = []
    prepared = []
    adapter = types.ModuleType("mhm_qgis.vSpecific")
    adapter.build_initial_values = lambda version, owner: {
        "main": {"config_project": {"project_details": version}}
    }
    adapter.build_dimensions = lambda version, owner: {"n_domains": 2}
    monkeypatch.setitem(sys.modules, "mhm_qgis.vSpecific", adapter)
    monkeypatch.setattr(
        controller_module,
        "ensure_project_structure",
        lambda path, version: prepared.append((path, version)),
    )
    monkeypatch.setattr(
        nml_tools.gui,
        "launch_gui",
        lambda schemas, folder, profiles, **kwargs: calls.append(dict(schemas_dir=schemas, output_dir=str(folder), project_profiles=profiles, **kwargs)) or 0,
    )

    assert controller_module.edit_namelists(dialog) is True
    assert prepared == [(str(tmp_path), "5.13")]
    assert calls == [{
        "schemas_dir": Path(__file__).parents[1] / "src/mhm_qgis/nml-schemas/v5.13",
        "output_dir": workspace_folder(tmp_path),
        "initial_values": {
            "main": {"config_project": {"project_details": "5.13"}}
        },
        "initial_dimensions": {"n_domains": 2},
        "project_profiles": "v5",
    }]



@pytest.mark.parametrize(("setup", "namelists", "expected"), [
    ("single", None, "classic"),
    ("single", {"parameter": []}, {"classic": {"parameter": []}}),
    ("multi", None, "combine"),
    ("multi", {"main": ["config_optimize"]}, {"combine": {"meta": ["config_optimize"]}}),
])
def test_edit_namelists_opens_the_setups_v6_project(tmp_path: Path, monkeypatch, setup, namelists, expected) -> None:
    """A v6 single setup edits `classic` (mhm.nml), a combined one `combine` (combine.nml)."""
    import json

    from mhm_qgis.core.handlers.store.layout import (
        forget_project_version,
        input_state_path,
    )

    state = Path(input_state_path(tmp_path))
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(json.dumps({"mhm_version": "6.0", **({"setup": "multi"} if setup == "multi" else {})}))
    forget_project_version(tmp_path)
    dialog = Dialog(tmp_path, "6.0")
    adapter = types.ModuleType("mhm_qgis.vSpecific")
    adapter.build_initial_values = lambda version, owner: {}
    adapter.build_dimensions = lambda version, owner: {"n_domains": 1}
    monkeypatch.setitem(sys.modules, "mhm_qgis.vSpecific", adapter)
    for name in ("ensure_project_structure", "ensure_multi_structure"):
        monkeypatch.setattr(controller_module, name, lambda *_args: None)
    selections = []
    monkeypatch.setattr(nml_tools.gui, "launch_gui",
                        lambda schemas, folder, profiles, **_kwargs: selections.append(profiles) or 0)
    assert controller_module.edit_namelists(dialog, namelists, update_inputs=False) is True
    assert selections == [expected]

def _no_buttons(monkeypatch):
    from mhm_qgis.qt.controllers import workflow_state

    monkeypatch.setattr(workflow_state, "set_button_state", lambda *_args: None)
    monkeypatch.setattr(controller_module, "set_button_state", lambda *_args: None)


def test_run_mhm_uses_project_terminal(tmp_path: Path, monkeypatch) -> None:
    dialog = Dialog(tmp_path)
    _no_buttons(monkeypatch)
    executable = tmp_path / "mhmv6"
    monkeypatch.setattr(
        controller_module,
        "ensure_project_structure",
        lambda path, version: [],
    )
    monkeypatch.setattr(
        controller_module,
        "require_v6_executable",
        lambda owner: str(executable),
    )
    # The input check before a run has its own tests (test_intro_wizard.py).
    monkeypatch.setattr(controller_module, "inputs_ready", lambda owner, folder: True)
    from mhm_qgis.externals import mhm_handler
    monkeypatch.setattr(
        mhm_handler,
        "executable",
        lambda version, v6_path=None: Path(v6_path),
    )

    assert controller_module.run(dialog) is True
    command, options = dialog.terminal.calls[0]
    callback = options.pop("on_finished")
    assert (command, options) == (
        str(executable),
        {"cwd": workspace_folder(tmp_path), "show": True},
    )
    assert processing.workflow(tmp_path, "run_mhm")["status"] == "running"
    callback(0)
    assert processing.workflow(tmp_path, "run_mhm")["status"] == "completed"


def test_a_named_run_points_the_namelists_at_its_folder(tmp_path: Path, monkeypatch) -> None:
    from mhm_qgis.core.post import runs
    from mhm_qgis.qt.controllers import post_context

    dialog = Dialog(tmp_path)
    dialog.run_name = "case1"
    _no_buttons(monkeypatch)
    monkeypatch.setattr(controller_module, "ensure_project_structure", lambda path, version: [])
    monkeypatch.setattr(controller_module, "require_v6_executable", lambda owner: str(tmp_path / "mhmv6"))
    monkeypatch.setattr(controller_module, "inputs_ready", lambda owner, folder: True)
    monkeypatch.setattr(post_context, "populate", lambda _dialog: None)
    from mhm_qgis.externals import mhm_handler
    monkeypatch.setattr(mhm_handler, "executable", lambda version, v6_path=None: Path(v6_path))
    (tmp_path / "mhm.nml").write_text('&config_mhm\n  output_path(1) = "output/mhm_output.nc"\n/\n')

    assert controller_module.run(dialog) is True
    assert 'output_path(1) = "runs/case1/output/mhm_output.nc"' in (tmp_path / "mhm.nml").read_text()
    assert (tmp_path / "runs" / "case1" / "output").is_dir()
    dialog.terminal.calls[0][1]["on_finished"](0)
    assert [ref.name for ref in runs.list_runs(tmp_path)] == ["case1"]


def test_version_change_prepares_project_and_saves_state(
        tmp_path: Path, monkeypatch) -> None:
    dialog = Dialog(tmp_path, "5.13")
    prepared = []
    refreshed = []
    _no_buttons(monkeypatch)
    monkeypatch.setattr(
        controller_module, "refresh_advanced_nml_settings", lambda _dialog: refreshed.append(True)
    )
    monkeypatch.setattr(controller_module, "restore_simulation_times", lambda _dialog: None)
    monkeypatch.setattr(
        controller_module,
        "ensure_project_structure",
        lambda path, version: prepared.append((path, version)),
    )

    controller_module.version_changed(dialog)

    assert prepared == [(str(tmp_path), "5.13")]
    assert refreshed == [True]
    assert dialog.saved == 1


def test_configuration_defaults_and_missing_project(monkeypatch):
    dialog = Dialog("", " ")
    assert selected_version(dialog) == "6.0"
    del dialog.comboBox_mHMversion
    assert selected_version(dialog) == "6.0"
    warnings = []
    monkeypatch.setattr(
        controller_module.QMessageBox, "warning",
        lambda *args: warnings.append(args[1]),
    )
    assert controller_module.edit_namelists(dialog) is False
    assert controller_module.run(dialog) is False
    assert warnings == ["Project Folder Required"] * 2
    assert dialog.terminal.calls == []


def test_editor_failure_is_logged_and_reported(tmp_path, monkeypatch):
    dialog = Dialog(tmp_path)
    errors = []

    def fail(*args):
        raise OSError("Cannot prepare project")

    monkeypatch.setattr(controller_module, "ensure_project_structure", fail)
    monkeypatch.setattr(
        controller_module.QMessageBox, "critical",
        lambda parent, title, message: errors.append((title, message)),
    )
    assert controller_module.edit_namelists(dialog) is False
    assert errors == [("Configuration Error", "Cannot prepare project")]
    assert dialog.messages == [
        "ERROR: Edit Namelists failed. Details: Cannot prepare project"
    ]


def test_qtpy_uses_the_qgis_binding(monkeypatch) -> None:
    from qgis.PyQt.QtCore import QT_VERSION_STR
    monkeypatch.setenv("QT_API", "pyside6")
    expected = "pyqt6" if QT_VERSION_STR.startswith("6.") else "pyqt5"
    assert configure_qtpy_api() == expected
    assert os.environ["QT_API"] == expected


def test_gui_dependency_import_names() -> None:
    nml_tools = dependency_from_requirement_line("nml-tools[gui]")
    assert nml_tools is not None
    assert nml_tools.import_name == "nml_tools"
    assert dependency_from_requirement_line("qtpy").import_name == "qtpy"
    assert dependency_from_requirement_line("guidata").import_name == "guidata"
