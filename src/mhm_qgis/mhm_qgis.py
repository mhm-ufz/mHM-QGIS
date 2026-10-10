"""QGIS plugin implementation for mhm_qgis."""
from __future__ import annotations

import os
import sys

# QGIS reloads can accidentally import this file as top-level ``mhm_qgis`` if the
# plugin directory was previously added to sys.path. Make the module package-like
# so relative imports still resolve in that recovery path.
_PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
if not __package__:
    __package__ = __name__
    __path__ = [_PLUGIN_DIR]
    if globals().get("__spec__") is not None:
        __spec__.submodule_search_locations = __path__

_plugin_dir_key = os.path.normcase(os.path.abspath(_PLUGIN_DIR))
sys.path[:] = [
    path for path in sys.path
    if os.path.normcase(os.path.abspath(path or os.curdir)) != _plugin_dir_key
]

from qgis.PyQt.QtCore import QCoreApplication, QSettings, QTranslator
from qgis.PyQt.QtGui import QIcon

try:
    from qgis.PyQt.QtGui import QAction
except ImportError:  # Qt5
    from qgis.PyQt.QtWidgets import QAction


def classFactory(iface):  # pylint: disable=invalid-name
    """Return the plugin instance when QGIS imports this module directly."""
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
    return MhmQgis(iface)


class MhmQgis:
    """QGIS Plugin Implementation."""

    def __init__(self, iface):
        """Constructor.

        :param iface: An interface instance that will be passed to this class
            which provides the hook by which you can manipulate the QGIS
            application at run time.
        :type iface: QgsInterface
        """
        # Save reference to the QGIS interface
        self.iface = iface
        # initialize plugin directory
        self.plugin_dir = os.path.dirname(__file__)
        # initialize locale
        locale = QSettings().value("locale/userLocale")[0:2]
        locale_path = os.path.join(
            self.plugin_dir, "i18n", f"mhm_qgis_{locale}.qm"
        )

        if os.path.exists(locale_path):
            self.translator = QTranslator()
            self.translator.load(locale_path)
            QCoreApplication.installTranslator(self.translator)

        # Declare instance attributes
        self.actions = []
        self.menu = self.tr("&mHM QGIS")

        # Check if plugin was started the first time in current QGIS session
        # Must be set in initGui() to survive plugin reloads
        self.first_start = None

    # noinspection PyMethodMayBeStatic
    def tr(self, message):
        """Get the translation for a string using Qt translation API.

        We implement this ourselves since we do not inherit QObject.

        :param message: String for translation.
        :type message: str, QString

        :returns: Translated version of message.
        :rtype: QString
        """
        # noinspection PyTypeChecker,PyArgumentList,PyCallByClass
        return QCoreApplication.translate("mhm_qgis", message)

    def add_action(
        self,
        icon_path,
        text,
        callback,
        enabled_flag=True,
        add_to_menu=True,
        add_to_toolbar=True,
        status_tip=None,
        whats_this=None,
        parent=None,
    ):
        """Add a toolbar icon to the toolbar.

        :param icon_path: Path to the icon for this action. Can be a resource
            path (e.g. ':/plugins/foo/bar.png') or a normal file system path.
        :type icon_path: str

        :param text: Text that should be shown in menu items for this action.
        :type text: str

        :param callback: Function to be called when the action is triggered.
        :type callback: function

        :param enabled_flag: A flag indicating if the action should be enabled
            by default. Defaults to True.
        :type enabled_flag: bool

        :param add_to_menu: Flag indicating whether the action should also
            be added to the menu. Defaults to True.
        :type add_to_menu: bool

        :param add_to_toolbar: Flag indicating whether the action should also
            be added to the toolbar. Defaults to True.
        :type add_to_toolbar: bool

        :param status_tip: Optional text to show in a popup when mouse pointer
            hovers over the action.
        :type status_tip: str

        :param parent: Parent widget for the new action. Defaults None.
        :type parent: QWidget

        :param whats_this: Optional text to show in the status bar when the
            mouse pointer hovers over the action.

        :returns: The action that was created. Note that the action is also
            added to self.actions list.
        :rtype: QAction
        """

        icon = QIcon(icon_path)
        action = QAction(icon, text, parent)
        action.triggered.connect(callback)
        action.setEnabled(enabled_flag)

        if status_tip is not None:
            action.setStatusTip(status_tip)

        if whats_this is not None:
            action.setWhatsThis(whats_this)

        if add_to_toolbar:
            # Adds plugin icon to Plugins toolbar
            self.iface.addToolBarIcon(action)

        if add_to_menu:
            self.iface.addPluginToMenu(self.menu, action)

        self.actions.append(action)

        return action

    def initGui(self):
        """Create the menu entries and toolbar icons inside the QGIS GUI."""

        icon_path = os.path.join(self.plugin_dir, "qt", "ui", "forms", "Resources", "icon.png")
        self.add_action(
            icon_path,
            text=self.tr("mHM QGIS"),
            callback=self.run,
            parent=self.iface.mainWindow(),
        )

        # will be set False in run()
        self.first_start = True

    def unload(self):
        """Removes the plugin menu item and icon from QGIS GUI."""
        dialog = getattr(self, "dlg", None)
        if dialog is not None:
            from .qt.controllers import post_context

            post_context.end_workspace(dialog)  # our docks leave QGIS's window; its layout returns
        for action in self.actions:
            self.iface.removePluginMenu(self.tr("&mHM QGIS"), action)
            self.iface.removeToolBarIcon(action)

    def run(self):
        """Run method that performs all the real work"""

        parent = self.iface.mainWindow() if self.iface is not None else None
        try:
            from .dependency_bootstrap import ensure_qgis_runtime_dependencies

            dependency_result = ensure_qgis_runtime_dependencies(
                parent=parent,
                prompt=False,
            )
        except Exception as exc:
            from qgis.PyQt.QtWidgets import QMessageBox

            QMessageBox.warning(
                parent,
                "mHM QGIS Dependency Check",
                f"mHM QGIS dependency check could not run.\n\n{exc}",
            )
            return

        if not dependency_result.ok:
            from qgis.PyQt.QtWidgets import QMessageBox

            missing = (
                dependency_result.failed_requirements
                or [dependency.requirement for dependency in dependency_result.missing]
            )
            QMessageBox.warning(
                parent,
                "mHM QGIS Python Dependencies Missing",
                "mHM QGIS cannot open until these Python packages are available "
                "in the QGIS Python environment:\n\n"
                + "\n".join(missing),
            )
            return

        from .qt.dialogs.mhm_qgis_main import MhmQgisDialog

        old_dialog = getattr(self, "dlg", None)
        if old_dialog is not None and old_dialog._closing:
            if old_dialog.isVisible():
                return
            old_dialog.deleteLater()
            self.first_start = True

        # Create the dialog with elements (after translation) and keep reference
        # Only create GUI ONCE in callback, so that it will only load when the plugin is started
        if self.first_start:
            self.first_start = False
            self.dlg = MhmQgisDialog(iface=self.iface)

        # Non-modal, so Desktop's map canvas stays usable beside the dialog.
        self.dlg.show()
        self.dlg.raise_()
        self.dlg.activateWindow()
