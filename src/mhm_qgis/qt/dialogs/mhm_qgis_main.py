# -*- coding: utf-8 -*-
"""Dialog and UI wiring for the mhm_qgis QGIS plugin."""

import json
import os
import sys
import traceback
from pathlib import Path

# QGIS and PyQt imports
from qgis.core import QgsApplication, QgsMapLayer, QgsProject, QgsRasterLayer
from qgis.PyQt import QtCore
from qgis.PyQt.QtWidgets import QDialog, QMessageBox


from ..threads.meteo_workflow import MeteorologyWorkflowWorker
from ..reporting import error_detail
from ...core.grid import (
    Extent,
    Grid,
    ceil_cellsize,
    format_resolution,
    header_bounds,
    header_for_model_level,
    is_geographic_unit,
    l0_header_from_l2,
    resolution_in_crs,
    target_grid_from_header,
    validate_grid_headers,
)
from .input_selection import InputComboAdapter
from ...core.executions import meteo as meteo_execution
from ...core.executions.morphology import reset as morphology_reset
from ...core.executions.morphology import setup as morphology_setup
from ...core.handlers.state import processing as processing_state
from ...core.handlers.state import settings as project_settings
from ...core.handlers.state.meteo_outputs import MeteorologyOutputState
from ...core.executions.meteo import MeteorologyRun
from ...core.morphology.hydrology.outlets import StationIdError
from ...core.handlers.store.layout import (
    input_state_path as project_input_state_path,
    morph_folder,
)
from ...core.handlers.store.paths import (
    data_folder, temp_morphology_folder, output_folder, restart_folder, workspace_folder,
)
from ...qt.bindings.main import bind as bind_main_form
from ...qt.controllers import main as main_controller
from ...qgis_bridge import layers as qgis_layers
from ...qgis_bridge.layers import map_layer_filters
from .project_terminal import ProjectTerminalDialog
from ..threads.task_coordinator import TaskCoordinator
from .thread_display import ThreadDisplayDialog
from ..objects.morphology_tasks import MorphologyTaskBridge
from ..ui import load_form

Ui_MhmQgisDialog = load_form("mhm_qgis_main.ui")


