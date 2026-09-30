# -*- coding: utf-8 -*-
"""Main dialog: construction, lifecycle, logging, and the names other packages call.

Widget behaviour lives in `qt/controllers` (`main`, `grid`, `forcing`,
`categorical`, `input_state`, ...), the wiring in `qt/bindings/main.py`. The
one-line methods below exist because workflows, display bridges, `vSpecific`
or other dialogs hold this object and call them by name.
"""

import sys

from qgis.core import QgsProject
from qgis.PyQt import QtCore
from qgis.PyQt.QtWidgets import QDialog, QMessageBox

from ...core.handlers.store.paths import workspace_folder
from ...qgis_bridge import layers as qgis_layers
from ...qgis_bridge.display import meteo as meteo_display
from ...qgis_bridge.display import output as output_display
from ..bindings.main import bind as bind_main_form
from ..controllers import categorical, grid, input_state, workflow_state
from ..controllers import main as main_controller
from ..threads.task_coordinator import TaskCoordinator
from ..ui import load_form
from ..workflows.morphology import MorphologyTaskBridge
from .map_canvas import MapCanvasWindow
from .project_terminal import ProjectTerminalDialog
from .thread_display import ThreadDisplayDialog

Ui_MhmQgisDialog = load_form("mhm_qgis_main.ui")


