"""Qt scheduler for background morphology API commands."""
from __future__ import annotations

import os
import shutil
from functools import partial
from pathlib import Path
from tempfile import TemporaryDirectory

from qgis.PyQt import QtCore
from qgis.PyQt.QtWidgets import QMessageBox
from qgis.core import QgsVectorLayer

from ...core.executions import morphology
from ...core.executions.morphology import commands
from ...core.handlers.store.paths import (
    geometry_folder,
    morph_staging_folder,
)
from ...core.handlers.state import processing
from ...core.handlers.state import settings as project_settings
from ...core.handlers.store import registry
from ...core.morphology.hydrology.outlets import configured_gauged_outlet_ids
from ...core.morphology.layers import categorical, lai
from ...core.morphology.layers.categorical import SPECS
from ...qgis_bridge import layers
from ...qgis_bridge.layers.domain import snap_points_to_network
from ..reporting import error_detail


# Worker implementations are public core commands. These aliases keep the
# scheduler's call sites compact and make it obvious that no computation lives
# in the Qt package.
_saved_categorical_outputs = commands.categorical_outputs
_run_fill = commands.fill
_run_dem_derivatives = commands.dem_derivatives
_run_hydrology = commands.hydrology
_run_terrain = commands.terrain
_run_lai = commands.lai
_run_lookup = commands.lookup
_run_advanced = commands.advanced


