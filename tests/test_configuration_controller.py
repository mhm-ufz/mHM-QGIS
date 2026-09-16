"""Focused tests for the nml-tools configuration bridge."""
from __future__ import annotations

import pytest

import os
import sys
import types
from pathlib import Path

import nml_tools.gui

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")



pytest.importorskip("qgis.core", reason="QGIS runtime libraries are required")

from mhm_qgis.qt.controllers import main as controller_module
from mhm_qgis.dependency_bootstrap import (
    configure_qtpy_api,
    dependency_from_requirement_line,
)
from mhm_qgis.core.handlers.store.paths import workspace_folder


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
        for name in controller_module.TIME_WIDGETS.values():
            setattr(self, name, SimpleNamespace(dateTime=lambda: stamp))
        self.messages = []
        self.saved = 0
        self.terminal = Terminal()

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
        lambda schemas, folder, profiles, **kwargs: calls.append(dict(schemas_dir=schemas, output_dir=str(folder), file_profiles=profiles, **kwargs)) or 0,
    )

    assert controller_module.edit_namelists(dialog) is True
    assert prepared == [(str(tmp_path), "5.13")]
    assert calls == [{
        "schemas_dir": Path(controller_module.plugin_root()) / "nml-schemas" / "v5.13",
        "output_dir": workspace_folder(tmp_path),
        "initial_values": {
            "main": {"config_project": {"project_details": "5.13"}}
        },
        "initial_dimensions": {"n_domains": 2},
        "file_profiles": {"main": [], "parameter": [], "output": []},
    }]


def test_run_mhm_uses_project_terminal(tmp_path: Path, monkeypatch) -> None:
    dialog = Dialog(tmp_path)
    monkeypatch.setattr(
        controller_module,
        "ensure_project_structure",
        lambda path, version: [],
    )

    assert controller_module.run_mhm(dialog) is True
    assert dialog.terminal.calls == [(
        "mhm",
        {"cwd": workspace_folder(tmp_path), "show": True},
    )]


def test_version_change_prepares_project_and_saves_state(
        tmp_path: Path, monkeypatch) -> None:
    dialog = Dialog(tmp_path, "5.13")
    prepared = []
    refreshed = []
    dialog.refresh_advanced_nml_settings = lambda: refreshed.append(True)
    monkeypatch.setattr(controller_module, "restore_simulation_times", lambda _dialog: None)
    monkeypatch.setattr(
        controller_module,
        "ensure_project_structure",
        lambda path, version: prepared.append((path, version)),
    )

    controller_module.handle_version_changed(dialog)

    assert prepared == [(str(tmp_path), "5.13")]
    assert refreshed == [True]
    assert dialog.saved == 1


def test_configuration_defaults_and_missing_project(monkeypatch):
    dialog = Dialog("", " ")
    assert controller_module.selected_version(dialog) == "6.0"
    del dialog.comboBox_mHMversion
    assert controller_module.selected_version(dialog) == "6.0"
    warnings = []
    monkeypatch.setattr(
        controller_module.QMessageBox, "warning",
        lambda *args: warnings.append(args[1]),
    )
    assert controller_module.edit_namelists(dialog) is False
    assert controller_module.run_mhm(dialog) is False
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
