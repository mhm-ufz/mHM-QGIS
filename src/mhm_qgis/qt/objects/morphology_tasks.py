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

from ...externals.mhm_tools_handler import categorical_output_suffix
from ...core.executions import morphology
from ...core.executions.morphology import commands
from ...core.handlers.file.netcdf.raster import file_path
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
_run_dem_derivatives = commands.dem_derivatives
_run_hydrology = commands.hydrology
_run_lai = commands.lai
_run_lookup = commands.lookup
_run_advanced = commands.advanced
_run_ready = commands.ready


class MorphologyTaskBridge(QtCore.QObject):
    """Schedule heavy work without passing Qt or QGIS objects to workers."""

    message = QtCore.pyqtSignal(str)

    def __init__(self, dialog):
        super().__init__(dialog)
        self.dialog = dialog
        self.message.connect(dialog.log_message)
        self.coordinator = dialog.task_coordinator
        self.execute_all_active = False

    def path(self, name: str) -> str:
        """Return a fixed morphology workspace path."""
        return str(Path(geometry_folder(self.dialog.project_folder)) / name)

    @property
    def filled_dem_path(self) -> str:
        return self.path("filled_dem.nc")

    @property
    def snapped_points_path(self) -> str:
        return self.path("2_pour_points_snapped.shp")

    @property
    def channel_network_path(self) -> str:
        return self.path("2_channel_network.shp")

    @property
    def gauge_position_path(self) -> str:
        return self.path("idgauges.nc")

    def _register(self, path, *, name=None, loaded=False, algorithm=None):
        path = file_path(path)
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
        source_uri = getattr(combo, "source_uri", None)
        if callable(source_uri):
            value = source_uri()
            if value:
                return str(value)
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
        combo = self.dialog.input_combo("dem")
        layer = combo.currentLayer()
        source = self._selected_source("dem")
        if not source:
            raise ValueError("The selected DEM has no readable source.")
        data = combo.currentData()
        configured_crs = data.get("source_crs", "") if isinstance(data, dict) else ""
        source_crs = layer.crs() if layer is not None else None
        target_crs = self.dialog.get_crs()
        folder = Path(geometry_folder(self.dialog.project_folder))
        return {
            "source": source,
            "output_folder": str(folder),
            "source_crs": configured_crs or (
                source_crs.authid() or source_crs.toWkt()
                if source_crs is not None and source_crs.isValid()
                else ""
            ),
            "target_crs": (
                target_crs.authid() or target_crs.toWkt()
                if target_crs is not None and target_crs.isValid()
                else ""
            ),
            "reprojected_path": str(folder / "dem_reprojected.nc"),
            "header": (self.dialog._grid_l2_metadata or {}).get("l0_header"),
        }

    def prepare_filled_dem(self) -> str:
        """Synchronously ensure the fixed filled-DEM prerequisite exists.

        This is used only by GUI calculations that need the grid immediately;
        processing buttons and Execute All use ``start_dem_derivatives`` instead.
        """
        options = self._dem_options()
        result = _run_dem_derivatives(None, options)
        path = str(result["filled_dem"])
        self._register(path, name="1_DEM_Filled", loaded=False)
        if not result.get("reused"):
            self.dialog.log_message(
                "DEM derivatives prepared successfully."
            )
        return path

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
            "accumulation": write_flow and accumulation,
            "area_path": str(folder / "facc_area.nc") if write_flow else "",
            "direction": direction,
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
        if not all(os.path.isfile(self.path(name)) for name in ("filled_dem.nc", "dem_derivatives.nc")):
            return self.start_dem_derivatives(
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
        self._register(result["filled_dem"], name="1_DEM_Filled")
        self._register(result["dem_derivatives"], name="DEM derivatives")
        if load:
            key = "filled_dem" if load is True else load
            self._load(result[key], key.replace("_", " ").title())
        if not result.get("reused"):
            self.dialog.log_message("DEM derivatives prepared successfully.")
        self.dialog.update_l0_resolution_from_dem()
        if done is not None:
            done(result)

    def start_lai(
        self,
        *,
        key="execute-all-lai",
        controls=(),
        publish=False,
        done=None,
        failed=None,
    ):
        """Resample the selected LAI NetCDF onto the filled DEM grid."""
        if not os.path.isfile(self.filled_dem_path):
            return self.start_dem_derivatives(
                key=f"{key}-dem",
                controls=controls,
                done=lambda _result: self.start_lai(
                    key=key,
                    controls=controls,
                    publish=publish,
                    done=done,
                    failed=failed,
                ),
                failed=failed,
            )
        mode = self.dialog.categorical_input_mode("lai").lower()
        if mode == "mhm ready" or "lookup" in mode:
            return self.start_categorical(
                "lai", key=key, controls=controls, load=False,
                reuse_existing=True, publish=publish,
                done=done, failed=failed,
            )
        if "netcdf" not in mode:
            self.dialog.log_message(
                "No LAI NetCDF input is selected. Skipping LAI.")
            if done is not None:
                done(None)
            return True
        config = self.dialog.lai_netcdf_config() or {}
        source_path = str(config.get("input_path", "") or self._selected_source("lai"))
        source = source_path
        if source_path and config.get("variable"):
            from ...core.handlers.file.netcdf.raster import source as netcdf_source

            source = netcdf_source(source_path, config["variable"])
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
            QtCore.QTimer.singleShot(
                0,
                lambda: self._lai_ready(
                    options["output_path"], [], done,
                    publish=publish, config=config, source=source,
                ),
            )
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
                result, messages, done, digest=digest, publish=publish,
                config=config, source=source),
            on_error=lambda message: self._error("LAI", message, failed),
        )

    def _lai_ready(
        self, result, messages, done, digest=None, *, publish=False,
        config=None, source="",
    ):
        for message in messages:
            self.dialog.log_message(message)
        if result:
            self._register(
                result, name=Path(result).name, loaded=False)
            if digest:
                self._record_stage("lai", digest, (result,))
            self.dialog.log_message(
                f"LAI resampled to the filled DEM grid: {result}")
            if publish:
                output = lai.publish_to_l0(
                    self.dialog.project_folder,
                    self.dialog.grid_level_headers()["L0"],
                    self._crs_text(self.dialog.get_crs()),
                    source=source,
                    target_timestep=(config or {}).get("target_timestep")
                    or lai.DEFAULT_TIMESTEP,
                    classdefinition_source=(config or {}).get(
                        "classdefinition_path", ""
                    ),
                    log=self.dialog.log_message,
                )
                self._register(output, loaded=False)
                result = str(output)
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
        publish=False,
        done=None,
        failed=None,
    ):
        if not os.path.isfile(self.filled_dem_path):
            return self.start_dem_derivatives(
                key=f"{key or kind}-dem",
                controls=controls,
                load=False,
                done=lambda _result: self.start_categorical(
                    kind,
                    key=key,
                    controls=controls,
                    load=load,
                    reuse_existing=reuse_existing,
                    publish=publish,
                    done=done,
                    failed=failed,
                ),
                failed=failed,
            )
        try:
            mode = categorical.normalized_mode(
                self.dialog.categorical_input_mode(kind)
            )
            if mode == "mhm_ready":
                config = self.dialog.categorical_source_config(kind) or {}
                source = config.get("input_path") or self._selected_source(
                    SPECS[kind]["kind"]
                )
                if not source:
                    raise ValueError(f"Select an mHM-ready {SPECS[kind]['label']} input.")
                job = {
                    "project_folder": self.dialog.project_folder,
                    "kind": kind,
                    "input_path": source,
                    "classdefinition_path": config.get("classdefinition_path", ""),
                    "variable": config.get("variable", ""),
                    "source_crs": config.get("source_crs", ""),
                    "target_crs": self._crs_text(self.dialog.get_crs()),
                    "target_header": self.dialog.grid_level_headers()["L0"],
                    "publish": bool(publish),
                }
                runner = partial(_run_ready, job=job)
                finish = lambda result: self._ready_input_ready(
                    result, load, done
                )
            elif self.dialog.uses_advanced_categorical_input(kind):
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
                if kind == "lc":
                    from ...core.handlers.lookup import validate_land_use_input
                    from ...core.handlers.state.nml_settings import load_settings
                    times = load_settings(self.dialog.project_folder).get("time", {})
                    validate_land_use_input(value, simulation_start=times.get("sim_start"),
                                            simulation_end=times.get("sim_end"), version=job["version"])
                from ...core.morphology.layers.advanced_inputs import input_fingerprint
                digest = input_fingerprint(value, self.filled_dem_path, job["version"])
                reusable = self._reusable_stage(kind, digest) if reuse_existing else None
                if reusable:
                    result = dict(kind=kind, outputs=reusable["outputs"])
                    QtCore.QTimer.singleShot(0, lambda: self._advanced_ready(result, done))
                    return True
                runner = partial(_run_advanced, job=job)
                finish = lambda result: self._advanced_ready(result, done, digest)
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
                        "input_variable": job["input_variable"],
                        "input_crs": job["input_crs"],
                        "dem_crs": job["dem_crs"],
                        "format": "common-l0-nc-1",
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

    def _ready_input_ready(self, result, load, done):
        outputs = [Path(path) for path in result.get("outputs", ())]
        published = [Path(path) for path in result.get("published", ())]
        for output in (*outputs, *published):
            self._register(output, loaded=False)
        if load and (published or outputs):
            self._load(str((published or outputs)[0]), SPECS[result["kind"]]["layer_name"])
        self._categorical_complete(result["kind"], published or outputs)
        if done is not None:
            done(result)

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
        output = staging / f"{spec['ready_name']}{categorical_output_suffix(kind)}"
        definition = staging / spec["definition"] if spec.get("definition") else None
        metadata = geometry / "geology_class_metadata.json" if kind == "geology" else None
        removals = [
            *categorical.ready_paths(self.dialog.project_folder, kind),
            *categorical.intermediate_paths(self.dialog.project_folder, kind)[1:],
            # An older run staged this raster under a different extension;
            # publication would otherwise place both.
            *(staging / f"{spec['ready_name']}{suffix}"
              for suffix in (".asc", ".nc", ".tif", ".tiff")
              if suffix != output.suffix),
        ]
        layer_crs = layer.crs() if layer is not None else None
        input_crs = (
            str(config.get("source_crs", ""))
            or self._crs_text(layer_crs)
            or self._crs_text(self.dialog.get_crs())
        )
        dem_crs = self._crs_text(layers.crs_of(self.filled_dem_path)) or self._crs_text(
            self.dialog.get_crs()
        )
        return {
            "kind": kind,
            "geometry": str(geometry),
            "input_file": str(local),
            "input_variable": str(config.get("variable", "")),
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

    def _advanced_ready(self, result, done, digest=None):
        if digest:
            self._record_stage(result["kind"], digest, result["outputs"])
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
            folder / "filled_dem.nc",
            folder / "dem_derivatives.nc",
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

    def start_publication(self, *, done, failed):
        mode = self.dialog.categorical_input_mode("lai").lower()
        config = (
            self.dialog.lai_netcdf_config() if "netcdf" in mode else {}
        ) or {}
        source = str(config.get("input_path") or "")
        if source and config.get("variable"):
            from ...core.handlers.file.netcdf.raster import source as netcdf_source

            source = netcdf_source(source, config["variable"])
        request = morphology.setup.SetupRequest(
            project_folder=self.dialog.project_folder,
            headers=self.dialog.grid_level_headers(),
            crs=self.dialog.selected_meteo_crs(),
            lai_source=source,
            lai_timestep=config.get("target_timestep") or lai.DEFAULT_TIMESTEP,
        )
        return self.coordinator.submit(
            "morphology-publication", "Publish morphology",
            lambda task: morphology.setup.run(request, task=task, log=self.message.emit),
            task_aware=True,
            on_success=done, on_error=failed, resource="morphology-files")

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
        try:
            from ..controllers.main import invalidate_workflows, require_common_grid
            require_common_grid(self.dialog)
        except ValueError as error:
            QMessageBox.warning(self.dialog, "Configuration", str(error))
            return False
        self.execute_all_active = True
        self.dialog.set_meteo_setup_controls_enabled(False)
        invalidate_workflows(self.dialog, "run_mhm")
        processing.mark_workflow(
            self.dialog.project_folder, "execute_all", "running"
        )
        self.dialog.set_morphology_workflow_button_state("execute_all", "running")

        def start(stage, advance):
            if not self._condition(stage.condition):
                QtCore.QTimer.singleShot(0, advance)
                return True
            starter = getattr(self, f"start_{stage.command}")
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
        try:
            self.dialog.finish_morphology_workflow("execute_all", True, "")
        except Exception as error:
            self._fail_execute_all(error)
            return
        self.execute_all_active = False
        self.dialog.set_meteo_setup_controls_enabled(True)

    def _fail_execute_all(self, message):
        if not self.execute_all_active:
            return
        self.execute_all_active = False
        try:
            self.dialog.finish_morphology_workflow(
                "execute_all", False, str(message)
            )
        finally:
            self.dialog.set_meteo_setup_controls_enabled(True)

    def _condition(self, name):
        if not name:
            return True
        if name == "pour_points":
            return self.dialog.input_combo("pour_points").currentLayer() is not None
        if name == "gauged_outlets":
            return bool(configured_gauged_outlet_ids(self.dialog.project_folder))
        return True

__all__ = ["MorphologyTaskBridge"]
