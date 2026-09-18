"""Focused checks for tracked commands in the persistent terminal."""
import pytest

pytest.importorskip("qgis.core")

from mhm_qgis.qt.dialogs.project_terminal import ProjectTerminalDialog


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
