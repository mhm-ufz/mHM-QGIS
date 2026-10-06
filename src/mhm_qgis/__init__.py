"""QGIS plugin entry point for mhm_qgis."""

from ._version import __version__

__all__ = ["__version__", "classFactory"]


# noinspection PyPep8Naming
def classFactory(iface):  # pylint: disable=invalid-name
    """Load mhm_qgis class from file mhm_qgis.

    :param iface: A QGIS interface instance.
    :type iface: QgsInterface
    """

    parent = iface.mainWindow() if iface is not None else None
    try:
        from .dependency_bootstrap import ensure_qgis_runtime_dependencies

        ensure_qgis_runtime_dependencies(parent=parent, prompt=True)
    except Exception as exc:
        try:
            from qgis.PyQt.QtWidgets import QMessageBox

            QMessageBox.warning(
                parent,
                "mHM QGIS Dependency Check",
                f"mHM QGIS dependency check could not run.\n\n{exc}",
            )
        except Exception:
            print(f"mHM QGIS dependency check could not run: {exc}")  # noqa: T201 -- no GUI yet

    from .mhm_qgis import MhmQgis

    return MhmQgis(iface)
