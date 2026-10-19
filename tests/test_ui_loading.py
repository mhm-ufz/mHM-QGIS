"""Designer sources compile on either Qt binding without generated modules."""
import importlib.util
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize(("version", "api", "plot_api"), [
    ("5.15.14", "pyqt5", "PyQt5"), ("6.8.3", "pyqt6", "PyQt6"),
])
def test_binding_selection_uses_qgis_qt_version(monkeypatch, version, api, plot_api):
    from mhm_qgis.dependency_bootstrap import configure_qtpy_api

    monkeypatch.setitem(sys.modules, "qgis.PyQt.QtCore", SimpleNamespace(QT_VERSION_STR=version))
    monkeypatch.setenv("QT_API", "pyside6")
    monkeypatch.setenv("PYQTGRAPH_QT_LIB", "PySide6")
    assert configure_qtpy_api() == api
    assert os.environ["PYQTGRAPH_QT_LIB"] == plot_api


def test_launcher_requires_real_qgis():
    result = subprocess.run(
        [sys.executable, "-c", ("import sys; sys.modules['qgis'] = None; "
         "from mhm_qgis_runtime.app import main; main([])")],
        env=dict(os.environ, PYTHONPATH=str(Path(__file__).parents[1] / "src")),
        check=False, capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 1
    assert "QGIS runtime libraries are required" in result.stderr
    assert "Traceback" not in result.stderr


def test_launcher_opens_and_closes_the_real_application():
    """`main` owns its QgsApplication here, unlike every in-process GUI test."""
    pytest.importorskip("qgis.core", reason="QGIS runtime libraries are required")
    script = '''
import sys
from qgis.PyQt.QtCore import QTimer
from mhm_qgis.qt.dialogs.mhm_qgis_main import MhmQgisDialog
from mhm_qgis_runtime.app import main
show_ = MhmQgisDialog.show
def show_once(self):
    self.open_map_canvas()
    QTimer.singleShot(0, self.reject)
    return show_(self)
MhmQgisDialog.show = show_once
sys.exit(main([]))
'''
    paths = (str(Path(__file__).parents[1] / "src"), os.environ.get("PYTHONPATH", ""))
    result = subprocess.run(
        [sys.executable, "-c", script],
        env=dict(os.environ, QT_QPA_PLATFORM="offscreen",
                 PYTHONPATH=os.pathsep.join(filter(None, paths))),
        check=False, capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stderr[-3000:]
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize("binding", ["PyQt5", "PyQt6"])
def test_designer_forms_and_icons(binding):
    if importlib.util.find_spec(binding) is None:
        pytest.skip(f"{binding} is not installed")
    forms = Path(__file__).parents[1] / "src/mhm_qgis/qt/ui/forms"
    result = subprocess.run(
        [sys.executable, "-c", '''
import importlib, io, sys
from pathlib import Path
from xml.etree import ElementTree as ET
uic = importlib.import_module(sys.argv[1] + ".uic")
widgets = importlib.import_module(sys.argv[1] + ".QtWidgets")
app = widgets.QApplication([])
for path in Path(sys.argv[2]).glob("*.ui"):
    tree = ET.parse(path)
    assert not tree.findall(".//resources/include"), path
    for icon in tree.findall(".//iconset/normaloff"):
        assert (path.parent / icon.text).is_file(), (path, icon.text)
    code = io.StringIO()
    uic.compileUi(str(path), code)
    compile(code.getvalue(), str(path), "exec")
    if tree.findall(".//customwidget"):
        continue  # Promoted QGIS widgets need the actual QGIS runtime.
    form, base = uic.loadUiType(str(path))
    dialog = base()
    form().setupUi(dialog)
    for node in tree.findall(".//widget"):
        if node.find("./property[@name='icon']") is not None:
            button = dialog.findChild(widgets.QPushButton, node.get("name"))
            assert button is not None and not button.icon().isNull(), path
''', binding, str(forms.resolve())],
        cwd=forms.parent, env=dict(os.environ, QT_QPA_PLATFORM="offscreen"),
        check=False, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
