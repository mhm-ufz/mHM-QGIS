# -*- coding: utf-8 -*-
"""Persistent project terminal dialog for mhm_qgis."""
from __future__ import annotations

import os
import shlex

from qgis.PyQt import QtCore, QtGui, QtWidgets

from ...qt.controllers import project_terminal as terminal_controller
from ...qt.bindings.project_terminal import bind as bind_terminal
from ..ui import load_form

Ui_ProjectTerminalDialog = load_form("project_terminal_dialog.ui")


class ProjectTerminalDialog(QtWidgets.QDialog, Ui_ProjectTerminalDialog):
    """A small persistent shell session shown in a reusable dialog."""

    def __init__(self, parent=None):
        super(ProjectTerminalDialog, self).__init__(parent)
        self.setupUi(self)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose, False)

        self._cwd = None
        self._history = []
        self._history_index = 0
        self._process = QtCore.QProcess(self)
        self._process.setProcessChannelMode(QtCore.QProcess.ProcessChannelMode.MergedChannels)
        self._process.readyReadStandardOutput.connect(self._read_output)
        self._process.finished.connect(self._process_finished)
        self._process.errorOccurred.connect(self._process_error)

        font = QtGui.QFont("Monospace")
        font.setStyleHint(QtGui.QFont.StyleHint.TypeWriter)
        self.output.setFont(font)
        self.command_edit.installEventFilter(self)
        bind_terminal(self)

    def show_for_directory(self, cwd: str) -> bool:
        """Show the terminal and ensure its shell is in ``cwd``."""
        if not self.ensure_session(cwd):
            self.show()
            self.raise_()
            self.activateWindow()
            return False
        self.show()
        self.raise_()
        self.activateWindow()
        self.command_edit.setFocus()
        return True

    def run_command(
            self,
            command: str,
            cwd: str | None = None,
            show: bool = True) -> bool:
        """Run a command in the persistent shell session."""
        if cwd is not None and not self.ensure_session(cwd):
            return False
        if cwd is None and not self.is_running():
            start_cwd = self._cwd or os.getcwd()
            if not self.ensure_session(start_cwd):
                return False
        if show:
            self.show_for_directory(cwd or self._cwd or os.getcwd())
        self._write_command(command)
        return True

    def ensure_session(self, cwd: str) -> bool:
        """Start or reuse the shell session for the requested directory."""
        cwd = os.path.abspath(cwd)
        if self.is_running():
            if self._cwd != cwd:
                self._write_command(self._cd_command(cwd))
                self._cwd = cwd
            return True

        self._cwd = cwd
        self._append_text(f"Starting terminal session in {cwd}\n")
        program, args = self._shell_command()
        self._process.setWorkingDirectory(cwd)
        self._process.start(program, args)
        if not self._process.waitForStarted(3000):
            self._append_text(f"Could not start shell: {program}\n")
            return False
        return True

    def is_running(self) -> bool:
        """Return whether the backing shell process is alive."""
        return self._process.state() != QtCore.QProcess.ProcessState.NotRunning

    def send_current_command(self) -> None:
        """Send the command line contents to the shell."""
        command = self.command_edit.text()
        if not command.strip():
            return
        if not self.is_running():
            self.ensure_session(self._cwd or os.getcwd())
        self._history.append(command)
        self._history_index = len(self._history)
        self.command_edit.clear()
        self._write_command(command)

    def eventFilter(self, watched, event):
        """Provide a minimal command history for the input line."""
        if watched is self.command_edit and event.type() == QtCore.QEvent.Type.KeyPress:
            if event.key() == QtCore.Qt.Key.Key_Up:
                self._show_history(-1)
                return True
            if event.key() == QtCore.Qt.Key.Key_Down:
                self._show_history(1)
                return True
        return super(ProjectTerminalDialog, self).eventFilter(watched, event)

    def closeEvent(self, event):
        """Hide the dialog without stopping the shell process."""
        event.ignore()
        self.hide()

    def reject(self) -> None:
        """Hide on Escape/window close instead of ending the shell."""
        self.hide()

    def _shell_command(self) -> tuple[str, list[str]]:
        if os.name == "nt":
            return os.environ.get("COMSPEC", "cmd.exe"), []
        return os.environ.get("SHELL", "/bin/bash"), []

    def _cd_command(self, cwd: str) -> str:
        if os.name == "nt":
            return f'cd /d "{cwd}"'
        return f"cd {shlex.quote(cwd)}"

    def _write_command(self, command: str) -> None:
        if not self.is_running():
            self._append_text("No active shell session.\n")
            return
        self._append_text(f"$ {command}\n")
        self._process.write((command + "\n").encode("utf-8"))

    def _read_output(self) -> None:
        data = bytes(self._process.readAllStandardOutput())
        if data:
            self._append_text(data.decode("utf-8", errors="replace"))

    def _process_finished(self, exit_code, exit_status) -> None:
        self._append_text(f"\nShell exited with code {exit_code}.\n")

    def _process_error(self, error) -> None:
        self._append_text(f"Shell error: {self._process.errorString()}\n")

    def _append_text(self, *args, **kwargs):
        return terminal_controller._append_text(self, *args, **kwargs)

    def _show_history(self, *args, **kwargs):
        return terminal_controller._show_history(self, *args, **kwargs)