class MorphologyTaskBridge(QtCore.QObject):
    """Schedule heavy work without passing Qt or QGIS objects to workers."""

    def __init__(self, dialog):
        super().__init__(dialog)
        self.dialog = dialog
        self.coordinator = dialog.task_coordinator
        self.execute_all_active = False

    def path(self, name: str) -> str:
        """Return a fixed morphology workspace path."""
        return str(Path(geometry_folder(self.dialog.project_folder)) / name)

    @property
    def filled_dem_path(self) -> str:
        return self.path("1_dem_filled.tif")

    @property
    def snapped_points_path(self) -> str:
        return self.path("2_pour_points_snapped.shp")

    @property
    def channel_network_path(self) -> str:
        return self.path("2_channel_network.shp")

    @property
    def gauge_position_path(self) -> str:
        return self.path("2_gauge_position.tif")

    def _register(self, path, *, name=None, loaded=False, algorithm=None):
        return registry.register(
            self.dialog.project_folder,
            path,
            name=name or Path(path).name,
            loaded=loaded,
            algorithm=algorithm,
        )

    def _load(self, path, name, *, is_raster=True):
        self._register(path, name=name, loaded=True)
        return layers.load(
            path,
            name,
            is_raster=is_raster,
            log=self.dialog.log_message,
        )

    def _selected_source(self, kind: str) -> str:
        combo = self.dialog.input_combo(kind)
        source_path = getattr(combo, "source_path", None)
        if callable(source_path):
            value = source_path()
            if value:
                return str(value)
        layer = combo.currentLayer()
        return str(layer.source() or "") if layer is not None else ""

    @staticmethod
    def _crs_text(crs) -> str:
        if crs is None or not crs.isValid():
            return ""
        return crs.authid() or crs.toWkt()

    def _controls(self, controls):
        controls = tuple(controls)
        if controls:
            controls += (self.dialog.pushButton_BrowseProjectFolder,)
        return controls

    def _error(self, label, message, callback=None):
        detail = error_detail(message)
        # The dialog gets the one useful line; the log keeps the whole
        # traceback the worker sent, so nothing is lost.
        self.dialog.log_message(f"ERROR: {label}: {message}")
        if callback is not None:
            callback(detail)
        else:
            QMessageBox.critical(self.dialog, label, detail)

    def _dem_options(self):
        if not self.dialog.check_prerequisites():
            raise ValueError("Select a project folder and DEM first.")
        layer = self.dialog.input_combo("dem").currentLayer()
        source = str(layer.source() or "")
        if not source:
            raise ValueError("The selected DEM has no readable source.")
        source_crs = layer.crs()
        target_crs = self.dialog.get_crs()
        folder = Path(geometry_folder(self.dialog.project_folder))
        return {
            "source": source,
            "output_path": str(folder / "1_dem_filled.tif"),
            "source_crs": (
                source_crs.authid() or source_crs.toWkt()
                if source_crs is not None and source_crs.isValid()
                else ""
            ),
            "target_crs": (
                target_crs.authid() or target_crs.toWkt()
                if target_crs is not None and target_crs.isValid()
                else ""
            ),
            "reprojected_path": str(folder / "0_dem_reprojected.tif"),
        }

    def prepare_filled_dem(self) -> str:
        """Synchronously ensure the fixed filled-DEM prerequisite exists.

        This is used only by GUI calculations that need the grid immediately;
        processing buttons and Execute All use ``start_fill`` instead.
        """
        options = self._dem_options()
        result = _run_fill(None, options)
        path = str(result["filled_dem"])
        self._register(path, name="1_DEM_Filled", loaded=False)
        if not result.get("reused"):
            self.dialog.log_message(
                f'DEM filled successfully ({result.get("filled_cells", 0)} adjusted cells).'
            )
        return path

    def start_fill(
        self,
        *,
        key="fill-dem",
        controls=(),
        load=True,
        done=None,
        failed=None,
    ):
        try:
            options = self._dem_options()
        except Exception as error:
            self._error("Fill DEM", error, failed)
            return False
        started = self.coordinator.submit(
            key,
            "Fill DEM",
            partial(_run_fill, options=options),
            controls=self._controls(controls),
            resource="morphology-files",
            task_aware=True,
            on_success=lambda result: self._filled(result, load, done),
            on_error=lambda message: self._error("Fill DEM", message, failed),
        )
        return started

    def _filled(self, result, load, done):
        path = str(result["filled_dem"])
        self._register(path, name="1_DEM_Filled", loaded=False)
        if load:
            self._load(path, "1_DEM_Filled")
        if not result.get("reused"):
            self.dialog.log_message(
                f'DEM filled successfully ({result.get("filled_cells", 0)} adjusted cells).'
            )
        self.dialog.update_l0_resolution_from_dem()
        if done is not None:
            done(result)

    def _hydrology_options(
        self,
        *,
        channel_path=None,
        threshold=None,
        direction=True,
        include_channel=True,
        write_flow=True,
        accumulation=True,
    ):
        folder = Path(geometry_folder(self.dialog.project_folder))
        return {
            "filled_dem": self.filled_dem_path,
            "accumulation_path": (
                str(folder / "2_flow_accumulation.tif")
                if write_flow and accumulation
                else ""
            ),
            "area_path": (
                str(folder / "2_flow_accumulation_area.tif") if write_flow else ""
            ),
            "direction_path": str(folder / "2_flow_direction.tif") if direction else "",
            "channel_path": (
                str(channel_path or folder / "2_channel_network.shp")
                if include_channel
                else ""
            ),
            "threshold_cells": threshold,
        }

    def start_hydrology(
        self,
        *,
        key="prepare-hydrology",
        controls=(),
        load="",
        channel_path=None,
        threshold=None,
        direction=True,
        include_channel=True,
        write_flow=True,
        accumulation=True,
        done=None,
        failed=None,
    ):
        if not os.path.isfile(self.filled_dem_path):
            return self.start_fill(
                key=f"{key}-dem",
                controls=controls,
                load=False,
                done=lambda _result: self.start_hydrology(
                    key=key,
                    controls=controls,
                    load=load,
                    channel_path=channel_path,
                    threshold=threshold,
                    direction=direction,
                    include_channel=include_channel,
                    write_flow=write_flow,
                    accumulation=accumulation,
                    done=done,
                    failed=failed,
                ),
                failed=failed,
            )
        options = self._hydrology_options(
            channel_path=channel_path,
            threshold=threshold,
            direction=direction,
            include_channel=include_channel,
            write_flow=write_flow,
            accumulation=accumulation,
        )
        publish_channel = not channel_path
        return self.coordinator.submit(
            key,
            "Prepare flow and channel files",
            partial(_run_hydrology, options=options),
            controls=self._controls(controls),
            resource="morphology-files",
            task_aware=True,
            on_success=lambda result: self._hydrology_ready(
                result, load, done, publish_channel
            ),
            on_error=lambda message: self._error("Hydrology", message, failed),
        )

    def _hydrology_ready(self, result, load, done, publish_channel=True):
        mapping = {
            "flow_accumulation": ("2_Flow_Accumulation", True),
            "flow_accumulation_area": ("2_Flow_Accumulation_Area", True),
            "flow_direction": ("2_Flow_Direction", True),
            "channel_network": ("2_Channel_Network", False),
        }
        for key, path in result.items():
            if key == "channel_network" and not publish_channel:
                continue
            name, is_raster = mapping[key]
            self._register(str(path), name=name, loaded=False)
            if load == key:
                self._load(str(path), name, is_raster=is_raster)
        if done is not None:
            done(result)

    def start_snap_points(
        self,
        *,
        key="snap-pour-points",
        controls=(),
        load=False,
        done=None,
        failed=None,
    ):
        """Run the QGIS layer-bound snapping command on the GUI thread."""
        pour_points = self.dialog.input_combo("pour_points").currentLayer()
        if pour_points is None:
            self._error("Snap Pour Points", "Select a pour-points layer first.", failed)
            return False
        if not Path(self.channel_network_path).is_file():
            return self.start_hydrology(
                key=f"{key}-hydrology",
                controls=controls,
                load="",
                direction=False,
                done=lambda _result: self.start_snap_points(
                    key=key,
                    controls=controls,
                    load=load,
                    done=done,
                    failed=failed,
                ),
                failed=failed,
            )
        try:
            channel = layers.open_layer(
                self.channel_network_path,
                "2_Channel_Network",
                is_raster=False,
            )
            if channel is None:
                raise RuntimeError("The prepared channel network is invalid.")
            result = snap_points_to_network(
                pour_points,
                channel,
                self.snapped_points_path,
                project_crs=self.dialog.get_crs(),
                max_snap_buffer_distance=project_settings.read(self.dialog.project_folder)["max_snap_buffer_distance"],
                log=self.dialog.log_message,
            )
            self._register(result, name="2_pour_points_snapped", loaded=False)
            if load:
                self._load(result, "2_Pour_Points_Snapped", is_raster=False)
            if done is not None:
                QtCore.QTimer.singleShot(0, lambda: done(result))
            return True
        except Exception as error:
            self._error("Snap Pour Points", error, failed)
            return False

    def start_gauge_position(
        self,
        *,
        key="gauge-position",
        controls=(),
        load=False,
        done=None,
        failed=None,
    ):
        """Extract QGIS gauge points, then burn them in a file-only worker."""
        if not Path(self.snapped_points_path).is_file():
            return self.start_snap_points(
                key=f"{key}-snap",
                controls=controls,
                load=False,
                done=lambda _result: self.start_gauge_position(
                    key=key,
                    controls=controls,
                    load=load,
                    done=done,
                    failed=failed,
                ),
                failed=failed,
            )
        try:
            gauges = layers.gauge_coordinates(
                self.dialog.project_folder,
                self.snapped_points_path,
                self.filled_dem_path,
                preferred_field=self.dialog.selected_outlet_id_field(),
            )
        except Exception as error:
            self._error("Gauge Position", error, failed)
            return False
        options = {
            "filled_dem": self.filled_dem_path,
            "gauges": gauges,
            "output_path": self.gauge_position_path,
        }
        return self.coordinator.submit(
            key,
            "Prepare gauge positions",
            partial(commands.gauge_position, options=options),
            controls=self._controls(controls),
            resource="morphology-files",
            task_aware=True,
            on_success=lambda result: self._gauge_position_ready(
                result, load, done
            ),
            on_error=lambda message: self._error(
                "Gauge Position", message, failed
            ),
        )

    def _gauge_position_ready(self, result, load, done):
        result = str(result)
        self._register(result, name="2_Gauge_Position", loaded=False)
        if load:
            self._load(result, "2_Gauge_Position")
        if done is not None:
            done(result)

    def start_dem_derivatives(
        self,
        *,
        key="execute-all-dem-derivatives",
        controls=(),
        load=False,
        done=None,
        failed=None,
    ):
        """Fill the DEM and derive slope, aspect, facc and fdir in one pass."""
        try:
            options = self._dem_options()
        except Exception as error:
            self._error("DEM derivatives", error, failed)
            return False
        options.pop("output_path", None)
        options["output_folder"] = str(geometry_folder(self.dialog.project_folder))
        options["log"] = self.dialog.log_message
        return self.coordinator.submit(
            key,
            "Prepare DEM derivatives",
            partial(_run_dem_derivatives, options=options),
            controls=self._controls(controls),
            resource="morphology-files",
            task_aware=True,
            on_success=lambda result: self._dem_derivatives_ready(result, load, done),
            on_error=lambda message: self._error("DEM derivatives", message, failed),
        )

    def _dem_derivatives_ready(self, result, load, done):
        self._filled(result, load, None)
        for key in ("slope", "aspect", "flow_accumulation", "flow_direction"):
            self._register(
                result[key], name=Path(result[key]).name, loaded=False
            )
        if done is not None:
            done(result)

    def start_terrain(
        self,
        *,
        key="execute-all-terrain",
        controls=(),
        load="",
        done=None,
        failed=None,
    ):
        folder = Path(geometry_folder(self.dialog.project_folder))
        layer = self.dialog.input_combo("dem").currentLayer()
        scale = 111200.0 if layer.crs().isValid() and layer.crs().isGeographic() else 1.0
        options = {
            "filled_dem": self.filled_dem_path,
            "slope_path": str(folder / "1_dem_slope.tif"),
            "aspect_path": str(folder / "1_dem_aspect.tif"),
            "scale": scale,
        }
        return self.coordinator.submit(
            key,
            "Prepare slope and aspect",
            partial(_run_terrain, options=options),
            controls=self._controls(controls),
            resource="morphology-files",
            task_aware=True,
            on_success=lambda result: self._terrain_ready(result, load, done),
            on_error=lambda message: self._error("Terrain", message, failed),
        )

    def _terrain_ready(self, result, load, done):
        for path in result.values():
            self._register(path, name=Path(path).name, loaded=False)
        if load in result:
            self._load(
                result[load], "1_DEM_Slope" if load == "slope" else "1_DEM_Aspect"
            )
        if done is not None:
            done(result)

    def start_lai(
        self,
        *,
        key="execute-all-lai",
        controls=(),
        done=None,
        failed=None,
    ):
        """Resample the selected LAI NetCDF onto the filled DEM grid."""
        if "netcdf" not in self.dialog.categorical_input_mode("lai").lower():
            self.dialog.log_message(
                "No LAI NetCDF input is selected. Skipping LAI.")
            if done is not None:
                done(None)
            return True
        config = self.dialog.lai_netcdf_config()
        source = str(config.get("input_path", "") or self._selected_source("lai"))
        options = lai.task_options(
            self.dialog.project_folder,
            source,
            self.filled_dem_path,
            crs=self._crs_text(self.dialog.get_crs()),
            target_timestep=config.get("target_timestep") or lai.DEFAULT_TIMESTEP,
        )
        if options is None:
            self.dialog.log_message(
                "LAI NetCDF input is not selected. Skipping LAI.")
            if done is not None:
                done(None)
            return True
        os.makedirs(os.path.dirname(options["output_path"]), exist_ok=True)
        digest = self._stage_digest(
            (options["source_path"], options["filled_dem"]),
            {
                "source_variable": options["source_variable"],
                "target_timestep": options["target_timestep"],
                "method": options.get("method") or "bilinear",
                "output_path": options["output_path"],
            },
        )
        if self._reusable_stage(
                "lai",
                digest,
                inputs=(options["source_path"], options["filled_dem"]),
                outputs=(options["output_path"],),
        ) is not None:
            self.dialog.log_message(
                "LAI inputs are unchanged. Reusing the staged DEM-grid LAI."
            )
            self._register(
                options["output_path"],
                name=Path(options["output_path"]).name,
                loaded=False,
            )
            if done is not None:
                QtCore.QTimer.singleShot(
                    0, lambda: done(options["output_path"]))
            return True
        self.dialog.log_message(
            "Resampling LAI to the filled DEM grid with bilinear interpolation."
        )
        messages = []
        return self.coordinator.submit(
            key,
            "Resample LAI to the filled DEM grid",
            partial(_run_lai, options=options, messages=messages),
            controls=self._controls(controls),
            resource="morphology-files",
            task_aware=True,
            on_success=lambda result: self._lai_ready(
                result, messages, done, digest=digest),
            on_error=lambda message: self._error("LAI", message, failed),
        )

    def _lai_ready(self, result, messages, done, digest=None):
        for message in messages:
            self.dialog.log_message(message)
        if result:
            self._register(
                result, name=Path(result).name, loaded=False)
            if digest:
                self._record_stage("lai", digest, (result,))
            self.dialog.log_message(
                f"LAI resampled to the filled DEM grid: {result}")
        if done is not None:
            done(result)

    def _stage_digest(self, inputs, config):
        """Return the fingerprint of one stage's inputs and configuration."""
        return commands.stage_digest(inputs, config)

    def _reusable_stage(self, stage, digest, inputs=(), outputs=()):
        """Return the outputs to reuse, recording them when newly adopted.

        A recorded fingerprint that still matches is reused directly. Failing
        that, outputs already on disk are adopted when they postdate every input,
        which is how a project prepared before the cache existed avoids one
        pointless rebuild.
        """
        payload = commands.reusable_stage(
            self.dialog.project_folder,
            stage,
            digest,
            inputs=inputs,
            outputs=outputs,
        )
        if payload and payload.get("adopted"):
            self.dialog.log_message(
                f"Adopted the existing {stage} output into the project cache."
            )
        return payload

    def _record_stage(self, stage, digest, outputs):
        """Record which outputs a stage produced for the given inputs."""
        commands.record_stage(self.dialog.project_folder, stage, digest, outputs)

    def start_categorical(
        self,
        kind,
        *,
        key=None,
        controls=(),
        load=False,
        reuse_existing=False,
        done=None,
        failed=None,
    ):
        if not os.path.isfile(self.filled_dem_path):
            return self.start_fill(
                key=f"{key or kind}-dem",
                controls=controls,
                load=False,
                done=lambda _result: self.start_categorical(
                    kind,
                    key=key,
                    controls=controls,
                    load=load,
                    reuse_existing=reuse_existing,
                    done=done,
                    failed=failed,
                ),
                failed=failed,
            )
        if reuse_existing:
            outputs = _saved_categorical_outputs(self.dialog.project_folder, kind)
            if outputs:
                self.dialog.log_message(
                    f"Reusing prepared {SPECS[kind]['label']} output."
                )
                self._categorical_complete(kind, outputs, reused=True)
                if done is not None:
                    QtCore.QTimer.singleShot(
                        0,
                        lambda: done(
                            {"kind": kind, "outputs": tuple(map(str, outputs))}
                        ),
                    )
                return True
        try:
            mode = categorical.normalized_mode(
                self.dialog.categorical_input_mode(kind)
            )
            if mode == "mhm_ready":
                config = self.dialog.categorical_source_config(kind) or {}
                source = config.get("input_path") or self._selected_source(
                    SPECS[kind]["kind"]
                )
                outputs = categorical.copy_ready(
                    self.dialog.project_folder,
                    kind,
                    source,
                    definition_source=config.get("classdefinition_path", ""),
                    mode=mode,
                )
                for output in outputs:
                    self._register(output, loaded=False)
                if load:
                    self._load(outputs[0], SPECS[kind]["layer_name"])
                if kind == "soil":
                    definition = next(
                        (path for path in outputs if path.suffix == ".txt"), None
                    )
                    self.dialog.record_standard_soil_output(
                        str(outputs[0]), str(definition) if definition else None
                    )
                self._categorical_complete(kind, outputs)
                if done is not None:
                    QtCore.QTimer.singleShot(
                        0,
                        lambda: done(
                            {"kind": kind, "outputs": tuple(map(str, outputs))}
                        ),
                    )
                return True
            if self.dialog.uses_advanced_categorical_input(kind):
                value = (
                    self.dialog._land_use_input_value(
                        self.dialog._advanced_inputs.get("land_cover")
                    )
                    if kind == "lc"
                    else self.dialog._soil_input_value(
                        self.dialog._advanced_inputs.get("soil")
                    )
                )
                job = {
                    "kind": kind,
                    "project_folder": str(self.dialog.project_folder),
                    "version": self.dialog.comboBox_mHMversion.currentText().strip(),
                    "value": value,
                    "filled_dem": self.filled_dem_path,
                }
                runner = partial(_run_advanced, job=job)
                finish = lambda result: self._advanced_ready(result, done)
            else:
                if mode != "lookup_table":
                    raise ValueError(
                        f"Select an input type for {SPECS[kind]['label']}."
                    )
                job = self._lookup_job(kind)
                digest = self._stage_digest(
                    (job["input_file"], job["filled_dem"], job["lookup_table"]),
                    {
                        "kind": kind,
                        "mapping_field": job["mapping_field"],
                        "class_field": job["class_field"],
                        "is_vector": job["is_vector"],
                        "input_crs": job["input_crs"],
                        "dem_crs": job["dem_crs"],
                    },
                )
                reusable = self._reusable_stage(
                    kind,
                    digest,
                    inputs=(
                        job["input_file"], job["filled_dem"], job["lookup_table"],
                    ),
                    outputs=(
                        job["output"], job["definition"], job["metadata"],
                    ),
                )
                if reusable is not None:
                    self.dialog.log_message(
                        f'{SPECS[kind]["label"]} inputs are unchanged. '
                        "Reusing the prepared output."
                    )
                    self._lookup_ready(
                        {
                            "kind": kind,
                            "output": job["output"],
                            "definition": job["definition"],
                            "metadata": job["metadata"],
                        },
                        load,
                        None,
                        digest=digest,
                        reused=True,
                    )
                    if done is not None:
                        QtCore.QTimer.singleShot(0, lambda: done({"kind": kind}))
                    return True
                runner = partial(_run_lookup, job=job)
                finish = lambda result: self._lookup_ready(
                    result, load, done, digest=digest)
        except Exception as error:
            self._error(SPECS[kind]["label"], error, failed)
            return False
        return self.coordinator.submit(
            key or f"categorical-{kind}",
            f'Prepare {SPECS[kind]["label"]}',
            runner,
            controls=self._controls(controls),
            resource="morphology-files",
            task_aware=True,
            on_success=finish,
            on_error=lambda message: self._error(SPECS[kind]["label"], message, failed),
        )

    def _lookup_job(self, kind):
        spec = SPECS[kind]
        config = self.dialog.categorical_lookup_config(kind) or {}
        source_config = self.dialog.categorical_source_config(kind) or {}
        layer = self.dialog.input_combo(spec["kind"]).currentLayer()
        source = str(source_config.get("input_path", "") or "")
        if not source:
            source = self._selected_source(spec["kind"])
        is_vector = isinstance(layer, QgsVectorLayer) or Path(
            source.split("|", 1)[0]
        ).suffix.lower() in {".shp", ".gpkg", ".geojson"}
        if not source or any(not config.get(key) for key in ("lookup_table", "mapping_field", "class_field")):
            raise ValueError(f"Configure the {spec['label']} input and lookup table first.")
        local = layers.local_layer_source(layer) if layer is not None else None
        if local is None:
            candidate = Path(str(source).split("|", 1)[0])
            if candidate.is_file():
                local = str(candidate)
        if local is None:
            with TemporaryDirectory(prefix=f"mhm_qgis_{kind}_boundary_") as temporary:
                if layer is None:
                    raise ValueError(f"Input is not a readable local file: {source}")
                materialized = (
                    layers.materialize_vector_layer(
                        layer, str(Path(temporary) / "input.gpkg")
                    )
                    if is_vector
                    else layers.materialize_raster_layer(
                        layer,
                        str(Path(temporary) / "input.tif"),
                        log=self.dialog.log_message,
                    )
                )
                permanent = Path(geometry_folder(self.dialog.project_folder)) / f"_{kind}_input{Path(materialized).suffix}"
                shutil.copy2(materialized, permanent)
                local = str(permanent)
        geometry = Path(geometry_folder(self.dialog.project_folder))
        staging = Path(morph_staging_folder(self.dialog.project_folder))
        staging.mkdir(parents=True, exist_ok=True)
        output = geometry / spec["geometry"]
        definition = staging / spec["definition"] if spec.get("definition") else None
        metadata = geometry / "geology_class_metadata.json" if kind == "geology" else None
        removals = [
            *categorical.ready_paths(self.dialog.project_folder, kind),
            *categorical.intermediate_paths(self.dialog.project_folder, kind)[1:],
        ]
        layer_crs = layer.crs() if layer is not None else None
        input_crs = self._crs_text(layer_crs) or self._crs_text(self.dialog.get_crs())
        dem_crs = self._crs_text(layers.crs_of(self.filled_dem_path)) or self._crs_text(
            self.dialog.get_crs()
        )
        return {
            "kind": kind,
            "geometry": str(geometry),
            "input_file": str(local),
            "filled_dem": self.filled_dem_path,
            "output": str(output),
            "definition": str(definition) if definition else "",
            "metadata": str(metadata) if metadata else "",
            "lookup_table": config["lookup_table"],
            "mapping_field": config["mapping_field"],
            "class_field": config["class_field"],
            "is_vector": bool(is_vector),
            "input_crs": input_crs,
            "dem_crs": dem_crs,
            "removals": tuple(map(str, removals)),
        }

    def _lookup_ready(self, result, load, done, digest=None, reused=False):
        kind = result["kind"]
        output = Path(result["output"])
        definition = Path(result["definition"]) if result["definition"] else None
        self._register(str(output), name=output.name, loaded=False)
        if definition:
            self._register(str(definition), name=definition.name, loaded=False)
        if load:
            self._load(str(output), SPECS[kind]["layer_name"])
        if kind == "soil":
            self.dialog.record_standard_soil_output(None, str(definition) if definition else None)
        categorical.record_namelist(
            self.dialog.project_folder,
            kind,
            output,
            mode=self.dialog.categorical_input_mode(kind),
            lookup=self.dialog.categorical_lookup_config(kind),
            definition=definition,
        )
        outputs = [output]
        if definition:
            outputs.append(definition)
        if result.get("metadata"):
            outputs.append(Path(result["metadata"]))
        self._categorical_complete(kind, outputs, reused=reused)
        if digest and not reused:
            self._record_stage(kind, digest, outputs)
        if not reused:
            self.dialog.log_message(
                f'{SPECS[kind]["label"]} data prepared successfully.')
        if done is not None:
            done(result)

    def _advanced_ready(self, result, done):
        for output in result["outputs"]:
            self._register(output, name=Path(output).name, loaded=False)
        self.dialog.log_message(
            f'Advanced {"land-cover" if result["kind"] == "lc" else "soil"} data prepared.'
        )
        self._categorical_complete(result["kind"], result["outputs"])
        if done is not None:
            done(result)

    def _categorical_complete(self, kind, outputs, reused=False):
        paths = [str(path) for path in outputs if Path(path).is_file()]
        for path in paths:
            self._register(
                path, name=Path(path).name, loaded=False
            )
        processing.mark_workflow(
            self.dialog.project_folder,
            f"{'land_cover' if kind == 'lc' else kind}_processing",
            "completed",
            outputs=[registry.key_for(self.dialog.project_folder, path) for path in paths],
            reused=bool(reused),
        )

    def start_domain_preflight(self, controls, done, failed=None):
        try:
            self._dem_options()
        except Exception as error:
            self._error("Domain Delineator", error, failed)
            return False
        folder = Path(geometry_folder(self.dialog.project_folder))
        paths = (
            folder / "1_dem_filled.tif",
            folder / "2_flow_accumulation.tif",
            folder / "2_channel_network.shp",
        )
        if all(path.is_file() for path in paths):
            QtCore.QTimer.singleShot(0, lambda: done({"prepared": True}))
            return True
        return self.start_hydrology(
            key="domain-delineator-preflight",
            controls=controls,
            load="",
            direction=False,
            done=lambda result: done({"prepared": True, **result}),
            failed=failed,
        )

    def start_execute_all(self):
        if self.execute_all_active:
            return False
        if self.dialog.running_morphology_workflow_key() is not None or self.coordinator.resource_busy(
            "morphology-files"
        ):
            QMessageBox.information(
                self.dialog,
                "Execute All Processing",
                "Another preprocessing task is currently running.",
            )
            return False
        if not self.dialog.check_prerequisites():
            return False
        self.execute_all_active = True
        self.dialog.set_meteo_setup_controls_enabled(False)
        processing.mark_workflow(
            self.dialog.project_folder, "execute_all", "running"
        )
        self.dialog.set_morphology_workflow_button_state("execute_all", "running")

        def start(stage, advance):
            if not self._condition(stage.condition):
                QtCore.QTimer.singleShot(0, advance)
                return True
            starter = getattr(self, f"start_{stage.starter}")
            return starter(
                *stage.args,
                done=lambda _result: advance(),
                failed=self._fail_execute_all,
                **stage.options,
            )

        return morphology.run_stages(
            morphology.EXECUTE_ALL_STAGES,
            start,
            on_done=self._finish_execute_all,
            on_fail=self._fail_execute_all,
        )

    def _finish_execute_all(self):
        self.dialog.set_meteo_setup_controls_enabled(True)
        self.execute_all_active = False
        self.dialog.finish_morphology_workflow("execute_all", True, "")

    def _fail_execute_all(self, message):
        self.dialog.set_meteo_setup_controls_enabled(True)
        if not self.execute_all_active:
            return
        self.execute_all_active = False
        self.dialog.finish_morphology_workflow("execute_all", False, str(message))

    def _condition(self, name):
        if not name:
            return True
        if name == "pour_points":
            return self.dialog.input_combo("pour_points").currentLayer() is not None
        if name == "gauged_outlets":
            return bool(configured_gauged_outlet_ids(self.dialog.project_folder))
        return True

__all__ = ["MorphologyTaskBridge"]
