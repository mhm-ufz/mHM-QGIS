# -*- coding: utf-8 -*-
"""Interactive per-outlet watershed delineation dialog."""
from __future__ import annotations

import copy
import os
from functools import partial
from pathlib import Path

from qgis.PyQt.QtCore import QEvent, Qt
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import (
    QApplication,
    QDialog,
    QMessageBox,
    QVBoxLayout,
)
from qgis.core import (
    QgsCoordinateTransform,
    QgsGeometry,
    QgsPalettedRasterRenderer,
    QgsProject,
    QgsRasterLayer,
    QgsVectorLayer,
)
from qgis.gui import QgsMapCanvas, QgsMapToolEmitPoint, QgsVertexMarker

from ...core.morphology.hydrology.outlets import (
    OutletAssignment,
    find_outlet_id_field,
    station_id_text,
)
from ...core.handlers.state.domain_state import (
    DOMAIN_MODE_DELINEATOR,
    assign_domain_ids,
    cached_delineation,
    resolve_input_path,
    save_state,
    save_preview,
)
from ...qgis_bridge.layers.domain import DomainWorkflow
from ...core.executions.morphology import delineation
from ...core.handlers.store import registry
from ...core.handlers.store.layout import morph_folder
from ...core.handlers.store.layout import domain_dem_path
from ..bindings.domain_delineator import bind as bind_domain_delineator
from ..ui import load_form
from .file_picker import open_file
from .input_selection import scan_project_inputs

Ui_DomainDelineatorDialog = load_form("domain_delineator_dialog.ui")
from ..objects.viewport_raster_range import ViewportRasterRangeController
from ..reporting import error_detail
from ..workflows import hydrology


MASK_COLOR = QColor(255, 105, 180)
MASK_OPACITY = 0.5


