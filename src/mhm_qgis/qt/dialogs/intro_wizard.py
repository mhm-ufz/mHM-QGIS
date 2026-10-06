"""The introduction wizard, built from `intro/questions.yml` (no .ui form)."""
from __future__ import annotations

import html

from qgis.PyQt.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QLabel,
    QRadioButton,
    QVBoxLayout,
    QWizard,
    QWizardPage,
)

from ... import intro


class IntroWizard(QWizard):
    """One page per schema page, then a summary; `answers()` returns the choices."""

    def __init__(self, questions, version, answers=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("mHM introduction")
        self.questions, self.version = questions, version
        self.inputs = {}  # page id -> radio QButtonGroup or {choice id: QCheckBox}
        saved = answers or {}
        for page in questions["pages"]:
            self.addPage(self._page(page, saved.get(page["id"])))
        self.summary = QLabel(wordWrap=True)
        self.addPage(_SummaryPage(self))

    def _page(self, spec, saved):
        page = QWizardPage()
        page.setTitle(spec["title"])
        layout = QVBoxLayout(page)
        if spec.get("text"):
            layout.addWidget(QLabel(spec["text"], wordWrap=True))
        if spec["kind"] == "choice":
            group = QButtonGroup(page)
            for index, choice in enumerate(spec["choices"]):
                button = QRadioButton(choice["label"])
                button.setObjectName(choice["id"])
                button.setChecked(choice["id"] == saved if saved else index == 0)
                group.addButton(button)
                layout.addWidget(button)
            self.inputs[spec["id"]] = group
        elif spec["kind"] == "multiple":
            boxes = {}
            for choice in spec["choices"]:
                box = QCheckBox(choice["label"])
                box.setChecked(choice["id"] in saved if saved is not None else bool(choice.get("default")))
                boxes[choice["id"]] = box
                layout.addWidget(box)
            self.inputs[spec["id"]] = boxes
        layout.addStretch()
        return page

    def answers(self) -> dict:
        result = {}
        for page_id, widget in self.inputs.items():
            if isinstance(widget, dict):
                result[page_id] = [choice for choice, box in widget.items() if box.isChecked()]
            elif widget.checkedButton() is not None:
                result[page_id] = widget.checkedButton().objectName()
        return result

    def nextId(self):
        pages = self.questions["pages"]
        answers = self.answers()
        for index in range(self.currentId() + 1, len(pages)):
            if intro.visible(pages[index], answers):
                return index
        return len(pages) if self.currentId() < len(pages) else -1

    def _fill_summary(self):
        answers = self.answers()
        effects = intro.effects(self.questions, answers, self.version)
        lines = intro.summary(self.version, effects) + intro.tips(self.questions, answers, self.version)
        self.summary.setText("<ul>" + "".join(f"<li>{html.escape(line)}</li>" for line in lines) + "</ul>"
                             "<p>Finish applies these settings; you can change every one later.</p>")


class _SummaryPage(QWizardPage):
    def __init__(self, wizard):
        super().__init__()
        self.setTitle("Your setup")
        QVBoxLayout(self).addWidget(wizard.summary)

    def initializePage(self):
        self.wizard()._fill_summary()