class MhmQgisDialog(QDialog, Ui_MhmQgisDialog):
    def __init__(self, parent=None):
        """Constructor."""

        super(MhmQgisDialog, self).__init__(parent)
        self.setupUi(self)
        self._categorical_lookup_configs = {}
        self._categorical_modes = {}
        self._advanced_inputs = {}
        self._land_cover_ready_source = ""
        self._categorical_ready_configs = {}
        self._lai_input_config = {}
        self._domain_definition_mode = ""
        self._input_adapters = {}
        self.configure_input_adapters()
        self.configure_morphology_display()

        # --- Filter map layer combo boxes to show only relevant layer types ---
        self.input_combo("dem").setFilters(map_layer_filters("RasterLayer"))
        self.input_combo("pour_points").setFilters(map_layer_filters("VectorLayer"))

        # Set filters for new layer combo boxes (both vector and raster allowed)
        self.input_combo("soil").setFilters(
            map_layer_filters("RasterLayer", "VectorLayer")
        )
        self.input_combo("land_cover").setFilters(
            map_layer_filters("RasterLayer", "VectorLayer")
        )
        self.input_combo("geology").setFilters(
            map_layer_filters("RasterLayer", "VectorLayer")
        )
        self.input_combo("lai").setFilters(
            map_layer_filters("RasterLayer", "VectorLayer")
        )
        self.configure_input_layer_combo_boxes()

        # --- Instance attributes for managing file paths ---
        self.project_folder = None  # Active domain root, passed to core APIs.
        self.project_directory = None  # Outer folder shown in the project selector.
        self._meteo_inputs = {}
        self._observation_inputs = {}
        self.temp_morphology_folder = None  # Subfolder for geometry outputs
        self._loading_input_state = False
        self._suspend_input_state_saves = False
        self._preserve_missing_layer_state = False
        self._grid_l0_info = None
        self._grid_l2_metadata = None
        self._grid_l2_header = None
        self._l2_multiplier_user_selected = False
        self._preferred_l1_resolution = None
        self._preferred_l11_resolution = None
        self._meteo_inspections = {}
        self._observation_inspections = {}
        self._pending_meteo_run = None
        self._terminal_dialog = None
        self._thread_display_dialog = None
        self.task_coordinator = TaskCoordinator(self, max_threads=2)
        self._domain_delineator_dialog = None
        self._morphology_workflow_threads = {}
        self._morphology_workflow_workers = {}
        self._closing = False
        self._workflow_button_default_styles = {}
        self.spinBox_L2ResolutionMultiplier.setMinimum(1)
        self.spinBox_L2ResolutionMultiplier.setMaximum(1_000_000)
        if self.spinBox_L2ResolutionMultiplier.value() < 1:
            self.spinBox_L2ResolutionMultiplier.setValue(1)
        self.pushButton_executeAllMeteo.setToolTip(
            "Prepare meteorology forcing on the saved common L2 grid"
        )
        self.lineEdit_precipitationGlobDefinition.setText("*.nc")
        self.comboBox_mhmWorkType.setCurrentIndex(0)
        for index in (1, 2):
            self.comboBox_mhmWorkType.model().item(index).setEnabled(False)
        self.stackedWidget_configuration.setCurrentWidget(self.page_singleDomain)
        self.tabWidget_steps.setCurrentIndex(0)
        self._capture_workflow_button_default_styles()

        self.morphology_tasks = MorphologyTaskBridge(self)

        # --- Connect signals and slots ---
        bind_main_form(self)
        self.refresh_input_sources()
        self.refresh_meteo_display()
        self.refresh_output_display()
        self.refresh_meteo_folder_sources()
        self.refresh_grid_resolution_controls()

    def log_message(self, message):
        """Append to the UI log without feeding back into captured stdout."""
        try:
            console = getattr(sys, "__stdout__", None)
            if console is not None:
                print(message, file=console)
        except Exception:
            pass
        self.LogText.append(message)
        QgsApplication.processEvents()

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

    def configure_input_adapters(self, *args, **kwargs):
        return main_controller.configure_input_adapters(self, *args, **kwargs)

    def input_combo(self, *args, **kwargs):
        return main_controller.input_combo(self, *args, **kwargs)

    def refresh_meteo_display(self):
        """Re-read the prepared forcing and re-range the display controls."""
        from ...qgis_bridge.display import meteo as meteo_display

        meteo_display.refresh(self)

    def refresh_morphology_date_control(self):
        return main_controller.refresh_morphology_date_control(self)

    def display_selected_morphology_layer(self):
        return main_controller.display_selected_morphology_layer(self)

    def selected_version(self):
        return main_controller.selected_version(self)

    def refresh_mhm_executable(self):
        return main_controller.refresh_mhm_executable(self)

    def select_mhm_v6_executable(self):
        return main_controller.select_mhm_v6_executable(self)

    def handle_version_changed(self):
        return main_controller.handle_version_changed(self)

    def edit_namelists(self):
        return main_controller.edit_namelists(self)

    def run_mhm(self):
        return main_controller.run_mhm(self)

    def refresh_output_display(self):
        """Re-read the mHM output and re-range the display controls."""
        from ...qgis_bridge.display import output as output_display

        output_display.refresh(self)

    def configure_morphology_display(self, *args, **kwargs):
        return main_controller.configure_morphology_display(self, *args, **kwargs)

    def configure_input_layer_combo_boxes(self, *args, **kwargs):
        return main_controller.configure_input_layer_combo_boxes(self, *args, **kwargs)

    @QtCore.pyqtSlot()
    def refresh_input_sources(self, *args, **kwargs):
        return main_controller.refresh_input_sources(self, *args, **kwargs)

    def populate_pour_point_outlet_fields(self, *args, **kwargs):
        return main_controller.populate_pour_point_outlet_fields(self, *args, **kwargs)

    def selected_outlet_id_field(self, *args, **kwargs):
        return main_controller.selected_outlet_id_field(self, *args, **kwargs)

    def selected_outlet_ids(self, *args, **kwargs):
        return main_controller.selected_outlet_ids(self, *args, **kwargs)

    def selected_input_file_paths(self, *args, **kwargs):
        return main_controller.selected_input_file_paths(self, *args, **kwargs)

    @classmethod
    def _nested_existing_paths(cls, value):
        if isinstance(value, dict):
            for item in value.values():
                yield from cls._nested_existing_paths(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                yield from cls._nested_existing_paths(item)
        elif isinstance(value, str) and os.path.isfile(value):
            yield value

    def meteo_input_widgets(self, *args, **kwargs):
        return main_controller.meteo_input_widgets(self, *args, **kwargs)

    def refresh_meteo_folder_sources(self, *args, **kwargs):
        return main_controller.refresh_meteo_folder_sources(self, *args, **kwargs)

    def selected_meteo_folder(self, *args, **kwargs):
        return main_controller.selected_meteo_folder(self, *args, **kwargs)

    def selected_meteo_source(self, *args, **kwargs):
        return main_controller.selected_meteo_source(self, *args, **kwargs)

    @staticmethod
    def selected_folder_path(*args, **kwargs):
        return main_controller.selected_folder_path(*args, **kwargs)

    @staticmethod
    def _folder_combo_index(*args, **kwargs):
        return main_controller._folder_combo_index(*args, **kwargs)

    def _add_folder_combo_item(self, *args, **kwargs):
        return main_controller._add_folder_combo_item(self, *args, **kwargs)

    def browse_meteo_input_folder(self, *args, **kwargs):
        return main_controller.browse_meteo_input_folder(self, *args, **kwargs)

    def handle_meteo_input_changed(self, *args, **kwargs):
        return main_controller.handle_meteo_input_changed(self, *args, **kwargs)

    def selected_meteo_crs(self, *args, **kwargs):
        return main_controller.selected_meteo_crs(self, *args, **kwargs)

    def meteo_folder_spec(self, *args, **kwargs):
        return main_controller.meteo_folder_spec(self, *args, **kwargs)

    def selected_meteo_specs(self, *args, **kwargs):
        return main_controller.selected_meteo_specs(self, *args, **kwargs)

    def clear_precipitation_resolution_labels(self, *args, **kwargs):
        return main_controller.clear_precipitation_resolution_labels(self, *args, **kwargs)

    @staticmethod
    def _matching_input_index(*args, **kwargs):
        return main_controller._matching_input_index(*args, **kwargs)

    def browse_input_file(self, *args, **kwargs):
        return main_controller.browse_input_file(self, *args, **kwargs)

    def connect_optional_processor_button(self, *args, **kwargs):
        return main_controller.connect_optional_processor_button(self, *args, **kwargs)

    def handle_l2_multiplier_changed(self, *args, **kwargs):
        return main_controller.handle_l2_multiplier_changed(self, *args, **kwargs)

    def handle_model_input_changed(self, *args, **kwargs):
        return main_controller.handle_model_input_changed(self, *args, **kwargs)

    def refresh_grid_resolution_controls(self, *args, **kwargs):
        return main_controller.refresh_grid_resolution_controls(self, *args, **kwargs)

    def update_l0_resolution_from_dem(self, *args, **kwargs):
        return main_controller.update_l0_resolution_from_dem(self, *args, **kwargs)

    def filled_dem_resolution_info(self):
        """Read the effective DEM header; never start terrain processing here."""
        from ...core.grid.raster import dem_grid
        layer = self.input_combo("dem").currentLayer()
        crs = self.get_crs()
        if not layer or not crs or not crs.isValid():
            return None
        return dem_grid(layer.source(), qgis_layers.crs_string(layer.crs()),
                        qgis_layers.crs_string(crs), qgis_layers.crs_unit(crs))

    def update_l2_resolution_from_metadata(self, *args, **kwargs):
        return main_controller.update_l2_resolution_from_metadata(self, *args, **kwargs)

    def set_meteo_l2_grid_metadata(self, *args, **kwargs):
        return main_controller.set_meteo_l2_grid_metadata(self, *args, **kwargs)

    def prepare_meteo_l2_grid(self, precipitation_metadata=None):
        """Build the requested L2 grid used by meteorology processing."""
        self.update_l0_resolution_from_dem()
        if not self._grid_l0_info:
            raise ValueError("Filled DEM is required before preparing meteorology data.")
        x_resolution = float(self._grid_l0_info["exact_x_resolution"])
        y_resolution = float(self._grid_l0_info["exact_y_resolution"])
        if abs(x_resolution - y_resolution) > max(x_resolution, y_resolution, 1.0) * 1e-6:
            raise ValueError(
                "The filled DEM must have square L0 cells before preparing "
                f"meteorology data (x={x_resolution}, y={y_resolution})."
            )
        target_crs = self.get_crs()
        if target_crs is None or not target_crs.isValid():
            raise ValueError("Please set a valid processing CRS before preparing meteorology data.")
        from ...core.grid import header_bounds
        domain_bounds = header_bounds(self._grid_l0_info["header"])
        extent_source = self.input_combo("dem").currentLayer().source()

        crs_text = qgis_layers.crs_string(target_crs)
        unit = qgis_layers.crs_unit(target_crs)
        raw_resolution = None
        raw_extent = None
        raw_crs = None
        source_file = ""
        if precipitation_metadata is not None:
            converted = resolution_in_crs(
                precipitation_metadata,
                crs_text or None,
            )
            raw_resolution = (converted.x_resolution, converted.y_resolution)
            raw_extent = Extent.from_centres(
                precipitation_metadata.bounds,
                precipitation_metadata.x_resolution,
                precipitation_metadata.y_resolution,
            )
            raw_crs = precipitation_metadata.crs
            source_file = str(precipitation_metadata.files[0])
        grid = Grid.for_meteorology(
            self._grid_l0_info["header"],
            (domain_bounds,),
            self.spinBox_L2ResolutionMultiplier.value(),
            crs=crs_text,
            unit=unit,
            raw_l2_resolution=raw_resolution,
            raw_l2_extent=raw_extent,
            raw_l2_crs=raw_crs,
            gap_limit_fraction=project_settings.read(self.project_folder)["grid_alignment_gap_limit"],
        )
        target = target_grid_from_header(grid.l2_header, crs_text or None)
        metadata = grid.metadata(
            extent_source=os.path.basename(str(extent_source).split("|", 1)[0]),
            source_file=source_file,
        )
        if target.sample_lon is not None:
            metadata["wgs84_bounds"] = (
                float(target.sample_lon.min()),
                float(target.sample_lon.max()),
                float(target.sample_lat.min()),
                float(target.sample_lat.max()),
            )
        if raw_resolution:
            metadata.update({
                "raw_meteo_resolution": sum(raw_resolution) / 2,
                "raw_meteo_x_resolution": raw_resolution[0],
                "raw_meteo_y_resolution": raw_resolution[1],
            })
        self.log_message(
            "Meteo L2 grid: "
            f"{format_resolution(metadata.get('l2_resolution'), metadata.get('l2_unit', ''))} "
            f"{metadata.get('l2_unit', '')} "
            f"({metadata.get('l2_ratio_to_l0')} x L0)."
        )
        if grid.requires_l2_resampling and grid.alignment_gap:
            self.log_message(
                "Meteo L2 grid requires resampling: alignment gap "
                f"x={grid.alignment_gap[0]:g}, y={grid.alignment_gap[1]:g} "
                f"exceeds {grid.alignment_gap_limit:g}."
            )
        return grid, target, metadata

    def refresh_l1_l11_resolution_options(self, *args, **kwargs):
        return main_controller.refresh_l1_l11_resolution_options(self, *args, **kwargs)

    def handle_l1_resolution_changed(self, *args, **kwargs):
        return main_controller.handle_l1_resolution_changed(self, *args, **kwargs)

    def disable_l1_l11_resolution_options(self, *args, **kwargs):
        return main_controller.disable_l1_l11_resolution_options(self, *args, **kwargs)

    def disable_l11_resolution_options(self, *args, **kwargs):
        return main_controller.disable_l11_resolution_options(self, *args, **kwargs)


    def update_l1_resolution_label(self, *args, **kwargs):
        return main_controller.update_l1_resolution_label(self, *args, **kwargs)

    def update_l11_resolution_label(self, *args, **kwargs):
        return main_controller.update_l11_resolution_label(self, *args, **kwargs)

    def current_l0_resolution(self, *args, **kwargs):
        return main_controller.current_l0_resolution(self, *args, **kwargs)

    def current_l2_resolution(self, *args, **kwargs):
        return main_controller.current_l2_resolution(self, *args, **kwargs)

    def current_l1_resolution(self, *args, **kwargs):
        return main_controller.current_l1_resolution(self, *args, **kwargs)

    def current_l11_resolution(self, *args, **kwargs):
        return main_controller.current_l11_resolution(self, *args, **kwargs)

    def current_grid_unit(self, *args, **kwargs):
        return main_controller.current_grid_unit(self, *args, **kwargs)

    def grid_level_headers(self):
        """Return compatible L0, L1, L11, and L2 headers for latlon.nc."""
        if not self._grid_l2_header:
            self.update_l2_resolution_from_metadata()
        if not self._grid_l2_header:
            raise ValueError("L2 grid is not available. Run and save meteorology data first.")

        l0_resolution = self.current_l0_resolution()
        l1_resolution = self.current_l1_resolution()
        l11_resolution = self.current_l11_resolution()
        if not l0_resolution or not l1_resolution or not l11_resolution:
            raise ValueError("L0, L1, and L11 resolutions must be available.")

        unit = self.current_grid_unit()
        l2_header = dict(self._grid_l2_header)
        l2_header["unit"] = unit
        ratio = int(
            (self._grid_l2_metadata or {}).get("l2_ratio_to_l0")
            or round(float(l2_header["cellsize"]) / float(l0_resolution))
        )
        headers = {
            "L0": l0_header_from_l2(l2_header, l0_resolution, ratio),
            "L1": header_for_model_level(
                l2_header, l1_resolution, l0_resolution, "L1"
            ),
            "L11": header_for_model_level(
                l2_header, l11_resolution, l0_resolution, "L11"
            ),
            "L2": l2_header,
        }
        return validate_grid_headers(headers)[0]

    def grid_configuration_snapshot(self):
        """Return current grid configuration for project-state reference."""
        snapshot = {
            "l0_resolution": self.current_l0_resolution(),
            "l1_resolution": self.current_l1_resolution(),
            "l11_resolution": self.current_l11_resolution(),
            "l2_resolution": self.current_l2_resolution(),
            "unit": self.current_grid_unit(),
        }
        try:
            snapshot["headers"] = self.grid_level_headers()
        except Exception:
            snapshot["headers"] = {}
        return snapshot

    def update_extent_labels(self, *args, **kwargs):
        return main_controller.update_extent_labels(self, *args, **kwargs)

    def update_latlon_button_state(self, *args, **kwargs):
        return main_controller.update_latlon_button_state(self, *args, **kwargs)

    def _set_resolution_labels(self, *args, **kwargs):
        return main_controller._set_resolution_labels(self, *args, **kwargs)


    def resolution_state_precision(self, unit):
        """Return precision used when restoring saved grid-resolution choices."""
        if is_geographic_unit(unit):
            return 8
        return 6

    def normalized_state_resolution(self, value, unit):
        """Normalize saved and available resolutions for robust state matching."""
        return ceil_cellsize(value, unit, precision=self.resolution_state_precision(unit))

    def resolution_values_match(self, available_value, preferred_value, unit):
        """Return True when two resolutions are equivalent for state restore."""
        try:
            available = self.normalized_state_resolution(available_value, unit)
            preferred = self.normalized_state_resolution(preferred_value, unit)
        except (TypeError, ValueError):
            return False
        tolerance = 10 ** (-self.resolution_state_precision(unit))
        return abs(available - preferred) <= tolerance

    def log_resolution_preference_warning(self, *args, **kwargs):
        return main_controller.log_resolution_preference_warning(self, *args, **kwargs)


    def connect_input_state_teardown_guards(self):
        """Avoid overwriting saved layer selections during QGIS teardown."""
        application = QtCore.QCoreApplication.instance()
        if application is not None:
            try:
                application.aboutToQuit.connect(self.suspend_input_state_saves)
            except Exception:
                pass

        try:
            project = QgsProject.instance()
        except Exception:
            project = None
        if project is None:
            return

        removal_start_signal = getattr(project, "layersWillBeRemoved", None)
        if removal_start_signal is not None:
            try:
                removal_start_signal.connect(self.preserve_missing_layer_state)
            except Exception:
                pass

        for signal_name in ("layersRemoved", "cleared"):
            signal = getattr(project, signal_name, None)
            if signal is None:
                continue
            try:
                signal.connect(self.release_missing_layer_state_preservation)
            except Exception:
                pass

    def suspend_input_state_saves(self, *args):
        """Stop input-state writes once QGIS is shutting down."""
        self._preserve_missing_layer_state = True
        self._suspend_input_state_saves = True

    def preserve_missing_layer_state(self, *args):
        """Keep saved layer entries if QGIS removes layers from combo boxes."""
        self._preserve_missing_layer_state = True

    def release_missing_layer_state_preservation(self, *args):
        """Stop preserving empty layer selections after a removal batch ends."""
        if self._suspend_input_state_saves:
            return
        QtCore.QTimer.singleShot(0, self.clear_missing_layer_state_preservation)

    def clear_missing_layer_state_preservation(self):
        """Return to normal layer-state saving after project layer removal."""
        if not self._suspend_input_state_saves:
            self._preserve_missing_layer_state = False

    def connect_processor_button(self, *args, **kwargs):
        return main_controller.connect_processor_button(self, *args, **kwargs)

    def run_processor_action(self, action_name, callback):
        """Log current input selections before running a processor action."""
        self.log_selected_input_paths(action_name)
        self.save_input_state()
        callback()
        if action_name == "Fill DEM":
            self.update_l0_resolution_from_dem()

    def run_background_processor_action(self, action_name, callback, button):
        """Dispatch supported file jobs without moving QGIS objects off-thread."""
        self.log_selected_input_paths(action_name)
        self.save_input_state()
        controls = (button,)
        failed = lambda message: self._background_processor_failed(
            action_name, message
        )
        if action_name in {"Fill DEM", "Slope", "Aspect", "Flow Accumulation", "Flow Direction"}:
            variable = "filled_dem" if action_name == "Fill DEM" else action_name.lower().replace(" ", "_")
            started = self.morphology_tasks.start_dem_derivatives(
                controls=controls, load=variable, failed=failed
            )
        elif action_name == "Create Channel Network":
            started = self.morphology_tasks.start_hydrology(
                key="create-channel-network",
                controls=controls,
                load="channel_network",
                failed=failed,
            )
        elif action_name == "Snap Pour Points":
            started = self.morphology_tasks.start_snap_points(
                controls=controls, load=True, failed=failed
            )
        elif action_name == "Gauge Position":
            started = self.morphology_tasks.start_gauge_position(
                controls=controls, load=True, failed=failed
            )
        elif action_name == "LAI":
            started = self.morphology_tasks.start_lai(
                controls=controls, failed=failed
            )
        elif action_name in {"Land Use", "Soil", "Hydrogeology"}:
            kind = {"Land Use": "lc", "Soil": "soil", "Hydrogeology": "geology"}[
                action_name
            ]
            started = self.morphology_tasks.start_categorical(
                kind,
                key=f"categorical-{kind}",
                controls=controls,
                load=True,
                failed=failed,
            )
        else:
            # Legacy callbacks remain on Qt's GUI thread until split into a
            # path-only worker and a main-thread completion callback.
            button.setEnabled(False)

            def run_on_main_thread():
                try:
                    callback()
                except Exception as error:
                    self._background_processor_failed(action_name, error)
                finally:
                    button.setEnabled(True)

            QtCore.QTimer.singleShot(0, run_on_main_thread)
            started = True
        if not started:
            QMessageBox.information(
                self, action_name, f"{action_name} is already running."
            )

    def _background_processor_failed(self, *args, **kwargs):
        return main_controller._background_processor_failed(self, *args, **kwargs)

    def reset_geometry_processing(self):
        """Reset geometry outputs and refresh workflow UI state."""
        if not self.project_folder:
            QMessageBox.warning(self, "Reset Geometry", "Select a project folder first.")
            return False
        answer = QMessageBox.question(
            self,
            "Reset Geometry",
            "This will remove all geometry layers from QGIS and delete all files "
            "in the temporary Morphology folder.\n\nAre you sure you want to continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            self.log_message("Reset cancelled by user.")
            return False
        folder = temp_morphology_folder(self.project_folder)
        removed = qgis_layers.remove_under(folder, log=self.log_message)
        deleted = morphology_reset.clear_geometry(
            self.project_folder, log=self.log_message
        )
        self.log_message(
            f"Geometry reset completed: removed {removed} map layer(s) and "
            f"deleted {deleted} file(s)."
        )
        self.refresh_morphology_workflow_button_states()
        return True

    def process_lat_lon(self):
        """Create the v5 coordinate input through the core setup API."""
        request = morphology_setup.SetupRequest(
            project_folder=self.project_folder,
            headers=self.grid_level_headers(),
            crs=self.selected_meteo_crs() or "",
        )
        outputs = morphology_setup.latlon(request, log=self.log_message)
        if outputs:
            self.log_message(f"latlon.nc created successfully: {outputs[0]}")
        return bool(outputs)

    def handle_domain_definition_type(self, *args, **kwargs):
        return main_controller.handle_domain_definition_type(self, *args, **kwargs)

    def open_domain_assignment(self):
        """Open the non-interactive gauge assignment for the DEM-extent domain."""
        if not self.project_folder:
            QMessageBox.warning(self, "Domain Assignment", "Select a project folder first.")
            return False
        try:
            outlet_ids = self.selected_outlet_ids()
        except StationIdError as error:
            QMessageBox.warning(self, "Domain Assignment", str(error))
            return False
        if not outlet_ids:
            QMessageBox.warning(
                self,
                "Domain Assignment",
                "The pour-point layer does not contain any outlet features.",
            )
            return False
        try:
            from ...qt.dialogs.discharge_assignment import (
                DischargeTableAssignmentDialog,
            )
            from ...qgis_bridge.layers.domain import DomainWorkflow
            from ...core.handlers.state.domain_state import DOMAIN_MODE_DEM_EXTENT
            from ...core.handlers.state.nml_settings import sync_domain_settings

            layer = self.input_combo("pour_points").currentLayer()
            workflow = DomainWorkflow(
                self.project_folder,
                layer,
                self.selected_outlet_id_field(),
                outlet_ids,
                prepare=self.morphology_tasks.prepare_filled_dem,
                log=self.log_message,
            )
            state = workflow.load_synced_state(DOMAIN_MODE_DEM_EXTENT, True)
            dialog = DischargeTableAssignmentDialog(
                outlet_ids,
                self,
                initial_records=state.get("outlets", {}),
            )
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return False
            workflow.apply_dem_extent(dialog.selected_assignments())
            sync_domain_settings(self.project_folder)
            self.update_gauged_outlet_count()
            self.invalidate_execute_all_meteo()
            return True
        except Exception as error:
            self.log_message(f"ERROR: Domain assignment failed: {error}")
            self.log_message(f"Traceback: {traceback.format_exc()}")
            QMessageBox.critical(self, "Domain Assignment", str(error))
            return False


    def open_domain_delineator(self, checked=False):
        """Open the per-outlet domain delineation dialog in QGIS."""
        if not self.project_folder:
            QMessageBox.warning(
                self,
                "Domain Delineator",
                "Select a project folder first.",
            )
            return
        try:
            outlet_ids = self.selected_outlet_ids()
        except StationIdError as error:
            QMessageBox.warning(self, "Domain Delineator", str(error))
            return
        if not outlet_ids:
            QMessageBox.warning(
                self,
                "Domain Delineator",
                "The pour-point layer does not contain any outlet features.",
            )
            return

        from .domain_delineator import DomainDelineatorDialog

        layer = self.input_combo("pour_points").currentLayer()
        field = self.selected_outlet_id_field()
        controls = (
            self.comboBox_domainDefinitionType,
        )
        started = self.morphology_tasks.start_domain_preflight(
            controls,
            lambda context: self._show_prepared_domain_delineator(
                context, layer, field, outlet_ids
            ),
            self._domain_delineator_preflight_failed,
        )
        if not started:
            QMessageBox.information(
                self,
                "Domain Delineator",
                "Domain Delineator preparation is already running.",
            )

    def _show_prepared_domain_delineator(
        self, context, pour_points_layer, outlet_id_field, outlet_ids
    ):
        from .domain_delineator import DomainDelineatorDialog

        try:
            dialog = DomainDelineatorDialog(
                self,
                pour_points_layer,
                outlet_id_field,
                outlet_ids,
                prepared_context=context,
            )
            self._domain_delineator_dialog = dialog
            dialog.exec()
        except Exception as error:
            self.log_message(f"ERROR: Domain delineator failed: {error}")
            self.log_message(f"Traceback: {traceback.format_exc()}")
            QMessageBox.critical(self, "Domain Delineator", str(error))
        finally:
            self._domain_delineator_dialog = None
            try:
                from ...core.handlers.state.nml_settings import sync_domain_settings

                sync_domain_settings(self.project_folder)
            except Exception as error:
                self.log_message(
                    f"WARNING: Could not update domain namelist settings: {error}"
                )
            self.save_input_state()
            self.update_gauged_outlet_count()

    def _domain_delineator_preflight_failed(self, message):
        self.log_message(f"ERROR: Domain Delineator preparation failed: {message}")
        QMessageBox.critical(self, "Domain Delineator", error_detail(message))

    def morphology_workflow_specs(self):
        """Return metadata for persisted workflow buttons.

        `button` is the widget itself, not its name: `setupUi` has already run
        by the time anything asks, and a misspelled attribute must fail here
        rather than silently resolve to nothing.
        """
        return {
            "project_structure": {
                "button": self.pushButton_createProjectStructure,
                "label": "Project Structure",
                "completed_message": "Project structure is ready.",
                "failed_message": "Project structure setup failed.",
            },
            "scan_precipitation": {
                "button": self.pushButton_scanPrecipitationFiles,
                "label": "Precipitation Scan",
                "completed_message": "Precipitation scan completed.",
                "failed_message": "Precipitation scan failed.",
            },
            "execute_all_morphology": {
                "button": self.pushButton_executeAllMorphology,
                "label": "Execute All Morphology",
                "action_name": "Execute All Morphology",
                "thread_name": "mHM QGISExecuteAllThread",
                "completed_message": (
                    "Morphology preparation completed successfully."
                ),
                "failed_message": (
                    "Execute All Morphology failed. Check the log for details."
                ),
            },
            "execute_all_meteo": {
                "button": self.pushButton_executeAllMeteo,
                "label": "Meteorology Preparation",
                "action_name": "Meteorology Preparation",
                "thread_name": "mHM QGISMeteoThread",
                "completed_message": (
                    "Meteorology preparation completed successfully."
                ),
                "failed_message": (
                    "Meteorology preparation failed. "
                    "Check the log for details."
                ),
            },
            "execute_all_observations": {
                "button": self.pushButton_executeAllObservation,
                "label": "Observation Preparation",
                "completed_message": "Observation preparation completed.",
                "failed_message": "Observation preparation failed.",
            },
            "run_mhm": {
                "button": self.pushButton_execute_mHM,
                "label": "Run mHM",
                "completed_message": "mHM completed successfully.",
                "failed_message": "mHM execution failed.",
            },
        }

    def _capture_workflow_button_default_styles(self, *args, **kwargs):
        return main_controller._capture_workflow_button_default_styles(self, *args, **kwargs)

    def morphology_workflow_button(self, *args, **kwargs):
        return main_controller.morphology_workflow_button(self, *args, **kwargs)

    def running_morphology_workflow_key(self):
        """Return the first running morphology workflow key, if any."""
        if self.morphology_tasks.execute_all_active:
            return "execute_all_morphology"
        for workflow_key, thread in self._morphology_workflow_threads.items():
            if thread is not None and thread.isRunning():
                return workflow_key
        return None

    def start_execute_all_morphology_processing(self):
        """Run Execute All as a sequence of QGIS-managed file tasks."""
        self.morphology_tasks.start_execute_all()

    def update_gauged_outlet_count(self, layer=None):
        return main_controller.update_gauged_outlet_count(self, layer)

    def _current_meteorology_state(self):
        """Return a meteorology output state bound to the current project."""
        return MeteorologyOutputState(self.project_folder, log=self.log_message)

    def start_execute_all_meteo_processing(self):
        """Validate inputs and run meteo then morphology setup."""
        if self.running_morphology_workflow_key() is not None:
            self.start_morphology_workflow("execute_all_meteo")
            return
        if not self.check_prerequisites():
            return

        try:
            inputs = self.selected_meteo_specs()
            metadata = main_controller.require_common_grid(self)
            target_grid = target_grid_from_header(metadata["l2_header"], self.selected_meteo_crs())
            target_grid.validate()
            self._pending_meteo_run = MeteorologyRun(
                project_folder=Path(self.project_folder),
                inputs=inputs,
                target_grid=target_grid,
                grid_metadata=dict(metadata),
                times=main_controller.simulation_times(self),
            )
        except Exception as error:
            self.log_message(f"ERROR: Cannot start meteo setup: {error}")
            QMessageBox.warning(
                self,
                "Meteorology Setup",
                str(error),
            )
            return

        self.start_morphology_workflow("execute_all_meteo")

    def start_execute_all_observation_processing(self):
        return main_controller.start_execute_all_observations(self)

    def start_morphology_workflow(self, workflow_key):
        """Start a named morphology workflow in a background worker thread."""
        if workflow_key == "execute_all_morphology":
            self.morphology_tasks.start_execute_all()
            return
        spec = self.morphology_workflow_specs().get(workflow_key)
        if spec is None:
            self.log_message(f"ERROR: Unknown morphology workflow: {workflow_key}")
            return

        if self.task_coordinator.resource_busy("morphology-processor"):
            QMessageBox.information(
                self,
                spec["label"],
                "Another morphology preprocessing task is currently running.",
            )
            return
        if not self.task_coordinator.has_capacity():
            QMessageBox.information(
                self,
                spec["label"],
                "All configured worker threads are currently busy.",
            )
            return

        running_key = self.running_morphology_workflow_key()
        if running_key is not None:
            running_spec = self.morphology_workflow_specs().get(
                running_key, {}
            )
            QMessageBox.information(
                self,
                spec["label"],
                f"{running_spec.get('label', 'A morphology workflow')} is already running.",
            )
            return

        if not self.check_prerequisites():
            return

        self.log_selected_input_paths(spec["action_name"])
        self.save_input_state()
        main_controller.invalidate_workflows(self, "run_mhm")
        processing_state.mark_workflow(self.project_folder, workflow_key, "running")
        self.set_morphology_workflow_button_state(workflow_key, "running")
        if workflow_key == "execute_all_meteo":
            self.set_meteo_setup_controls_enabled(False)

        thread = QtCore.QThread(self)
        thread.setObjectName(spec["thread_name"])
        worker = MeteorologyWorkflowWorker(
            workflow_key,
            spec["label"],
            meteorology_state=self._current_meteorology_state(),
            meteorology_run=self._pending_meteo_run,
        )
        worker.moveToThread(thread)

        worker.log_message.connect(self.log_message)
        worker.finished.connect(self.finish_meteo_workflow_stage)
        worker.finished.connect(
            lambda key, ok, message, workflow_thread=thread: workflow_thread.quit()
        )
        worker.finished.connect(worker.deleteLater)
        thread.started.connect(worker.run)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(
            lambda key=workflow_key: self.clear_morphology_workflow_worker(key)
        )

        self._morphology_workflow_threads[workflow_key] = thread
        self._morphology_workflow_workers[workflow_key] = worker
        self.task_coordinator.start_external(
            f"workflow-{workflow_key}",
            spec["label"],
            resource="morphology-processor",
            cancel=worker.cancel,
        )
        worker.log_message.connect(
            lambda message, key=workflow_key: self.task_coordinator.append_log(
                f"workflow-{key}", message
            )
        )
        worker.progress.connect(
            lambda value, key=workflow_key: self.task_coordinator.set_progress(
                f"workflow-{key}", value
            )
        )
        thread.start()

    def finish_meteo_workflow_stage(self, workflow_key, ok, message):
        """Meteorology completion never executes morphology commands."""
        self.finish_morphology_workflow(workflow_key, ok, message)

    def finish_morphology_workflow(self, workflow_key, ok, message):
        """Update UI and persisted workflow status after a workflow finishes."""
        spec = self.morphology_workflow_specs().get(workflow_key)
        if spec is None:
            return
        if workflow_key == "execute_all_meteo":
            self.set_meteo_setup_controls_enabled(True)

        if ok:
            processing_state.mark_workflow(
                self.project_folder,
                workflow_key,
                "completed",
                spec["completed_message"],
            )
            self.set_morphology_workflow_button_state(workflow_key, "completed")
            self.log_message(spec["completed_message"])
            self.task_coordinator.finish_external(
                f"workflow-{workflow_key}", True, spec["completed_message"]
            )
            return

        workflow_message = processing_state.workflow(
            self.project_folder, workflow_key
        ).get("message")
        message = (
            message
            or workflow_message
            or spec["failed_message"]
        )
        processing_state.mark_workflow(
            self.project_folder,
            workflow_key,
            "failed",
            message,
        )
        self.set_morphology_workflow_button_state(workflow_key, "failed")
        self.log_message(message)
        self.task_coordinator.finish_external(
            f"workflow-{workflow_key}", False, message
        )
        if not self._closing:
            QMessageBox.critical(self, spec["label"], message)

    def clear_morphology_workflow_worker(self, workflow_key):
        """Drop worker references after a morphology workflow thread stops."""
        self._morphology_workflow_threads.pop(workflow_key, None)
        self._morphology_workflow_workers.pop(workflow_key, None)
        if workflow_key == "execute_all_meteo":
            self._pending_meteo_run = None

    def invalidate_execute_all_meteo(self):
        """Clear saved completion when any meteo setup input changes."""
        workflow_key = "execute_all_meteo"
        if (
                self._loading_input_state
                or not self.project_folder
                or self.running_morphology_workflow_key() == workflow_key):
            return
        processing_state.remove_entry(
            self.project_folder, "workflows", workflow_key
        )
        self.set_morphology_workflow_button_state(workflow_key, "")
        processing_state.remove_entry(
            self.project_folder, "workflows", "run_mhm"
        )
        self.set_morphology_workflow_button_state("run_mhm", "")

    def set_meteo_setup_controls_enabled(self, *args, **kwargs):
        return main_controller.set_meteo_setup_controls_enabled(self, *args, **kwargs)

    def done(self, result):
        """Cancel active work before closing through Cancel or Escape."""
        if self._closing:
            return
        running_key = self.running_morphology_workflow_key()
        terminal = self._terminal_dialog
        terminal_busy = terminal is not None and terminal.command_running()
        if int(result) == int(QDialog.DialogCode.Rejected) and (
            running_key is not None or self.task_coordinator.is_busy()
            or (terminal is not None and terminal.is_running())
        ):
            self._closing = True
            self._close_result = result
            self.task_coordinator.cancel_all()
            for worker in tuple(self._morphology_workflow_workers.values()):
                worker.cancel()
            for thread in tuple(self._morphology_workflow_threads.values()):
                thread.requestInterruption()
            if terminal is not None:
                terminal.stop()
            QtCore.QTimer.singleShot(0, self._finish_cancelled_close)
            return
        if running_key is not None or self.task_coordinator.is_busy() or terminal_busy:
            spec = self.morphology_workflow_specs().get(
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
        if (self.task_coordinator.is_busy()
                or self._morphology_workflow_threads
                or (terminal is not None and terminal.is_running())):
            QtCore.QTimer.singleShot(50, self._finish_cancelled_close)
            return
        if self._domain_delineator_dialog is not None:
            self._domain_delineator_dialog.close()
        super().done(self._close_result)

    def set_execute_all_button_state(self, *args, **kwargs):
        return main_controller.set_execute_all_button_state(self, *args, **kwargs)

    def set_morphology_workflow_button_state(self, *args, **kwargs):
        return main_controller.set_morphology_workflow_button_state(self, *args, **kwargs)

    def refresh_execute_all_button_state(self, *args, **kwargs):
        return main_controller.refresh_execute_all_button_state(self, *args, **kwargs)

    def refresh_morphology_workflow_button_states(self, *args, **kwargs):
        return main_controller.refresh_morphology_workflow_button_states(self, *args, **kwargs)

    def refresh_morphology_workflow_button_state(self, *args, **kwargs):
        return main_controller.refresh_morphology_workflow_button_state(self, *args, **kwargs)

    def project_terminal_dialog(self):
        """Return the persistent terminal dialog for this plugin dialog."""
        if self._terminal_dialog is None:
            self._terminal_dialog = ProjectTerminalDialog(self)
        return self._terminal_dialog

    def open_thread_display(self, checked=False):
        """Show the persistent task monitor for plugin preprocessing."""
        if self._thread_display_dialog is None:
            self._thread_display_dialog = ThreadDisplayDialog(
                self.task_coordinator, self
            )
        self._thread_display_dialog.show()
        self._thread_display_dialog.raise_()
        self._thread_display_dialog.activateWindow()
        return self._thread_display_dialog

    def open_project_terminal(self):
        """Open the persistent terminal in the plugin-owned workspace."""
        if not self.project_folder:
            QMessageBox.warning(
                self,
                "Project Folder Required",
                "Select a project folder before opening the terminal.",
            )
            return None
        terminal = self.project_terminal_dialog()
        terminal.show_for_directory(workspace_folder(self.project_folder))
        return terminal

    def categorical_type_combo(self, *args, **kwargs):
        return main_controller.categorical_type_combo(self, *args, **kwargs)

    def categorical_input_mode(self, *args, **kwargs):
        return main_controller.categorical_input_mode(self, *args, **kwargs)

    def categorical_lookup_config(self, *args, **kwargs):
        return main_controller.categorical_lookup_config(self, *args, **kwargs)

    def categorical_source_config(self, *args, **kwargs):
        return main_controller.categorical_source_config(self, *args, **kwargs)

    def lai_netcdf_config(self, *args, **kwargs):
        return main_controller.lai_netcdf_config(self, *args, **kwargs)

    def handle_categorical_type(self, *args, **kwargs):
        return main_controller.handle_categorical_type(self, *args, **kwargs)

    def handle_lai_input_type(self, *args, **kwargs):
        return main_controller.handle_lai_input_type(self, *args, **kwargs)

    def handle_land_use_input_type(self, *args, **kwargs):
        return main_controller.handle_land_use_input_type(self, *args, **kwargs)

    def handle_multi_horizon_soil_input(self, *args, **kwargs):
        return main_controller.handle_multi_horizon_soil_input(self, *args, **kwargs)

    def uses_advanced_categorical_input(self, *args, **kwargs):
        return main_controller.uses_advanced_categorical_input(self, *args, **kwargs)

    def _save_land_cover_nml_input(self, value):
        from ...core.handlers.state.nml_settings import update_section

        update_section(
            self.project_folder,
            "land_cover",
            {
                "mode": "historical" if len(value.periods) > 1 else "single",
                "variable": "land_cover",
                "lookup_table": str(value.lookup_table),
                "mapping_field": value.mapping_field,
                "class_field": value.class_field,
                "scenes": [
                    {
                        "start_time": item.start_time.isoformat(),
                        "end_time": item.end_time.isoformat(),
                        "start_year": item.start_year,
                        "end_year": item.end_year,
                        "source_path": str(item.file_path),
                    }
                    for item in value.periods
                ],
            },
        )

    def _save_soil_nml_input(self, value):
        from ...core.handlers.state.nml_settings import update_section

        data = value.as_dict()
        data.update(
            {
                "mode": "multi_horizon",
                "soil_db_mode": 1,
                "variable": "soil_class",
                "source_bulk_density_unit": value.bulk_density_unit,
                "bulk_density_unit": "g/cm3",
                "composition_normalization": "component_sum_percent",
            }
        )
        update_section(self.project_folder, "soil", data)

    def _save_standard_soil_nml_input(
        self, mode, output_path=None, classdefinition_path=None
    ):
        if not self.project_folder:
            return
        from ...core.handlers.state.nml_settings import relative_workspace_path, update_section

        output = (
            relative_workspace_path(self.project_folder, output_path)
            if output_path
            else relative_workspace_path(self.project_folder, os.path.join(morph_folder(self.project_folder), "soil_class.asc"))
        )
        definition = (
            relative_workspace_path(self.project_folder, classdefinition_path)
            if classdefinition_path
            else relative_workspace_path(self.project_folder, os.path.join(morph_folder(self.project_folder), "soil_classdefinition.txt"))
        )
        update_section(
            self.project_folder,
            "soil",
            {
                "mode": mode,
                "soil_db_mode": 0,
                "output_path": output,
                "variable": "soil_class",
                "classdefinition_path": definition,
                "horizons": [],
            },
        )

    def record_standard_soil_output(self, output_path=None, classdefinition_path=None):
        """Update the namelist handoff after standard soil preparation."""
        self._save_standard_soil_nml_input(
            "mhm_ready" if self.categorical_input_mode("soil").lower() == "mhm ready"
            else "single_categorical",
            output_path,
            classdefinition_path,
        )

    def refresh_advanced_nml_settings(self):
        """Refresh version-dependent settings after the mHM version changes."""
        value = self._advanced_inputs.get("soil")
        if value:
            self._save_soil_nml_input(self._soil_input_value(value))
        land_cover = self._advanced_inputs.get("land_cover")
        if land_cover:
            self._save_land_cover_nml_input(
                self._land_use_input_value(land_cover)
            )
        self.invalidate_execute_all_meteo()

    @staticmethod
    def _land_use_input_value(value):
        from ...core.handlers.lookup import LandUseInput, LandUsePeriod

        if isinstance(value, LandUseInput):
            return value
        if not isinstance(value, dict):
            raise ValueError("Configure the land-use layers first.")
        return LandUseInput(
            tuple(
                LandUsePeriod.from_dict(item)
                for item in value.get("periods", [])
            ),
            Path(value.get("lookup_table", "")),
            str(value.get("mapping_field", "")),
            str(value.get("class_field", "")),
        )

    @staticmethod
    def _soil_input_value(value):
        from ...core.handlers.lookup import SoilHorizon, SoilInput

        if isinstance(value, SoilInput):
            return value
        if not isinstance(value, dict):
            raise ValueError("Configure the multi-horizon soil layers first.")
        return SoilInput(
            tuple(
                SoilHorizon(
                    int(item["horizon"]),
                    float(item["upper_depth"]),
                    float(item["lower_depth"]),
                    Path(item["clay_layer"]),
                    Path(item["sand_layer"]),
                    Path(item["silt_layer"]),
                    Path(item["bulk_density_layer"]),
                )
                for item in value.get("horizons", [])
            ),
            str(value.get("bulk_density_unit", "")),
        )

    def _restore_categorical_mode(self, *args, **kwargs):
        return main_controller._restore_categorical_mode(self, *args, **kwargs)

    def restore_lai_input_type(self, lai_input_type):
        """Restore the selected LAI input type by text when possible."""
        combo_box = self.comboBox_lai_inputType
        if combo_box is None or not lai_input_type:
            return
        index = combo_box.findText(lai_input_type)
        if index >= 0:
            combo_box.setCurrentIndex(index)

    def input_layer_widgets(self, *args, **kwargs):
        return main_controller.input_layer_widgets(self, *args, **kwargs)

    def input_text_widgets(self, *args, **kwargs):
        return main_controller.input_text_widgets(self, *args, **kwargs)

    def input_state_path(self):
        """Return the project-local input state file path."""
        if not self.project_folder:
            return None
        return project_input_state_path(self.project_folder)

    def save_input_state(self):
        """Save selected inputs to a JSON file in the project folder."""
        if (
            not self.project_folder
            or self._loading_input_state
            or self._suspend_input_state_saves
        ):
            return

        state_path = self.input_state_path()
        if not state_path:
            return

        existing_state = self.read_existing_input_state(state_path)
        existing_layers = existing_state.get("layers", {})
        if not isinstance(existing_layers, dict):
            existing_layers = {}

        layers = {}
        for key, combo_box in self.input_layer_widgets():
            item_data = combo_box.currentData()
            if isinstance(item_data, dict) and item_data.get("origin") == "file":
                source = item_data.get("path", "")
                suffix = os.path.splitext(source)[1].lower()
                layers[key] = {
                    "name": os.path.basename(source),
                    "source": source,
                    "type": "vector" if suffix == ".shp" else "raster",
                    "origin": "file",
                    "manual": bool(item_data.get("manual")),
                    "variable": str(item_data.get("variable", "")),
                    "source_crs": str(item_data.get("source_crs", "")),
                }
                continue
            layer = combo_box.currentLayer()
            if not layer:
                if self._preserve_missing_layer_state and key in existing_layers:
                    layers[key] = existing_layers.get(key)
                else:
                    layers[key] = None
                continue

            layer_type = (
                "raster"
                if isinstance(layer, QgsRasterLayer)
                or layer.type() == QgsMapLayer.LayerType.RasterLayer
                else "vector"
            )
            layers[key] = {
                "name": layer.name(),
                "source": layer.source(),
                "type": layer_type,
                "origin": "qgis",
            }

        text_inputs = {key: widget.text() for key, widget in self.input_text_widgets()}
        grid_resolutions = {
            "l1_resolution": self.current_l1_resolution(),
            "l11_resolution": self.current_l11_resolution(),
        }

        crs = self.get_crs()
        state = {
            "version": 4,
            "mhm_version": self.comboBox_mHMversion.currentText().strip(),
            "work_type": self.comboBox_mhmWorkType.currentText(),
            "layers": layers,
            "text_inputs": text_inputs,
            "grid_resolutions": grid_resolutions,
            "grid_configuration": self.grid_configuration_snapshot(),
            "lai_input_type": self.comboBox_lai_inputType.currentText(),
            "folder_search": self.checkBox_enableFolderSearch.isChecked(),
            "categorical_types": {
                kind: self.categorical_input_mode(kind)
                for kind in ("lc", "soil", "geology", "lai")
            },
            "categorical_lookups": self._serialized_categorical_lookups(),
            "categorical_ready": self._serialized_path_configs(
                self._categorical_ready_configs
            ),
            "lai_netcdf": self._serialized_path_config(self._lai_input_config),
            "thread_count": self.task_coordinator.max_threads,
            "advanced_inputs": {
                kind: value.as_dict() if hasattr(value, "as_dict") else value
                for kind, value in self._advanced_inputs.items()
            },
            "land_cover_ready_source": self._land_cover_ready_source,
            "meteo_inputs": self.serialized_meteo_inputs(),
            "observation_inputs": self._observation_inputs,
            "pour_point_outlet_id_field": self.selected_outlet_id_field(),
            "domain_definition_type": self.comboBox_domainDefinitionType.currentText().strip(),
            "crs_authid": crs.authid() if crs and crs.isValid() else "",
            "project_layout": {
                "data_folder": data_folder(self.project_folder),
                "temp_morphology_folder": temp_morphology_folder(self.project_folder),
                "output_folder": output_folder(self.project_folder),
                "restart_folder": restart_folder(self.project_folder),
            },
        }

        try:
            from ...core.handlers.file import json as jsonio
            jsonio.write(state_path, state)
        except Exception as e:
            self.log_message(f"WARNING: Could not save input state: {e}")

    def _serialized_categorical_lookups(self):
        configs = {}
        for kind, config in self._categorical_lookup_configs.items():
            saved = dict(config)
            for key in ("input_path", "lookup_table"):
                saved[key] = self._portable_input_path(saved.get(key))
            configs[kind] = saved
        return configs

    def _serialized_path_configs(self, configs):
        return {
            kind: self._serialized_path_config(config)
            for kind, config in configs.items()
            if isinstance(config, dict)
        }

    def _serialized_path_config(self, config):
        if not isinstance(config, dict):
            return {}
        saved = dict(config)
        for key in ("input_path", "classdefinition_path"):
            if key in saved:
                saved[key] = self._portable_input_path(saved.get(key))
        return saved

    def _portable_input_path(self, path):
        if not path or not self.project_folder:
            return str(path or "")
        try:
            relative = os.path.relpath(path, self.project_folder)
            if relative != ".." and not relative.startswith(f"..{os.sep}"):
                return relative.replace("\\", "/")
        except ValueError:
            pass
        return str(path)

    def serialized_meteo_inputs(self):
        """Return project-portable meteorology folder and source selections."""
        state = {
            "l2_multiplier": int(self.spinBox_L2ResolutionMultiplier.value()),
            "l2_multiplier_user_selected": bool(
                self._l2_multiplier_user_selected
            ),
        }
        for kind, combo, source_combo in self.meteo_input_widgets():
            path = self.selected_folder_path(combo)
            if path and self.project_folder:
                try:
                    relative = os.path.relpath(path, self.project_folder)
                    if relative != ".." and not relative.startswith(f"..{os.sep}"):
                        path = relative.replace("\\", "/")
                except ValueError:
                    pass
            state[kind] = {
                "folder": path,
                "source": source_combo.currentText().strip() if source_combo is not None else "auto",
                "pattern": self.lineEdit_precipitationGlobDefinition.text() if kind == "precipitation" else "*.nc",
            }
        state["variables"] = self._meteo_inputs
        return state

    def read_existing_input_state(self, state_path):
        """Return existing input state for preservation during teardown."""
        if not state_path or not os.path.exists(state_path):
            return {}
        try:
            with open(state_path, "r", encoding="utf-8") as state_file:
                state = json.load(state_file)
            if isinstance(state, dict):
                return state
        except Exception:
            pass
        return {}

    def load_input_state(self):
        """Load saved input selections from the project folder."""
        state_path = self.input_state_path()
        self._loading_input_state = True
        try:
            self._clear_input_state_selections()
            if not state_path or not os.path.exists(state_path):
                self.refresh_input_sources()
                self.refresh_meteo_folder_sources()
                self.log_message("No saved input state found for this project.")
                return
            try:
                with open(state_path, "r", encoding="utf-8") as state_file:
                    state = json.load(state_file)
            except Exception as error:
                self.refresh_input_sources()
                self.log_message(f"WARNING: Could not read input state: {error}")
                return

            self.restore_mhm_version(state.get("mhm_version", ""))
            self.restore_grid_resolution_preferences(
                state.get("grid_resolutions", {}))
            self.restore_text_inputs(state.get("text_inputs", {}))
            self.restore_lai_input_type(state.get("lai_input_type", ""))
            self.restore_input_crs(state.get("crs_authid", ""))
            self.restore_categorical_inputs(state)
            self.restore_advanced_inputs(state)
            self.refresh_input_sources()
            self.refresh_meteo_folder_sources()
            self.restore_input_layers(state.get("layers", {}))
            self.restore_domain_inputs(state)
            self.restore_meteo_inputs(state.get("meteo_inputs", {}))
            self._observation_inputs = dict(state.get("observation_inputs", {}))
            main_controller.populate_observation_table(self)
            main_controller.restore_simulation_times(self)
            self.refresh_grid_resolution_controls()
        finally:
            self._loading_input_state = False
            main_controller.refresh_observation_stations(self)
            if getattr(self, "_discharge_plot", None) is not None:
                self._discharge_plot.close()

        self.log_message(f"Input state loaded: {state_path}")

    def _clear_input_state_selections(self):
        """Prevent selections from leaking between project folders."""
        for _, combo in self.input_layer_widgets():
            combo.setCurrentIndex(-1)
        for kind in ("lc", "soil", "geology", "lai"):
            combo = self.categorical_type_combo(kind)
            combo.blockSignals(True)
            combo.setCurrentIndex(-1)
            combo.blockSignals(False)
        for _, widget in self.input_text_widgets():
            widget.clear()
        self.comboBox_pourPointOutletID.blockSignals(True)
        self.comboBox_pourPointOutletID.clear()
        self.comboBox_pourPointOutletID.addItem("")
        self.comboBox_pourPointOutletID.blockSignals(False)
        self.comboBox_domainDefinitionType.blockSignals(True)
        self.comboBox_domainDefinitionType.setCurrentIndex(-1)
        self.comboBox_domainDefinitionType.blockSignals(False)
        for _, folder_combo, source_combo in self.meteo_input_widgets():
            folder_combo.blockSignals(True)
            folder_combo.setCurrentIndex(-1)
            folder_combo.blockSignals(False)
            if source_combo is not None:
                source_combo.blockSignals(True)
                source_combo.setCurrentIndex(-1)
                source_combo.blockSignals(False)
        self.lineEdit_precipitationGlobDefinition.setText("*.nc")
        self._grid_l2_metadata = None
        self._grid_l2_header = None
        main_controller.restore_simulation_times(self)
        self.spinBox_L2ResolutionMultiplier.blockSignals(True)
        self.spinBox_L2ResolutionMultiplier.setValue(1)
        self.spinBox_L2ResolutionMultiplier.blockSignals(False)
        self._l2_multiplier_user_selected = False
        self._meteo_inspections = {}
        self._meteo_inputs = {}
        self._observation_inspections = {}
        self._observation_inputs = {}
        self._grid_l0_info = None
        main_controller.populate_meteo_table(self)
        main_controller.populate_observation_table(self)
        self.clear_precipitation_resolution_labels()
        self.checkBox_enableFolderSearch.blockSignals(True)
        self.checkBox_enableFolderSearch.setChecked(False)
        self.checkBox_enableFolderSearch.blockSignals(False)
        self._categorical_lookup_configs = {}
        self._categorical_ready_configs = {}
        self._lai_input_config = {}
        self._categorical_modes = {}
        self._advanced_inputs = {}
        self._land_cover_ready_source = ""
        self._domain_definition_mode = ""
        self._preferred_l1_resolution = None
        self._preferred_l11_resolution = None
        self.task_coordinator.set_max_threads(2)

    def restore_domain_inputs(self, state):
        """Restore the selected outlet ID field and domain mode."""
        self.populate_pour_point_outlet_fields(
            preferred=str(state.get("pour_point_outlet_id_field", "") or "")
        )
        combo = self.comboBox_domainDefinitionType
        if combo is not None:
            text = str(state.get("domain_definition_type", "") or "")
            if not text and state.get("dem_domain"):
                text = combo.itemText(0)
            combo.blockSignals(True)
            combo.setCurrentIndex(combo.findText(text) if text else -1)
            combo.blockSignals(False)
            self._domain_definition_mode = text

    def restore_meteo_inputs(self, state):
        """Restore configured variables and grid preferences without scanning."""
        if not isinstance(state, dict):
            return
        try:
            multiplier = max(1, int(state.get("l2_multiplier", 1)))
        except (TypeError, ValueError):
            multiplier = 1
        self.spinBox_L2ResolutionMultiplier.blockSignals(True)
        self.spinBox_L2ResolutionMultiplier.setValue(multiplier)
        self.spinBox_L2ResolutionMultiplier.blockSignals(False)
        self._l2_multiplier_user_selected = bool(
            state.get(
                "l2_multiplier_user_selected",
                "l2_multiplier" in state,
            )
        )

        self._meteo_inputs = dict(state.get("variables", {}))
        main_controller.populate_meteo_table(self)
        for kind, folder_combo, source_combo in self.meteo_input_widgets():
            saved = state.get(kind, {})
            if not isinstance(saved, dict):
                continue
            folder = str(saved.get("folder", "") or "")
            if folder and not os.path.isabs(folder):
                folder = os.path.abspath(os.path.join(self.project_folder, folder))
            index = self._folder_combo_index(folder_combo, folder)
            if index < 0 and folder and os.path.isdir(folder):
                self._add_folder_combo_item(folder_combo, folder)
                index = folder_combo.count() - 1
            folder_combo.setCurrentIndex(index if index >= 0 else 0)
            if source_combo is None:
                self.lineEdit_precipitationGlobDefinition.setText(saved.get("pattern", "*.nc"))
                continue
            source = str(saved.get("source", "") or "")
            source_index = source_combo.findText(source)
            if source_index < 0:
                normalized = source.lower().replace("_", " ")
                for candidate in range(source_combo.count()):
                    if source_combo.itemText(candidate).lower().replace("_", " ") == normalized:
                        source_index = candidate
                        break
            source_combo.setCurrentIndex(source_index)

    def restore_categorical_inputs(self, state):
        """Restore folder search, categorical modes, and lookup selections."""
        self.checkBox_enableFolderSearch.blockSignals(True)
        self.checkBox_enableFolderSearch.setChecked(bool(state.get("folder_search")))
        self.checkBox_enableFolderSearch.blockSignals(False)

        configs = {}
        for kind, config in state.get("categorical_lookups", {}).items():
            if kind not in {"lc", "soil", "geology", "lai"} or not isinstance(config, dict):
                continue
            restored = dict(config)
            for key in ("input_path", "lookup_table"):
                path = restored.get(key)
                if path and not os.path.isabs(path):
                    restored[key] = os.path.abspath(
                        os.path.join(self.project_folder, path)
                    )
            configs[kind] = restored
        self._categorical_lookup_configs = configs

        ready = {}
        for kind, config in state.get("categorical_ready", {}).items():
            if kind not in {"lc", "soil", "geology", "lai"} or not isinstance(config, dict):
                continue
            restored = dict(config)
            for key in ("input_path", "classdefinition_path"):
                path = restored.get(key)
                if path and not os.path.isabs(path):
                    restored[key] = os.path.abspath(
                        os.path.join(self.project_folder, path)
                    )
            ready[kind] = restored
        self._categorical_ready_configs = ready

        lai = state.get("lai_netcdf", {})
        self._lai_input_config = dict(lai) if isinstance(lai, dict) else {}
        path = self._lai_input_config.get("input_path")
        if path and not os.path.isabs(path):
            self._lai_input_config["input_path"] = os.path.abspath(
                os.path.join(self.project_folder, path)
            )
        self.task_coordinator.set_max_threads(state.get("thread_count", 2))

        for kind, text in state.get("categorical_types", {}).items():
            if kind not in {"lc", "soil", "geology", "lai"}:
                continue
            combo = self.categorical_type_combo(kind)
            index = combo.findText(str(text))
            if index < 0:
                normalized = str(text).lower().replace("_", " ")
                for candidate in range(combo.count()):
                    if (
                            combo.itemText(candidate).lower().replace("_", " ")
                            == normalized):
                        index = candidate
                        break
            combo.setCurrentIndex(index)
            self._categorical_modes[kind] = str(text) if index >= 0 else ""

    def restore_advanced_inputs(self, state):
        """Restore advanced source records without reopening their dialogs."""
        inputs = state.get("advanced_inputs", {})
        self._advanced_inputs = dict(inputs) if isinstance(inputs, dict) else {}
        source = str(state.get("land_cover_ready_source", "") or "")
        if source and not os.path.isabs(source):
            source = os.path.abspath(os.path.join(self.project_folder, source))
        self._land_cover_ready_source = source

    def restore_mhm_version(self, version):
        """Restore the saved mHM version selection."""
        combo_box = self.comboBox_mHMversion
        if combo_box is None or not version:
            return
        index = combo_box.findText(str(version))
        if index >= 0:
            combo_box.setCurrentIndex(index)

    def restore_grid_resolution_preferences(self, grid_resolutions):
        """Restore preferred L1/L11 selections for the next combo population."""
        try:
            self._preferred_l1_resolution = (
                float(grid_resolutions.get("l1_resolution"))
                if grid_resolutions.get("l1_resolution") is not None else None
            )
        except (TypeError, ValueError):
            self._preferred_l1_resolution = None
        try:
            self._preferred_l11_resolution = (
                float(grid_resolutions.get("l11_resolution"))
                if grid_resolutions.get("l11_resolution") is not None else None
            )
        except (TypeError, ValueError):
            self._preferred_l11_resolution = None

    def restore_text_inputs(self, text_inputs):
        """Restore saved file/folder and numeric text fields."""
        widget_lookup = dict(self.input_text_widgets())
        for key, value in text_inputs.items():
            widget = widget_lookup.get(key)
            if widget is not None:
                widget.setText(value or "")

    def restore_input_crs(self, crs_authid):
        """Restore the saved processing CRS if it is valid."""
        if not crs_authid:
            return

        from qgis.core import QgsCoordinateReferenceSystem

        crs = QgsCoordinateReferenceSystem(crs_authid)
        if crs.isValid():
            self.mProjectionSelectionWidget_crs.setCrs(crs)

    def restore_input_layers(self, saved_layers):
        """Restore saved layer selections, loading sources into QGIS if needed."""
        widget_lookup = dict(self.input_layer_widgets())
        for key, layer_info in saved_layers.items():
            combo_box = widget_lookup.get(key)
            if combo_box is None or not layer_info:
                continue
            if (
                isinstance(combo_box, InputComboAdapter)
                and layer_info.get("origin") == "file"
            ):
                self._restore_file_input(combo_box, layer_info)
                continue

            layer = self.find_or_load_saved_layer(layer_info)
            if not layer:
                self.log_message(
                    f"WARNING: Could not restore saved layer '{key}': "
                    f"{layer_info.get('source', '')}"
                )
                continue

            try:
                combo_box.setLayer(layer)
            except Exception as e:
                self.log_message(f"WARNING: Could not set saved layer '{key}': {e}")

    def _restore_file_input(self, adapter, layer_info):
        source = os.path.abspath(layer_info.get("source", ""))
        if not os.path.isfile(source):
            self.log_message(f"WARNING: Saved input file is missing: {source}")
            return
        combo = adapter.combo_box
        for index in range(combo.count()):
            data = combo.itemData(index)
            if isinstance(data, dict) and data.get("path") == source:
                updated = dict(data)
                updated.update(
                    variable=str(layer_info.get("variable", "")),
                    source_crs=str(layer_info.get("source_crs", "")),
                )
                combo.setItemData(index, updated)
                combo.setCurrentIndex(index)
                return
        label = source
        try:
            relative = os.path.relpath(source, self.project_folder)
            if relative != ".." and not relative.startswith(f"..{os.sep}"):
                label = relative.replace("\\", "/")
        except ValueError:
            pass
        combo.addItem(
            label,
            {
                "origin": "file",
                "kind": adapter.kind,
                "path": source,
                "label": label,
                "manual": bool(layer_info.get("manual")),
                "variable": str(layer_info.get("variable", "")),
                "source_crs": str(layer_info.get("source_crs", "")),
            },
        )
        combo.setCurrentIndex(combo.count() - 1)

    def find_or_load_saved_layer(self, layer_info):
        """Find an existing QGIS layer by source or load it from disk."""
        source = layer_info.get("source", "")
        if not source:
            return None

        found = qgis_layers.find_by_source(source)
        if found:
            return found

        name = layer_info.get("name") or os.path.basename(source.split("|")[0])
        layer = qgis_layers.open_layer(
            source, name, is_raster=layer_info.get("type", "vector") == "raster")
        if layer is None:
            return None

        qgis_layers.add(layer)
        self.log_message(f"Restored input layer: {name} | {source}")
        return layer

    def log_selected_input_paths(self, action_name):
        """Print selected layer/file inputs for easier debugging from QGIS."""
        self.log_message(f"\n--- Input selections before {action_name} ---")
        self.log_message(f"Project folder: {self.project_folder or '<not selected>'}")

        layer_inputs = [
            ("DEM", self.input_combo("dem")),
            ("Pour points", self.input_combo("pour_points")),
            ("Land cover", self.input_combo("land_cover")),
            ("Soil", self.input_combo("soil")),
            ("Geology", self.input_combo("geology")),
        ]

        layer_inputs.append(("LAI class", self.input_combo("lai")))

        for label, combo_box in layer_inputs:
            layer = combo_box.currentLayer()
            if layer:
                self.log_message(f"{label}: {layer.name()} | {layer.source()}")
            else:
                self.log_message(f"{label}: <not selected>")

        for kind, label in (
            ("lc", "Land cover"),
            ("soil", "Soil"),
            ("geology", "Geology"),
        ):
            mode = self.categorical_input_mode(kind) or "<not selected>"
            self.log_message(f"{label} input type: {mode}")
            config = self.categorical_lookup_config(kind)
            if config and mode.lower() == "lookup table":
                self.log_message(
                    f"{label} lookup: {config['lookup_table']} | "
                    f"{config['mapping_field']} -> {config['class_field']}"
                )

        self.log_message(
            f"LAI input type: {self.comboBox_lai_inputType.currentText() or '<not selected>'}"
        )
        if hasattr(self, "lineEdit_lai_file"):
            self.log_message(
                f"LAI file: {self.lineEdit_lai_file.text() or '<not selected>'}"
            )
        for kind, _, _ in self.meteo_input_widgets():
            self.log_message(
                f"{kind.title()} folder: "
                f"{self.selected_meteo_folder(kind) or '<not selected>'}"
            )
            self.log_message(
                f"{kind.title()} data source: "
                f"{self.selected_meteo_source(kind) or '<not selected>'}"
            )

    # --- Project Management Methods ---

    def select_project_folder(self):
        return main_controller.select_project_folder(self)

    def on_tab_changed(self, *args, **kwargs):
        return main_controller.on_tab_changed(self, *args, **kwargs)


    # --- UI Helper Methods ---

    def browse_lai_file(self, *args, **kwargs):
        return main_controller.browse_lai_file(self, *args, **kwargs)

    def get_lai_time_range(self, *args, **kwargs):
        return main_controller.get_lai_time_range(self, *args, **kwargs)

    def get_crs(self, *args, **kwargs):
        return main_controller.get_crs(self, *args, **kwargs)
