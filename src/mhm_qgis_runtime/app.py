"""Open the GUI using installed QGIS libraries, without QGIS Desktop."""
import argparse
import sys


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, prog="mhm-qgis")
    parser.parse_args(argv)
    try:
        from qgis.core import QgsApplication, QgsProject
        from qgis.PyQt.QtCore import QCoreApplication, QEvent, QEventLoop, Qt
    except ImportError as error:
        parser.exit(1, f"QGIS runtime libraries are required. Run with QGIS's Python environment.\n{error}\n")

    from mhm_qgis.dependency_bootstrap import configure_qtpy_api
    from mhm_qgis.qgis_bridge.processing import initialize

    configure_qtpy_api()
    app = QgsApplication.instance()
    owns_app = app is None
    if owns_app:
        app = QgsApplication([], True)
        app.initQgis()
    try:
        initialize()
        from mhm_qgis.qt.dialogs.mhm_qgis_main import MhmQgisDialog

        dialog = MhmQgisDialog()
        # not exec(): a modal dialog would stay above the map window
        loop = QEventLoop()
        dialog.finished.connect(loop.quit)
        # a normal window: a parentless dialog is kept above the app's other windows
        dialog.setWindowFlags(Qt.WindowType.Window)
        dialog.show()
        loop.exec()
        dialog.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        return 0
    finally:
        if owns_app:
            QgsProject.instance().clear()
            app.exitQgis()


if __name__ == "__main__":
    sys.exit(main())