class DomainDelineatorDialog(QDialog, Ui_DomainDelineatorDialog):
    """Configure gauges and delineate one optional domain per outlet."""

    def __init__(
        self,
        main_dialog,
        pour_points_layer,
        outlet_id_field,
        outlet_ids,
        prepared_context=None,
    ):
        super().__init__(main_dialog)
        self.setupUi(self)
        self.main_dialog = main_dialog
        self.project_folder = str(main_dialog.project_folder)
        self.pour_points_layer = pour_points_layer
        self.outlet_id_field = str(outlet_id_field)
        self.outlet_ids = [str(value) for value in outlet_ids]
        self.workflow = DomainWorkflow(
            self.project_folder,
            pour_points_layer,
            outlet_id_field,
            self.outlet_ids,
            log=main_dialog.log_message,
            project_crs=main_dialog.get_crs(),
        )
        self.current_outlet_id = ""
        self._preview_channel_path = ""
        self._map_tool = None
        self._picking = False

        self.canvas = QgsMapCanvas(self.widget_mapCanvasHost)
        self.canvas.setEnabled(True)
        self.widget_mapCanvasHost.setEnabled(True)
        layout = QVBoxLayout(self.widget_mapCanvasHost)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.canvas)
        self._outlet_marker = QgsVertexMarker(self.canvas)
        self._outlet_marker.setIconType(QgsVertexMarker.ICON_CIRCLE)
        self._outlet_marker.setIconSize(22)
        self._outlet_marker.setPenWidth(4)
        self._outlet_marker.setColor(QColor(220, 30, 30))
        self._outlet_marker.setFillColor(QColor(255, 240, 40, 180))
        self._outlet_marker.hide()

        self.state = self.workflow.load_synced_state(DOMAIN_MODE_DELINEATOR, False)
        self._draft_state = copy.deepcopy(self.state)
        for record in self._draft_state["outlets"].values():
            picked = (record.get("delineation") or {}).get("picked")
            if isinstance(picked, dict) and picked.get("source") != "snapped":
                record["picked"] = dict(picked)
                record["confidence"] = None
        self._features = self._outlet_features()
        self._prepare_map_layers(prepared_context)
        self._seed_snapped_outlets()
        bind_domain_delineator(self)
        self.finished.connect(self._cleanup)
        self.listWidget_outlets.addItems(self.outlet_ids)
        self._colour_outlets()
        if self.outlet_ids:
            self.listWidget_outlets.setCurrentRow(0)

    def _outlet_features(self):
        features = {}
        for feature in self.pour_points_layer.getFeatures():
            outlet_id = station_id_text(feature.attribute(self.outlet_id_field))
            if outlet_id:
                features[outlet_id] = feature
        return features

    def _seed_snapped_outlets(self):
        """Snap the pour points once so every outlet opens on a channel cell.

        Automatic locations refresh; a manually picked location is retained.
        Snapping is a convenience,
        not a prerequisite: when it fails the outlets simply start unpicked.
        """
        try:
            self._apply_snapped_outlets()
        except Exception as error:
            self.main_dialog.log_message(
                "WARNING: Domain Delineator could not snap the pour points; "
                f"pick the outlet locations manually ({error})."
            )

    def _apply_snapped_outlets(self):
        layer = QgsVectorLayer(
            self.workflow.regenerate_snapped_points(),
            "Snapped pour points",
            "ogr",
        )
        if not layer.isValid():
            raise RuntimeError("The snapped pour-point layer is invalid.")
        field = find_outlet_id_field(layer, self.outlet_id_field)
        target = self._filled_dem_layer.crs()
        transform = None
        source = layer.crs()
        if source.isValid() and target.isValid() and source != target:
            transform = QgsCoordinateTransform(
                source, target, QgsProject.instance()
            )
            transform.setBallparkTransformsAreAppropriate(True)
        for feature in layer.getFeatures():
            outlet_id = station_id_text(feature.attribute(field))
            record = self._draft_state["outlets"].get(outlet_id)
            if record is None:
                continue
            cached_point = (record.get("delineation") or {}).get("picked")
            saved_point = cached_point or record.get("picked")
            if isinstance(saved_point, dict) and saved_point.get("source") != "snapped":
                record["picked"] = dict(saved_point)
                record["confidence"] = None
                continue
            geometry = QgsGeometry(feature.geometry())
            if geometry.isEmpty():
                continue
            if transform is not None:
                geometry.transform(transform)
            point = geometry.asPoint()
            record["confidence"] = int(feature["confidence"])
            record["snap_count"] = int(feature["snap_count"])
            distance = feature["snap_dist"]
            record["snap_distance_m"] = float(distance) if feature["snap_state"] != "failed" else None
            record["picked"] = {
                "x": float(point.x()),
                "y": float(point.y()),
                "crs": target.authid() or target.toWkt(),
                "source": "snapped",
            }

    def _mask_layer(self, path, name, basin_id):
        """Render a delineation mask as a translucent pink raster.

        Drawing the pyflwdir mask itself avoids a polygonize pass.
        """
        layer = QgsRasterLayer(str(path), name)
        if not layer.isValid():
            return None
        layer.setRenderer(
            QgsPalettedRasterRenderer(
                layer.dataProvider(),
                1,
                [QgsPalettedRasterRenderer.Class(
                    int(basin_id), MASK_COLOR, name)],
            )
        )
        layer.setOpacity(MASK_OPACITY)
        return layer

    def _basin_id(self, outlet_id):
        return self.outlet_ids.index(str(outlet_id)) + 1

    def _prepare_map_layers(self, prepared_context=None):
        if prepared_context is None:
            raise RuntimeError("Domain Delineator prerequisites were not prepared.")
        self._filled_dem_layer = QgsRasterLayer(
            self.workflow.filled_dem_path, "Filled DEM")
        self._flow_accumulation_layer = QgsRasterLayer(
            self.workflow.flow_accumulation_path, "Flow accumulation")
        self._channel_layer = QgsVectorLayer(
            self.workflow.channel_network_path,
            "Channel network",
            "ogr",
        )
        if not self._filled_dem_layer.isValid():
            raise RuntimeError("The prepared filled DEM is invalid.")
        if not self._flow_accumulation_layer.isValid():
            raise RuntimeError("The prepared flow accumulation is invalid.")
        if not self._channel_layer.isValid():
            raise RuntimeError("The prepared channel network is invalid.")
        self.canvas.setDestinationCrs(self._filled_dem_layer.crs())
        self._viewport_range = ViewportRasterRangeController(
            self.canvas,
            self._flow_accumulation_layer,
            self.workflow.flow_accumulation_path,
            self.main_dialog.task_coordinator,
            self,
        )
        self._watershed_layer = None
        self.canvas.setExtent(self._filled_dem_layer.extent())
        self._refresh_canvas()

    def _refresh_canvas(self):
        layers = []
        for layer in (
            self._watershed_layer,
            self.pour_points_layer,
            self._channel_layer,
            self._flow_accumulation_layer,
        ):
            if layer is not None and layer.isValid():
                layers.append(layer)
        self.canvas.setLayers(layers)
        self.canvas.refresh()

    def _colour_outlets(self):
        """Confidence is a static background and tooltip, not an animation."""
        from qgis.PyQt.QtGui import QBrush, QColor

        colours = {0: "#f5a3a3", 1: "#ffe794", 2: "#a8e6a3"}
        for index in range(self.listWidget_outlets.count()):
            item = self.listWidget_outlets.item(index)
            record = self._draft_state["outlets"].get(item.text(), {})
            confidence = record.get("confidence")
            item.setBackground(QBrush(QColor(colours[confidence])) if confidence in colours else QBrush())
            source = record.get("picked", {}).get("source", "default")
            count, distance = record.get("snap_count"), record.get("snap_distance_m")
            detail = f"{count} channel feature(s) within buffer" if count is not None else "Not automatically snapped"
            if distance is not None:
                detail += f"; snap distance {distance:.1f} m"
            item.setToolTip(f"{source}: {detail}")

    def _load_outlet(self, outlet_id):
        self._stage_current_outlet()
        self._stop_picking()
        self.current_outlet_id = str(outlet_id or "")
        if not self.current_outlet_id:
            self._outlet_marker.hide()
            return

        record = self._draft_state["outlets"].get(self.current_outlet_id, {})
        self._channel_layer = QgsVectorLayer(
            self.workflow.channel_network_path,
            "Channel network",
            "ogr",
        )
        self.label_outletIDValue.setText(self.current_outlet_id)
        self.checkBox_isGaugedOutlet.blockSignals(True)
        self.checkBox_isDomainOutlet.blockSignals(True)
        self.checkBox_isGaugedOutlet.setChecked(
            bool(record.get("is_gauged")))
        self.checkBox_isDomainOutlet.setChecked(
            bool(record.get("is_domain")))
        self.checkBox_isGaugedOutlet.blockSignals(False)
        self.checkBox_isDomainOutlet.blockSignals(False)
        self._set_discharge_enabled(
            self.checkBox_isGaugedOutlet.isChecked())
        self.widget_domainControls.setEnabled(True)
        try:
            threshold = max(10, int(record.get("threshold_cells", 1000) or 1000))
        except (TypeError, ValueError):
            threshold = 1000
        self.spinBox_channelThreshold.setValue(threshold)

        discharge = self._resolved_input(record.get("discharge_file"))
        self._populate_discharge_files(discharge)
        area = record.get("catchment_area_m2")
        self._show_area(area)
        self._show_picked_coordinates(record.get("picked"))
        self._show_saved_watershed(record)
        self._zoom_to_outlet()

    def _set_discharge_enabled(self, enabled):
        self.comboBox_dischargeFile.setEnabled(bool(enabled))
        self.pushButton_browseDischargeFile.setEnabled(bool(enabled))

    @staticmethod
    def _normal_path(path):
        return os.path.normcase(os.path.abspath(str(path)))

    def _resolved_input(self, value):
        if not value:
            return ""
        return str(resolve_input_path(self.project_folder, value))

    def _used_discharge_paths(self, exclude_outlet=None):
        paths = set(self.main_dialog.selected_input_file_paths())
        for outlet_id, record in self._draft_state["outlets"].items():
            if outlet_id == exclude_outlet or not record.get("discharge_file"):
                continue
            paths.add(self._normal_path(self._resolved_input(
                record["discharge_file"])))
        return paths

    def _populate_discharge_files(self, current_path=""):
        current = self._normal_path(current_path) if current_path else ""
        excluded = self._used_discharge_paths(self.current_outlet_id)
        self.comboBox_dischargeFile.blockSignals(True)
        self.comboBox_dischargeFile.clear()
        self.comboBox_dischargeFile.addItem("", "")

        available = {}
        for item in scan_project_inputs(self.project_folder, "lookup"):
            path = self._normal_path(item.data["path"])
            if path in excluded:
                continue
            available[path] = item.label
        if current and current not in excluded and os.path.isfile(current):
            available.setdefault(current, self._path_label(current))

        selected_index = 0
        for path, label in sorted(
            available.items(), key=lambda item: item[1].casefold()
        ):
            self.comboBox_dischargeFile.addItem(label, path)
            if path == current:
                selected_index = self.comboBox_dischargeFile.count() - 1
        self.comboBox_dischargeFile.setCurrentIndex(selected_index)
        self.comboBox_dischargeFile.blockSignals(False)

    def _path_label(self, path):
        try:
            return Path(path).resolve().relative_to(
                Path(self.project_folder).resolve()).as_posix()
        except ValueError:
            return str(Path(path).resolve())

    def _browse_discharge(self, _checked=False):
        path, _ = open_file(
            self,
            "Select discharge data",
            "Discharge data (*.csv *.txt)",
        )
        if not path:
            return
        if Path(path).suffix.lower() not in {".csv", ".txt"}:
            QMessageBox.warning(
                self, "Invalid file", "Select a CSV or TXT discharge file.")
            return

        normalized = self._normal_path(path)
        if normalized in self._used_discharge_paths(self.current_outlet_id):
            QMessageBox.warning(
                self,
                "File already selected",
                "This file is already selected elsewhere in the plugin.",
            )
            return
        self._select_discharge_path(normalized)

    def _select_discharge_path(self, path):
        for index in range(self.comboBox_dischargeFile.count()):
            if self.comboBox_dischargeFile.itemData(index) == path:
                self.comboBox_dischargeFile.setCurrentIndex(index)
                return
        self.comboBox_dischargeFile.addItem(self._path_label(path), path)
        self.comboBox_dischargeFile.setCurrentIndex(
            self.comboBox_dischargeFile.count() - 1)

    def _generate_channel_network(self):
        output_folder = self.workflow.domain_output_folder()
        output_path = os.path.join(
            output_folder, "2_channel_network_preview.shp")
        self._channel_layer = QgsVectorLayer(
            self.workflow.channel_network_path,
            "Channel network",
            "ogr",
        )
        self._refresh_canvas()
        threshold = self.spinBox_channelThreshold.value()

        hydrology.start_hydrology(
            self.main_dialog,
            key="domain-channel-preview",
            controls=(
                self.pushButton_generateChannelNetwork,
                self.pushButton_close,
            ),
            channel_path=output_path,
            threshold=threshold,
            direction=False,
            write_flow=False,
            done=lambda result: self._channel_network_ready(
                result["channel_network"]
            ),
            failed=lambda message: self._show_error(
                "Channel Network", error_detail(message)
            ),
        )

    def _channel_network_ready(self, result):
        self._preview_channel_path = str(result)
        self._channel_layer = QgsVectorLayer(
            str(result), "Channel network preview", "ogr"
        )
        if not self._channel_layer.isValid():
            self._show_error("Channel Network", "The generated channel network is invalid.")
            return
        self._refresh_canvas()

    def _start_picking(self):
        if not self.current_outlet_id:
            return
        self._stop_picking()
        self._map_tool = QgsMapToolEmitPoint(self.canvas)
        self._map_tool.canvasClicked.connect(self._point_picked)
        self.canvas.setMapTool(self._map_tool)
        self.canvas.setCursor(Qt.CursorShape.CrossCursor)
        self.pushButton_pickLocation.setText("Click on map…")
        self._picking = True
        QApplication.instance().installEventFilter(self)

    def eventFilter(self, watched, event):
        if self._picking and event.type() == QEvent.Type.MouseButtonPress:
            position = self.canvas.mapFromGlobal(event.globalPos())
            if not self.canvas.rect().contains(position):
                self._stop_picking()
                QMessageBox.warning(
                    self,
                    "Pick location",
                    "Click inside the map canvas to select an outlet location.",
                )
                return True
        return super().eventFilter(watched, event)

    def _stop_picking(self):
        if not self._picking and self._map_tool is None:
            return
        self._picking = False
        application = QApplication.instance()
        if application is not None:
            application.removeEventFilter(self)
        if self._map_tool is not None:
            try:
                self._map_tool.canvasClicked.disconnect(self._point_picked)
            except (TypeError, RuntimeError):
                pass
            self.canvas.unsetMapTool(self._map_tool)
        self._map_tool = None
        self.canvas.unsetCursor()
        self.pushButton_pickLocation.setText("Update location")

    def _point_picked(self, point, button):
        if button != Qt.MouseButton.LeftButton:
            return
        self._stop_picking()
        record = self._draft_state["outlets"][self.current_outlet_id]
        record["picked"] = {"x": point.x(), "y": point.y(),
                            "crs": self._filled_dem_layer.crs().authid() or self._filled_dem_layer.crs().toWkt(),
                            "source": "manual"}
        record["confidence"] = None
        record.pop("snap_count", None)
        record.pop("snap_distance_m", None)
        self._show_picked_coordinates(record["picked"])
        self._show_saved_watershed(record)
        self._zoom_to_outlet()
        self._colour_outlets()

    def _outlet_options(self, outlet_id, state=None):
        record = (state or self._draft_state)["outlets"][outlet_id]
        picked = record.get("picked") or self._default_gauge_point(outlet_id)
        x, y = self._picked_in_dem_crs(picked)
        return {"outlet_id": outlet_id, "x": x, "y": y, "picked": dict(picked),
                "confidence": record.get("confidence"), "snap_count": record.get("snap_count"),
                "snap_distance_m": record.get("snap_distance_m"),
                "basin_id": self._basin_id(outlet_id), "domain_id": record.get("domain_id"),
                "raster_path": self.workflow.outlet_mask_path(outlet_id, self._draft_state), "cached": record.get("delineation")}

    def _show_delineation(self):
        """Only this action (or Save) requests an uncached watershed."""
        outlet_id = self.current_outlet_id
        if not outlet_id:
            return
        try:
            options = self._outlet_options(outlet_id)
        except (ValueError, RuntimeError) as error:
            self._show_error("Watershed Delineation", error)
            return
        self.main_dialog.task_coordinator.submit(
            "domain-watershed-preview",
            f"Preview watershed {outlet_id}",
            partial(delineation.show, project_folder=self.project_folder,
                    filled_dem=self.workflow.filled_dem_path, outlet=options),
            controls=(
                self.pushButton_showDelineation,
                self.pushButton_pickLocation,
                self.pushButton_generateChannelNetwork,
                self.pushButton_nextPourPoint,
                self.pushButton_save,
                self.listWidget_outlets,
                self.pushButton_close,
            ),
            on_success=lambda result: self._watershed_preview_ready(
                outlet_id, result
            ),
            on_error=lambda message: self._show_error(
                "Watershed Delineation", error_detail(message)
            ),
            resource="morphology-files",
        )

    def _watershed_preview_ready(self, outlet_id, result):
        if outlet_id != self.current_outlet_id:
            return
        try:
            save_preview(self.project_folder, self.state["pour_points_source"],
                         self.outlet_id_field, outlet_id, result)
            record = self._draft_state["outlets"][outlet_id]
            record["delineation"] = result
            record["picked"] = dict(result["picked"])
            self._show_saved_watershed(record)
        except Exception as error:
            self._show_error("Watershed Delineation", error)

    def _stage_current_outlet(self):
        """Retain widget values only; file validation belongs to Save."""
        if not self.current_outlet_id:
            return
        record = self._draft_state["outlets"][self.current_outlet_id]
        record["is_gauged"] = self.checkBox_isGaugedOutlet.isChecked()
        record["is_domain"] = self.checkBox_isDomainOutlet.isChecked()
        record["discharge_file"] = str(self.comboBox_dischargeFile.currentData() or "")
        record["threshold_cells"] = self.spinBox_channelThreshold.value()
        self._draft_state["dem_domain"] = False
        assign_domain_ids(self._draft_state)

    def _default_gauge_point(self, outlet_id):
        feature = self._features.get(str(outlet_id))
        if feature is None or feature.geometry().isEmpty():
            raise ValueError(f"Gauge outlet {outlet_id} has no default point location.")
        geometry = QgsGeometry(feature.geometry())
        source = self.pour_points_layer.crs()
        target = self._filled_dem_layer.crs()
        if source.isValid() and target.isValid() and source != target:
            transform = QgsCoordinateTransform(
                source, target, QgsProject.instance()
            )
            transform.setBallparkTransformsAreAppropriate(True)
            geometry.transform(transform)
        point = geometry.asPoint()
        if point.isEmpty():
            point = geometry.centroid().asPoint()
        return {
            "x": float(point.x()),
            "y": float(point.y()),
            "crs": target.authid() or target.toWkt(),
            "source": "default",
        }

    def _next_outlet(self, _checked=False):
        self._stage_current_outlet()
        row = self.listWidget_outlets.currentRow()
        if row + 1 < self.listWidget_outlets.count():
            self.listWidget_outlets.setCurrentRow(row + 1)

    def _all_assignments(self):
        assignments = []
        for outlet_id in self.outlet_ids:
            record = self._draft_state["outlets"].get(outlet_id, {})
            discharge_path = self._resolved_input(record.get("discharge_file"))
            assignments.append(
                OutletAssignment(
                    outlet_id=outlet_id,
                    is_gauge=bool(record.get("is_gauged", False)),
                    is_domain=bool(record.get("is_domain", False)),
                    discharge_layer=(
                        QgsVectorLayer(
                            discharge_path,
                            Path(discharge_path).name,
                            "ogr",
                        )
                        if record.get("is_gauged") and discharge_path
                        else None
                    ),
                )
            )
        return assignments

    def _save_outlet(self):
        try:
            self._stage_current_outlet()
            proposed_state = copy.deepcopy(self._draft_state)
            assignments = self._all_assignments()
            prepared = self.workflow.validate_gauge_assignments(assignments)
            previously_gauged = {
                outlet_id
                for outlet_id, record in self.state.get("outlets", {}).items()
                if record.get("is_gauged", False)
            }
            self.workflow.apply_assignment_records(
                proposed_state, assignments, prepared
            )
            assign_domain_ids(proposed_state)
            self.workflow.require_active_domain(proposed_state)
            self.workflow.validate_unique_state_gauge_ids(proposed_state)
            outlets = [self._outlet_options(outlet_id, proposed_state) for outlet_id in self.outlet_ids]
            dem_domain = None
            if proposed_state.get("dem_domain"):
                dem_domain = (
                    int(proposed_state["dem_domain_id"]),
                    self.workflow.dem_mask_path(),
                    domain_dem_path(self.project_folder, "dem_extent"),
                )
            merged_path = DomainWorkflow.merged_mask_path(self.project_folder)
            self._watershed_layer = None
            self._refresh_canvas()
            options = {
                "project_folder": self.project_folder,
                "filled_dem": self.workflow.filled_dem_path,
                "outlets": outlets,
                "dem_domain": dem_domain,
                "merged_path": merged_path,
            }
            started = self.main_dialog.task_coordinator.submit(
                "domain-final-save",
                "Save all domain and gauge inputs",
                partial(delineation.save, **options),
                controls=(
                    self.pushButton_showDelineation,
                    self.pushButton_nextPourPoint,
                    self.pushButton_save,
                    self.pushButton_pickLocation,
                    self.pushButton_generateChannelNetwork,
                    self.listWidget_outlets,
                    self.pushButton_close,
                ),
                on_success=lambda result: self._domain_files_ready(
                        result,
                        proposed_state,
                        prepared,
                        previously_gauged,
                ),
                on_error=lambda message: self._show_error(
                    "Save Domain Inputs", error_detail(message)
                ),
                resource="morphology-files",
            )
            if not started:
                raise RuntimeError("Domain saving is already running.")
        except Exception as error:
            self._show_error("Save Domain Inputs", error)

    def _commit_domain_state(
        self,
        result,
        proposed_state,
        prepared,
        previously_gauged,
    ):
        for outlet_id, saved in result["outlets"].items():
            record = proposed_state["outlets"][outlet_id]
            record.update(
                {
                    "picked": dict(saved["picked"]),
                    "delineation": dict(saved),
                    "mask_value": saved["mask_value"],
                    "catchment_area_m2": saved["catchment_area_m2"],
                    "mask_path": saved["raster_path"],
                    "domain_directory": morph_folder(self.project_folder),
                    "dem_path": domain_dem_path(self.project_folder, outlet_id),
                }
            )
            if record.get("is_gauged"):
                record["gauge_point"] = dict(record["picked"])
            else:
                record.pop("gauge_point", None)
        proposed_state["merged_mask_path"] = result["merged_path"]
        proposed_state["dem_mask_path"] = result.get("dem_mask_path") or ""
        if result.get("dem_domain_path"):
            proposed_state["dem_domain_directory"] = morph_folder(self.project_folder)
            proposed_state["dem_domain_path"] = result["dem_domain_path"]
        registry.register(
            self.project_folder,
            result["merged_path"],
            name="4_watershed_merged",
            loaded=False,
        )
        self.workflow.update_gauge_domain_ids(
            proposed_state, self.pour_points_layer
        )
        self.workflow.write_gauges(prepared)
        save_state(self.project_folder, proposed_state)
        self.workflow.remove_deselected_gauges(
            previously_gauged, proposed_state
        )
        from ...core.handlers.state.nml_settings import sync_domain_settings

        sync_domain_settings(self.project_folder)
        self._domain_state_saved(proposed_state)

    def _domain_files_ready(
        self,
        result,
        proposed_state,
        prepared,
        previously_gauged,
    ):
        try:
            self._commit_domain_state(
                result,
                proposed_state,
                prepared,
                previously_gauged,
            )
        except Exception as error:
            self._show_error("Save Domain Inputs", error)

    def _domain_state_saved(self, proposed_state):
        self.state = proposed_state
        self._draft_state = copy.deepcopy(proposed_state)
        self.main_dialog.save_input_state()
        self.main_dialog.update_gauged_outlet_count()
        self._show_saved_watershed(
            proposed_state["outlets"].get(self.current_outlet_id, {})
        )
        QMessageBox.information(
            self, "Domain Delineator", "Saved all pour-point inputs."
        )

    def _picked_in_dem_crs(self, picked):
        x = float(picked["x"])
        y = float(picked["y"])
        authid = str(picked.get("crs", "") or "")
        target = self._filled_dem_layer.crs()
        if not authid or authid == target.authid():
            return x, y

        from qgis.core import QgsCoordinateReferenceSystem, QgsPointXY

        source = QgsCoordinateReferenceSystem(authid)
        if not source.isValid():
            raise ValueError("The saved outlet coordinate CRS is invalid.")
        transform = QgsCoordinateTransform(
            source, target, QgsProject.instance())
        point = transform.transform(QgsPointXY(x, y))
        return point.x(), point.y()

    def _show_saved_watershed(self, record):
        self._watershed_layer = None
        result = None
        try:
            options = self._outlet_options(self.current_outlet_id)
            result = cached_delineation(self.project_folder, self.workflow.filled_dem_path,
                                        options["x"], options["y"], record.get("delineation"))
            if result:
                self._watershed_layer = self._mask_layer(
                    result["raster_path"],
                    f"Watershed {self.current_outlet_id}",
                    result["mask_value"],
                )
        except (KeyError, TypeError, ValueError):
            pass
        self._show_area(result["catchment_area_m2"] if result else None)
        self._refresh_canvas()

    def _show_area(self, value):
        try:
            area = float(value)
        except (TypeError, ValueError):
            self.label_catchmentAreaValue.setText("-")
            return
        self.label_catchmentAreaValue.setText(
            f"{area / 1_000_000.0:.3f}")

    def _show_picked_coordinates(self, picked):
        if not isinstance(picked, dict):
            self.label_pourPointUpdatedCoordinatesValue.setText("-")
            return
        try:
            x = float(picked["x"])
            y = float(picked["y"])
        except (KeyError, TypeError, ValueError):
            self.label_pourPointUpdatedCoordinatesValue.setText("-")
            return
        authid = str(picked.get("crs", "") or "")
        suffix = f" ({authid})" if authid else ""
        self.label_pourPointUpdatedCoordinatesValue.setText(
            f"{x:.3f}, {y:.3f}{suffix}"
        )

    def _zoom_to_outlet(self):
        from qgis.core import QgsPointXY

        record = self._draft_state["outlets"].get(self.current_outlet_id, {})
        try:
            picked = record.get("picked") or self._default_gauge_point(self.current_outlet_id)
            marker_point = QgsPointXY(*self._picked_in_dem_crs(picked))
        except (ValueError, KeyError, TypeError):
            self._outlet_marker.hide()
            return
        geometry = QgsGeometry.fromPointXY(marker_point)
        self._outlet_marker.setCenter(marker_point)
        self._outlet_marker.show()

        extent = geometry.boundingBox()
        padding = max(
            self._filled_dem_layer.extent().width(),
            self._filled_dem_layer.extent().height(),
        ) / 40.0
        extent.grow(padding if padding > 0 else 1.0)
        self.canvas.setExtent(extent)
        self.canvas.refresh()

    def _show_error(self, title, error):
        self.main_dialog.log_message(f"ERROR: {title}: {error}")
        if not self.main_dialog._closing:
            QMessageBox.critical(self, title, str(error))

    def _cleanup(self, *_args):
        self._stop_picking()
        controller = getattr(self, "_viewport_range", None)
        if controller is not None:
            controller.close()
            self._viewport_range = None
        if self._outlet_marker is not None:
            self._outlet_marker.hide()
            try:
                self.canvas.scene().removeItem(self._outlet_marker)
            except RuntimeError:
                pass
            self._outlet_marker = None
        self.canvas.setLayers([])

    def closeEvent(self, event):
        if self._domain_jobs_busy():
            event.ignore()
            QMessageBox.information(
                self,
                "Processing",
                "Wait for the active Domain Delineator task to finish.",
            )
            return
        self._cleanup()
        super().closeEvent(event)

    def reject(self):
        if self._domain_jobs_busy():
            QMessageBox.information(
                self,
                "Processing",
                "Wait for the active Domain Delineator task to finish.",
            )
            return
        super().reject()

    def _domain_jobs_busy(self):
        coordinator = self.main_dialog.task_coordinator
        return any(
            coordinator.is_busy(key)
            for key in (
                "domain-channel-preview",
                "domain-watershed-preview",
                "domain-final-save",
            )
        )
