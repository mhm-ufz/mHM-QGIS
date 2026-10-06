"""Guided tour of the visible page's main inputs, started by the Help button.

Steps come from `tooltips.yml`, keyed by object name; the current tab and any
visible stacked page inside it pick the steps.
"""
from __future__ import annotations

from functools import cache
from pathlib import Path

import yaml
from qgis.PyQt.QtCore import QEvent, QPoint, QRect, Qt, QUrl
from qgis.PyQt.QtGui import QDesktopServices
from qgis.PyQt.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)


@cache
def load() -> dict:
    return yaml.safe_load((Path(__file__).with_name("tooltips.yml")).read_text(encoding="utf-8"))


def page_keys(dialog) -> list[str]:
    tab = dialog.tabWidget_steps.currentWidget()
    return [tab.objectName()] + [
        stack.currentWidget().objectName()
        for stack in tab.findChildren(QStackedWidget)
        if stack.isVisible() and stack.currentWidget() is not None
    ]


def page_steps(dialog) -> list[tuple[QWidget, dict]]:
    """Visible widgets of the current page with their step, in tour order."""
    pages = load()["pages"]
    steps = []
    for key in page_keys(dialog):
        for step in pages.get(key) or []:
            widget = dialog.findChild(QWidget, step["widget"])
            if widget is not None and widget.isVisible() and (step.get("text") or widget.toolTip()):
                steps.append((widget, step))
    return steps


def start(dialog):
    for tour in dialog.findChildren(Tour):
        tour.close()
    steps = page_steps(dialog)
    if not steps:
        dialog.log_message("No guided help for this page yet.")
        return None
    tour = Tour(dialog, steps)
    tour.show_step(0)
    return tour


class Tour(QFrame):
    """A banner beside one widget at a time, with an outline around that widget."""

    def __init__(self, dialog, steps):
        super().__init__(dialog)
        self.dialog, self.steps, self.index = dialog, steps, 0
        self.setObjectName("tooltipTour")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setStyleSheet("#tooltipTour { background: palette(window); "
                           "border: 1px solid palette(highlight); border-radius: 4px; }")
        self.outline = QFrame(dialog)
        self.outline.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.outline.setStyleSheet("border: 2px solid palette(highlight); border-radius: 3px;")

        self.title, self.text, self.counter = QLabel(), QLabel(), QLabel()
        self.text.setWordWrap(True)
        self.text.setOpenExternalLinks(True)
        buttons = QHBoxLayout()
        buttons.addWidget(self.counter)
        buttons.addStretch()
        self.back, self.next, self.help, self.done = (QToolButton(text=t) for t in ("<", ">", "?", "✕"))
        for button in (self.back, self.next, self.help, self.done):
            button.setAutoRaise(True)
            buttons.addWidget(button)
        layout = QVBoxLayout(self)
        layout.addWidget(self.title)
        layout.addWidget(self.text)
        layout.addLayout(buttons)

        self.back.clicked.connect(lambda: self.show_step(self.index - 1))
        self.next.clicked.connect(lambda: self.show_step(self.index + 1))
        self.help.clicked.connect(self._open_help)
        self.done.clicked.connect(self.close)
        dialog.tabWidget_steps.currentChanged.connect(self.close)
        for stack in dialog.findChildren(QStackedWidget):
            stack.currentChanged.connect(self.close)
        dialog.installEventFilter(self)

    def show_step(self, index):
        self.index = max(0, min(index, len(self.steps) - 1))
        widget, step = self.steps[self.index]
        self.title.setText(f"<b>{step['title']}</b>")
        self.text.setText(step.get("text") or widget.toolTip())
        self.counter.setText(f"{self.index + 1}/{len(self.steps)}")
        self.back.setEnabled(self.index > 0)
        self.next.setEnabled(self.index < len(self.steps) - 1)
        self.help.setEnabled(bool(self._help_url()))
        self.help.setToolTip("Open the help page" if self._help_url() else "Help page not available yet")
        self._place()
        self.outline.show()
        self.show()
        self.raise_()

    def _help_url(self):
        base = load().get("help_url") or ""
        return base + self.steps[self.index][1].get("help", "") if base else ""

    def _open_help(self):
        QDesktopServices.openUrl(QUrl(self._help_url()))

    def _place(self):
        widget = self.steps[self.index][0]
        target = QRect(widget.mapTo(self.dialog, QPoint(0, 0)), widget.size())
        self.outline.setGeometry(target.adjusted(-3, -3, 3, 3))
        self.outline.raise_()
        width = max(160, min(360, self.dialog.width() - 32))
        self.setFixedWidth(width)
        height = self.layout().totalHeightForWidth(width)
        if height < 0:
            height = self.sizeHint().height()
        y = target.bottom() + 8
        if y + height > self.dialog.height() - 8:
            y = max(8, target.top() - height - 8)
        x = min(max(16, target.left()), self.dialog.width() - width - 16)
        self.setGeometry(x, y, width, height)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Resize:
            self._place()
        elif event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Escape:
            self.close()
            return True
        return False

    def closeEvent(self, event):
        self.dialog.removeEventFilter(self)
        self.outline.deleteLater()
        super().closeEvent(event)
