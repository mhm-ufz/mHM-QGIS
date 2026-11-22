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


def test_a_command_given_a_folder_runs_there_after_a_typed_cd(tmp_path):
    """Run mHM must start in the setup folder whatever the user typed in the terminal."""
    import shlex

    from qgis.PyQt import QtCore

    setup, elsewhere = tmp_path / "setup", tmp_path / "elsewhere"
    setup.mkdir()
    elsewhere.mkdir()
    terminal = ProjectTerminalDialog()
    assert terminal.ensure_session(str(setup))
    terminal._write_command(f"cd {shlex.quote(str(elsewhere))}")
    loop, statuses = QtCore.QEventLoop(), []
    assert terminal.run_command("pwd > where.txt", cwd=str(setup), show=False,
                                on_finished=lambda status: (statuses.append(status), loop.quit()))
    QtCore.QTimer.singleShot(10000, loop.quit)
    loop.exec()
    assert statuses == [0]
    assert (setup / "where.txt").read_text().strip() == str(setup.resolve())
    terminal.stop()
    terminal.deleteLater()


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


def test_enter_sends_the_command_and_keeps_the_output():
    from qgis.PyQt import QtCore
    from qgis.PyQt.QtTest import QTest

    terminal = ProjectTerminalDialog()
    sent = []
    terminal._write_command = sent.append
    terminal.is_running = lambda: True
    terminal.output.setPlainText("earlier output")
    terminal.show()
    terminal.command_edit.setText("ls")
    QTest.keyClick(terminal.command_edit, QtCore.Qt.Key.Key_Return)
    assert sent == ["ls"]
    assert terminal.output.toPlainText() == "earlier output"  # Enter clicked no Clear
    terminal.hide()
    terminal.deleteLater()
