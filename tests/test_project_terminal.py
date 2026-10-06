"""Focused checks for tracked commands in the persistent terminal."""
import pytest

pytest.importorskip("qgis.core")

from mhm_qgis.qt.dialogs.project_terminal import ProjectTerminalDialog


def test_stop_ends_active_shell_and_command(tmp_path):
    from qgis.PyQt import QtCore

    terminal = ProjectTerminalDialog()
    assert terminal.ensure_session(str(tmp_path))
    assert terminal.run_command("sleep 30", show=False, on_finished=lambda _: None)
    loop = QtCore.QEventLoop()
    terminal._process.finished.connect(loop.quit)
    terminal.stop()
    # Ceiling only: stop() escalates to SIGKILL after 2 s, and `finished` ends the wait.
    QtCore.QTimer.singleShot(10000, loop.quit)
    loop.exec()
    assert not terminal.is_running()
    assert not terminal.command_running()


def test_tracked_command_marker_may_span_output_chunks():
    terminal = ProjectTerminalDialog()
    output, statuses = [], []
    terminal._append_text = output.append
    terminal._command_marker = "__DONE__="
    terminal._command_callback = statuses.append

    terminal._consume_output("model output\n__DO")
    terminal._consume_output("NE__=0\nafter\n")

    assert "".join(output) == "model output\nafter\n"
    assert statuses == [0]
    terminal.deleteLater()