class MhmQgisDialog(QDialog, Ui_MhmQgisDialog):
    def __init__(self, parent=None, iface=None):
        """Constructor; `iface` is QGIS Desktop's interface, None when standalone."""
        super().__init__(parent)
        self.setupUi(self)
        self.iface = iface
        self.project_folder = None  # Active domain root, passed to core APIs.
        self.project_directory = None  # Outer folder shown in the project selector.

        # Per-domain form state; `controllers/input_state.clear` resets it.
        self._categorical_lookup_configs = {}
        self._categorical_ready_configs = {}
        self._categorical_modes = {}
        self._advanced_inputs = {}
        self._lai_input_config = {}
        self._land_cover_ready_source = ""
        self._domain_definition_mode = ""
        self._meteo_inputs = {}
        self._meteo_inspections = {}
        self._observation_inputs = {}
        self._observation_inspections = {}
        self._grid_l0_info = None
        self._grid_l2_metadata = None
        self._grid_l2_header = None
        self._l2_multiplier_user_selected = False
        self._preferred_l1_resolution = None
        self._preferred_l11_resolution = None

        # Save guards, see `connect_input_state_teardown_guards`.
        self._loading_input_state = False
        self._suspend_input_state_saves = False
        self._preserve_missing_layer_state = False

        # Windows and workers owned by this dialog.
        self._map_window = None
        self._terminal_dialog = None
        self._thread_display_dialog = None
        self._domain_delineator_dialog = None
        self._discharge_plot = None
        self._closing = False
        self._running_workflow = None  # See `controllers/workflow_state.running_key`.
        self._workflow_button_default_styles = {}
        self.task_coordinator = TaskCoordinator(self, max_threads=2)
        self.task_coordinator.log.connect(lambda _key, message: self.log_message(message))

        main_controller.configure_input_adapters(self)
        main_controller.configure_morphology_display(self)
        self.spinBox_L2ResolutionMultiplier.setMinimum(1)
        self.spinBox_L2ResolutionMultiplier.setMaximum(1_000_000)
        self.pushButton_executeAllMeteo.setToolTip(
            "Prepare meteorology forcing on the saved common L2 grid"
        )
        self.lineEdit_precipitationGlobDefinition.setText("*.nc")
        self.comboBox_mhmWorkType.setCurrentIndex(0)
        for index in (1, 2):
            self.comboBox_mhmWorkType.model().item(index).setEnabled(False)
        self.stackedWidget_configuration.setCurrentWidget(self.page_singleDomain)
        self.tabWidget_steps.setCurrentIndex(0)
        workflow_state.capture_button_styles(self)
        self.morphology_tasks = MorphologyTaskBridge(self)

        bind_main_form(self)
        self.connect_input_state_teardown_guards()
        project_crs = QgsProject.instance().crs()
        if project_crs.isValid():
            self.mProjectionSelectionWidget_crs.setCrs(project_crs)
        self.refresh_input_sources()
        self.update_gauged_outlet_count()
        meteo_display.refresh(self)
        output_display.refresh(self)
        grid.refresh_precipitation_folder(self)
        grid.refresh_grid_resolution_controls(self)

    def log_message(self, message):
        """Append to the UI log without feeding back into captured stdout."""
        try:
            console = getattr(sys, "__stdout__", None)
            if console is not None:
                print(message, file=console)
        except Exception:
            pass
        self.LogText.append(message)

    def check_prerequisites(self, needs_pour_points=False):
        """Check if the project folder and necessary layers are selected."""
        if not self.project_folder:
            QMessageBox.critical(
                self, "Missing Input", "Please select a project folder before proceeding.")
            return False
        if not self.input_combo("dem").currentLayer():
            QMessageBox.critical(self, "Missing Input",
                                 "Please select a DEM Raster Layer.")
            return False
        if needs_pour_points and not self.input_combo("pour_points").currentLayer():
            QMessageBox.critical(self, "Missing Input",
                                 "Please select a Pour Points Layer.")
            return False
        return True

    def log_selected_input_paths(self, action_name):
        """Print selected layer/file inputs for easier debugging from QGIS."""
        log = self.log_message
        log(f"\n--- Input selections before {action_name} ---")
        log(f"Project folder: {self.project_folder or '<not selected>'}")
        for label, kind in (("DEM", "dem"), ("Pour points", "pour_points")):
            layer = self.input_combo(kind).currentLayer()
            log(f"{label}: {layer.name()} | {layer.source()}" if layer else f"{label}: <not selected>")
        for kind, label in (("lc", "Land cover"), ("soil", "Soil"), ("geology", "Geology"), ("lai", "LAI")):
            config = categorical.categorical_source_config(self, kind) or (
                self._lai_input_config if kind == "lai" else {}
            )
            mode = categorical.categorical_input_mode(self, kind) or "<not selected>"
            log(f"{label} input type: {mode} | {config.get('input_path') or '<no source>'}")
            if config.get("lookup_table"):
                log(f"{label} lookup: {config['lookup_table']} | "
                    f"{config['mapping_field']} -> {config['class_field']}")
        log(f"Precipitation folder: {grid.precipitation_folder(self) or '<not selected>'}")

    # --- Names other packages call ----------------------------------------

    def input_combo(self, kind):
        return self._input_adapters.get(kind)

    @QtCore.pyqtSlot()
    def refresh_input_sources(self):
        main_controller.refresh_input_sources(self)

    def selected_input_file_paths(self):
        return main_controller.selected_input_file_paths(self)

    def update_gauged_outlet_count(self, layer=None):
        return main_controller.update_gauged_outlet_count(self, layer)

    def get_crs(self):
        return self.mProjectionSelectionWidget_crs.crs()

    def selected_meteo_crs(self):
        """Return the processing CRS in a form accepted by pyproj."""
        return qgis_layers.crs_string(self.get_crs())

    def grid_level_headers(self):
        return grid.grid_level_headers(self)

    def current_l1_resolution(self):
        return grid.current_l1_resolution(self)

    def current_l11_resolution(self):
        return grid.current_l11_resolution(self)

    def current_grid_unit(self):
        return grid.current_grid_unit(self)

    def categorical_input_mode(self, kind):
        return categorical.categorical_input_mode(self, kind)

    def categorical_lookup_config(self, kind):
        config = self._categorical_lookup_configs.get(kind)
        return dict(config) if config else None

    def categorical_source_config(self, kind):
        return categorical.categorical_source_config(self, kind)

    def lai_netcdf_config(self):
        return dict(self._lai_input_config)

    def uses_advanced_categorical_input(self, kind):
        return categorical.uses_advanced_categorical_input(self, kind)

    def save_input_state(self):
        input_state.save(self)

    def load_input_state(self):
        input_state.load(self)

    # --- Input-state teardown guards --------------------------------------

    def connect_input_state_teardown_guards(self):
        """Avoid overwriting saved layer selections during QGIS teardown."""
        QtCore.QCoreApplication.instance().aboutToQuit.connect(self.suspend_input_state_saves)
        project = QgsProject.instance()
        project.layersWillBeRemoved.connect(self.preserve_missing_layer_state)
        project.layersRemoved.connect(self.release_missing_layer_state_preservation)
        project.cleared.connect(self.release_missing_layer_state_preservation)

    def suspend_input_state_saves(self, *args):
        """Stop input-state writes once QGIS is shutting down."""
        self._preserve_missing_layer_state = True
        self._suspend_input_state_saves = True

    def preserve_missing_layer_state(self, *args):
        """Keep saved layer entries if QGIS removes layers from combo boxes."""
        self._preserve_missing_layer_state = True

    def release_missing_layer_state_preservation(self, *args):
        """Stop preserving empty layer selections after a removal batch ends."""
        if not self._suspend_input_state_saves:
            QtCore.QTimer.singleShot(0, self.clear_missing_layer_state_preservation)

    def clear_missing_layer_state_preservation(self):
        """Return to normal layer-state saving after project layer removal."""
        if not self._suspend_input_state_saves:
            self._preserve_missing_layer_state = False

    # --- Lifecycle --------------------------------------------------------

    def done(self, result):
        """Cancel active work before closing through Cancel or Escape."""
        if self._closing:
            return
        terminal = self._terminal_dialog
        running_key = workflow_state.running_key(self)
        busy = running_key is not None or self.task_coordinator.is_busy()
        if int(result) == int(QDialog.DialogCode.Rejected) and (
            busy or (terminal is not None and terminal.is_running())
        ):
            self._closing = True
            self._close_result = result
            self.task_coordinator.cancel_all()
            if terminal is not None:
                terminal.stop()
            QtCore.QTimer.singleShot(0, self._finish_cancelled_close)
            return
        terminal_busy = terminal is not None and terminal.command_running()
        if busy or terminal_busy:
            spec = workflow_state.specs(self).get(
                running_key or ("run_mhm" if terminal_busy else ""), {}
            )
            QMessageBox.warning(
                self,
                spec.get("label", "Morphology Processing"),
                (
                    f"{spec.get('label', 'Morphology processing')} is still running. "
                    "Wait for it to finish before closing mHM QGIS."
                ),
            )
            return
        super().done(result)

    def _finish_cancelled_close(self):
        terminal = self._terminal_dialog
        if self.task_coordinator.is_busy() or (terminal is not None and terminal.is_running()):
            QtCore.QTimer.singleShot(50, self._finish_cancelled_close)
            return
        if self._domain_delineator_dialog is not None:
            self._domain_delineator_dialog.close()
        super().done(self._close_result)

    # --- Owned windows ----------------------------------------------------

    def map_canvas_window(self):
        """Return the standalone map window, created on first use."""
        if self._map_window is None:
            self._map_window = MapCanvasWindow(self)
        return self._map_window

    def open_map_canvas(self, checked=False):
        """Focus Desktop's map canvas, or show the standalone map window."""
        if self.iface is not None:
            self.iface.mainWindow().raise_()
            self.iface.mainWindow().activateWindow()
            self.iface.mapCanvas().setFocus()
            return self.iface.mapCanvas()
        window = self.map_canvas_window()
        window.show()
        window.raise_()
        window.activateWindow()
        return window

    def map_displayed(self, when=None):
        """Show the standalone map with the displayed layer's time; Desktop needs nothing."""
        if self.iface is None:
            window = self.map_canvas_window()
            window.set_time(when)
            window.show()

    def open_thread_display(self, checked=False):
        """Show the persistent task monitor for plugin preprocessing."""
        if self._thread_display_dialog is None:
            self._thread_display_dialog = ThreadDisplayDialog(self.task_coordinator, self)
        self._thread_display_dialog.show()
        self._thread_display_dialog.raise_()
        self._thread_display_dialog.activateWindow()
        return self._thread_display_dialog

    def open_project_terminal(self, checked=False):
        """Open the persistent terminal in the plugin-owned workspace."""
        if not self.project_folder:
            QMessageBox.warning(
                self,
                "Project Folder Required",
                "Select a project folder before opening the terminal.",
            )
            return None
        if self._terminal_dialog is None:
            self._terminal_dialog = ProjectTerminalDialog(self)
        self._terminal_dialog.show_for_directory(workspace_folder(self.project_folder))
        return self._terminal_dialog
