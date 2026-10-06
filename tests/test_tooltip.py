"""The Help tour: its schema names real widgets, and it walks the visible page."""
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
import yaml

SRC = Path(__file__).parents[1] / "src" / "mhm_qgis"


def test_schema_names_widgets_that_exist_in_the_form():
    schema = yaml.safe_load((SRC / "qt/tooltip/tooltips.yml").read_text(encoding="utf-8"))
    form = ET.parse(SRC / "qt/ui/forms/mhm_qgis_main.ui").getroot()
    names = {widget.get("name") for widget in form.iter("widget")}
    assert set(schema) == {"version", "help_url", "pages"}
    for page, steps in schema["pages"].items():
        assert page in names, page
        widgets = [step["widget"] for step in steps]
        assert len(widgets) == len(set(widgets)), page
        for step in steps:
            assert set(step) <= {"widget", "title", "text", "help"}, step
            assert step["widget"] in names, (page, step["widget"])
            assert step["title"], step


def test_help_walks_the_visible_page_and_esc_only_ends_the_tour():
    pytest.importorskip("qgis.core")
    from qgis.PyQt.QtCore import QCoreApplication, QEvent, Qt
    from qgis.PyQt.QtTest import QTest

    from mhm_qgis.qt import tooltip
    from mhm_qgis.qt.dialogs.mhm_qgis_main import MhmQgisDialog

    dialog = MhmQgisDialog()
    dialog.show()
    dialog.tabWidget_steps.setCurrentWidget(dialog.tab)
    pages = tooltip.load()["pages"]

    dialog.Closure.helpRequested.emit()
    tour = dialog.findChild(tooltip.Tour)
    assert tour.isVisible()
    assert pages["tab"][0]["title"] in tour.title.text()
    assert not tour.back.isEnabled()
    assert not tour.help.isEnabled()  # no help_url yet
    steps = [step["widget"] for _widget, step in tour.steps]
    assert steps[:len(pages["tab"])] == [step["widget"] for step in pages["tab"]]
    assert pages["page_singleDomain"][0]["widget"] in steps  # the visible stacked page follows
    tour.next.click()
    assert tour.index == 1
    assert tour.back.isEnabled()

    dialog.checkBox_enableFolderSearch.hide()
    tour = tooltip.start(dialog)
    assert "checkBox_enableFolderSearch" not in [step["widget"] for _widget, step in tour.steps]

    QTest.keyClick(dialog, Qt.Key.Key_Escape)
    assert not tour.isVisible()
    assert dialog.isVisible()

    tour = tooltip.start(dialog)
    dialog.tabWidget_steps.setCurrentWidget(dialog.tab_meteo)
    assert not tour.isVisible()

    dialog.close()
    dialog.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
