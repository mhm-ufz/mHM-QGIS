"""Regression tests for UI forwarding of captured tool messages."""

import io
import logging
import sys

from mhm_qgis import standalone

standalone.install(force=True)

from mhm_qgis.applications.mhm_tools_handler import capture_messages  # noqa: E402
from mhm_qgis.qt.dialogs.mhm_qgis_main import MhmQgisDialog  # noqa: E402


class _LogText:
    def __init__(self):
        self.messages = []

    def append(self, message):
        self.messages.append(message)


class _Dialog:
    log_message = MhmQgisDialog.log_message

    def __init__(self):
        self.LogText = _LogText()


def test_captured_info_is_forwarded_once(monkeypatch):
    """A UI console echo must not feed back into redirected stdout."""
    console = io.StringIO()
    monkeypatch.setattr(sys, "__stdout__", console)
    dialog = _Dialog()

    with capture_messages(dialog.log_message):
        logging.getLogger("mhm_tools.pre.test").info("definition written")

    assert dialog.LogText.messages == ["INFO: definition written"]
    assert console.getvalue() == "INFO: definition written\n"
