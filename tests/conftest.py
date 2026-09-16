"""GUI tests use real QGIS; core tests need no GUI installation."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def pytest_sessionstart(session):
    try:
        from qgis.core import QgsApplication
    except ImportError:
        return
    from mhm_qgis.dependency_bootstrap import configure_qtpy_api

    configure_qtpy_api()
    app = QgsApplication([], True)
    app.initQgis()
    session._qgis_application = app


def pytest_sessionfinish(session):
    app = getattr(session, "_qgis_application", None)
    if app is not None:
        from qgis.PyQt.QtCore import QCoreApplication, QEvent
        for widget in app.topLevelWidgets():
            widget.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.exitQgis()
