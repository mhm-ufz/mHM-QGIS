"""Execute All morphology: the stages of `EXECUTE_ALL_STAGES`, in order.

`start_execute_all` runs them through `lifecycle.start`. Each `start_*` stage
builds path-only options, submits one core command, and calls `done` or
`failed`. One stage also runs alone: `start_ready_input` publishes an mHM-ready
input as soon as it is chosen. DEM derivatives and the Domain Delineator's
hydrology live in `hydrology`.
"""
from __future__ import annotations

import os
import shutil
from functools import partial
from pathlib import Path
from tempfile import TemporaryDirectory

from qgis.core import QgsVectorLayer
from qgis.PyQt import QtCore

from ...core.executions import morphology
from ...core.executions.morphology import commands
from ...core.handlers.state import processing
from ...core.handlers.store import registry
from ...core.handlers.store.paths import morph_staging_folder, temp_morphology_folder
from ...core.morphology.hydrology.outlets import (
    configured_gauge_points,
    configured_gauged_outlet_ids,
)
from ...core.morphology.layers import categorical, lai
from ...core.morphology.layers.categorical import SPECS
from ...externals.mhm_tools_handler import categorical_output_suffix
from ...qgis_bridge import layers
from ..controllers import grid
from ..controllers.categorical import advanced_input, record_standard_soil_output
from ..controllers.version import selected as selected_version
from . import hydrology, lifecycle


class MorphologyTaskBridge:
    """Run the morphology stages for one main dialog."""

    def __init__(self, dialog):
        self.dialog = dialog
        self.coordinator = dialog.task_coordinator

    @property
    def filled_dem_path(self) -> str:
        return hydrology.filled_dem_path(self.dialog)

    # --- Execute All --------------------------------------------------------

    def start_execute_all(self):
        return lifecycle.start(
            self.dialog, "execute_all_morphology", self._check_execute_all, self._run_execute_all
        )

    def _check_execute_all(self):
        if self.coordinator.resource_busy("morphology-files"):
            raise ValueError("Another preprocessing task is currently running.")
        return grid.require_common_grid(self.dialog)

    def _run_execute_all(self, _grid, finished):
        # One entry per `Stage.command` in `morphology.EXECUTE_ALL_STAGES`.
        starters = {
            "dem_derivatives": partial(hydrology.start_dem_derivatives, self.dialog),
            "gauge_position": self.start_gauge_position,
            "morph_mask": self.start_morph_mask,
            "categorical": self.start_categorical,
            "lai": self.start_lai,
            "publication": self.start_publication,
        }

        def start(stage, advance):
            if stage.condition == "gauged_outlets" and not configured_gauged_outlet_ids(
                    self.dialog.project_folder):
                QtCore.QTimer.singleShot(0, advance)
                return True
            return starters[stage.command](
                *stage.args,
                done=lambda _result: QtCore.QTimer.singleShot(0, advance),
                failed=lambda message: finished(False, message),
                **stage.options,
            )

        return morphology.run_stages(
            morphology.EXECUTE_ALL_STAGES,
            start,
            on_done=lambda: finished(True),
            on_fail=lambda message: finished(False, message),
        )

    def start_ready_input(self, kind, failed):
        """Publish a newly chosen mHM-ready input, preparing the filled DEM first if needed."""
        def publish(_result=None):
            return self.start_categorical(kind, key=f"ready-{kind}", publish=True, failed=failed)

        if os.path.isfile(self.filled_dem_path):
            return publish()
        return hydrology.start_dem_derivatives(
            self.dialog, key=f"ready-{kind}-dem", done=publish, failed=failed,
        )

    # --- Stages -------------------------------------------------------------

    def start_gauge_position(self, *, key="gauge-position", done=None, failed=None):
        """Burn the locations already saved by Domain Delineator."""
        try:
            gauges = configured_gauge_points(self.dialog.project_folder)
        except Exception as error:
            lifecycle.fail(self.dialog, "Gauge Position", error, failed)
            return False
        options = {
            "filled_dem": self.filled_dem_path,
            "gauges": gauges,
            "output_path": hydrology.path(self.dialog, "idgauges.nc"),
        }

        def ready(result):
            lifecycle.register(self.dialog, result, "2_Gauge_Position")
            if done is not None:
                done(str(result))

        return self.coordinator.submit(
            key,
            "Prepare gauge positions",
            partial(commands.gauge_position, options=options),
            resource="morphology-files",
            on_success=ready,
            on_error=lambda message: lifecycle.fail(self.dialog, "Gauge Position", message, failed),
        )

    def _setup_request(self):
        return morphology.setup.SetupRequest(
            project_folder=self.dialog.project_folder,
            headers=grid.grid_level_headers(self.dialog),
            crs=self.dialog.selected_meteo_crs(),
        )

    def _worker_log(self, key):
        return lambda message: self.coordinator.log.emit(key, message)

    def start_morph_mask(self, *, key="execute-all-morph-mask", done=None, failed=None):
        """Create the saved-domain mask before preparing model inputs."""
        request = self._setup_request()
        return self.coordinator.submit(
            key,
            "Prepare morphology mask",
            lambda task: morphology.setup.mask(request, task=task, log=self._worker_log(key)),
            on_success=done,
            on_error=lambda message: lifecycle.fail(self.dialog, "Morphology mask", message, failed),
            resource="morphology-files",
        )

    def start_publication(self, *, done, failed):
        key = "morphology-publication"
        request = self._setup_request()
        return self.coordinator.submit(
            key, "Publish morphology",
            lambda task: morphology.setup.run(request, task=task, log=self._worker_log(key)),
            on_success=done,
            on_error=lambda message: lifecycle.fail(self.dialog, "Publish morphology", message, failed),
            resource="morphology-files")

    def start_lai(self, *, key="execute-all-lai", done=None, failed=None):
        """Resample the selected LAI NetCDF onto the filled DEM grid."""
        mode = self.dialog.categorical_input_mode("lai").lower()
        if mode == "mhm ready" or "lookup" in mode:
            return self.start_categorical(
                "lai", key=key, reuse_existing=True,
                done=done, failed=failed,
            )
        config = self.dialog.lai_netcdf_config() or {}
        source = str(config.get("input_path", "") or "")
        if source and config.get("variable"):
            from ...core.handlers.file.netcdf.raster import source as netcdf_source

            source = netcdf_source(source, config["variable"])
        options = lai.task_options(
            self.dialog.project_folder,
            source,
            self.filled_dem_path,
            crs=layers.crs_string(self.dialog.get_crs()),
            target_timestep=config.get("target_timestep") or lai.DEFAULT_TIMESTEP,
        ) if "netcdf" in mode else None
        if options is None:
            self.dialog.log_message("No LAI NetCDF input is selected. Skipping LAI.")
            if done is not None:
                done(None)
            return True
        os.makedirs(os.path.dirname(options["output_path"]), exist_ok=True)
        digest = commands.stage_digest(
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
            QtCore.QTimer.singleShot(
                0, lambda: self._lai_ready(options["output_path"], [], done)
            )
            return True
        self.dialog.log_message(
            "Resampling LAI to the filled DEM grid with bilinear interpolation."
        )
        messages = []
        return self.coordinator.submit(
            key,
            "Resample LAI to the filled DEM grid",
            partial(commands.lai, options=options, messages=messages),
            resource="morphology-files",
            on_success=lambda result: self._lai_ready(
                result, messages, done, digest=digest),
            on_error=lambda message: lifecycle.fail(self.dialog, "LAI", message, failed),
        )

    def _lai_ready(self, result, messages, done, digest=None):
        for message in messages:
            self.dialog.log_message(message)
        if result:
            lifecycle.register(self.dialog, result)
            if digest:
                commands.record_stage(self.dialog.project_folder, "lai", digest, (result,))
            self.dialog.log_message(
                f"LAI resampled to the filled DEM grid: {result}")
        if done is not None:
            done(result)

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

    def start_categorical(
        self, kind, *, key=None, reuse_existing=False, publish=False, done=None, failed=None,
    ):
        """Prepare one categorical input on the filled-DEM grid.

        The filled DEM must exist: Execute All runs this after its DEM stage, and
        `start_ready_input` prepares the DEM first when it is missing.
        """
        label = SPECS[kind]["label"]
        try:
            mode = categorical.normalized_mode(
                self.dialog.categorical_input_mode(kind)
            )
            if mode == "mhm_ready":
                started = self._ready_task(kind, reuse_existing, publish, done)
            elif self.dialog.uses_advanced_categorical_input(kind):
                started = self._advanced_task(kind, reuse_existing, done)
            elif mode == "lookup_table":
                started = self._lookup_task(kind, done)
            else:
                raise ValueError(f"Select an input type for {label}.")
        except Exception as error:
            lifecycle.fail(self.dialog, label, error, failed)
            return False
        if started is True:  # Reused; the completion is already scheduled.
            return True
        runner, finish = started
        return self.coordinator.submit(
            key or f"categorical-{kind}",
            f"Prepare {label}",
            runner,
            resource="morphology-files",
            on_success=finish,
            on_error=lambda message: lifecycle.fail(self.dialog, label, message, failed),
        )

    def _ready_task(self, kind, reuse_existing, publish, done):
        config = self.dialog.categorical_source_config(kind) or {}
        source = config.get("input_path")
        if not source:
            raise ValueError(f"Select an mHM-ready {SPECS[kind]['label']} input.")
        job = {
            "project_folder": self.dialog.project_folder,
            "kind": kind,
            "input_path": source,
            "classdefinition_path": config.get("classdefinition_path", ""),
            "variable": config.get("variable", ""),
            "source_crs": config.get("source_crs", ""),
            "target_crs": layers.crs_string(self.dialog.get_crs()),
            "target_header": grid.grid_level_headers(self.dialog)["L0"],
            "publish": bool(publish),
        }
        inputs = tuple(
            path for path in (
                source,
                config.get("classdefinition_path", ""),
                self.filled_dem_path,
            ) if path
        )
        digest = commands.stage_digest(inputs, {
            "kind": kind,
            "variable": job["variable"],
            "source_crs": job["source_crs"],
            "target_crs": job["target_crs"],
            "target_header": job["target_header"],
            "format": "common-l0-nc-1",
        })
        reusable = self._reusable_stage(
            kind,
            digest,
            inputs=inputs,
            outputs=categorical.saved_outputs(self.dialog.project_folder, kind),
        ) if reuse_existing else None
        if reusable:
            result = {"kind": kind, "outputs": reusable["outputs"]}
            QtCore.QTimer.singleShot(
                0, lambda: self._ready_input_ready(result, done, reused=True)
            )
            return True
        return (
            partial(commands.ready, job=job),
            lambda result: self._ready_input_ready(result, done, digest=digest),
        )

    def _advanced_task(self, kind, reuse_existing, done):
        value = advanced_input(self.dialog, kind)
        job = {
            "kind": kind,
            "project_folder": str(self.dialog.project_folder),
            "version": selected_version(self.dialog),
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
            result = {"kind": kind, "outputs": reusable["outputs"]}
            QtCore.QTimer.singleShot(0, lambda: self._advanced_ready(result, done))
            return True
        return (
            partial(commands.advanced, job=job),
            lambda result: self._advanced_ready(result, done, digest),
        )

    def _lookup_task(self, kind, done):
        job = self._lookup_job(kind)
        digest = commands.stage_digest(
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
            inputs=(job["input_file"], job["filled_dem"], job["lookup_table"]),
            outputs=(job["output"], job["definition"], job["metadata"]),
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
                None,
                digest=digest,
                reused=True,
            )
            if done is not None:
                QtCore.QTimer.singleShot(0, lambda: done({"kind": kind}))
            return True
        return (
            partial(commands.lookup, job=job),
            lambda result: self._lookup_ready(result, done, digest=digest),
        )

    def _ready_input_ready(self, result, done, *, digest=None, reused=False):
        outputs = [Path(path) for path in result.get("outputs", ())]
        published = [Path(path) for path in result.get("published", ())]
        for output in (*outputs, *published):
            lifecycle.register(self.dialog, output)
        self._categorical_complete(result["kind"], published or outputs)
        if digest and not reused:
            commands.record_stage(self.dialog.project_folder, result["kind"], digest, outputs)
        if done is not None:
            done(result)

    def _lookup_job(self, kind):
        spec = SPECS[kind]
        config = self.dialog.categorical_lookup_config(kind) or {}
        source = str(config.get("input_path", "") or "")
        if not source or any(not config.get(key) for key in ("lookup_table", "mapping_field", "class_field")):
            raise ValueError(f"Configure the {spec['label']} input and lookup table first.")
        layer = layers.find_by_source(source)
        is_vector = isinstance(layer, QgsVectorLayer) or Path(
            source.split("|", 1)[0]
        ).suffix.lower() in {".shp", ".gpkg", ".geojson"}
        local = layers.local_layer_source(layer) if layer is not None else None
        if local is None:
            candidate = Path(source.split("|", 1)[0])
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
                permanent = Path(temp_morphology_folder(self.dialog.project_folder)) / f"_{kind}_input{Path(materialized).suffix}"
                shutil.copy2(materialized, permanent)
                local = str(permanent)
        geometry = Path(temp_morphology_folder(self.dialog.project_folder))
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
        project_crs = layers.crs_string(self.dialog.get_crs())
        input_crs = (
            str(config.get("source_crs", ""))
            or (layers.crs_string(layer.crs()) if layer is not None else "")
            or project_crs
        )
        dem_crs = layers.crs_string(layers.crs_of(self.filled_dem_path)) or project_crs
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

    def _lookup_ready(self, result, done, digest=None, reused=False):
        kind = result["kind"]
        output = Path(result["output"])
        definition = Path(result["definition"]) if result["definition"] else None
        lifecycle.register(self.dialog, output)
        if definition:
            lifecycle.register(self.dialog, definition)
        if kind == "soil":
            record_standard_soil_output(self.dialog, None, str(definition) if definition else None)
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
            commands.record_stage(self.dialog.project_folder, kind, digest, outputs)
        if not reused:
            self.dialog.log_message(
                f'{SPECS[kind]["label"]} data prepared successfully.')
        if done is not None:
            done(result)

    def _advanced_ready(self, result, done, digest=None):
        if digest:
            commands.record_stage(self.dialog.project_folder, result["kind"], digest, result["outputs"])
        for output in result["outputs"]:
            lifecycle.register(self.dialog, output)
        self.dialog.log_message(
            f'Advanced {"land-cover" if result["kind"] == "lc" else "soil"} data prepared.'
        )
        self._categorical_complete(result["kind"], result["outputs"])
        if done is not None:
            done(result)

    def _categorical_complete(self, kind, outputs, reused=False):
        paths = [str(path) for path in outputs if Path(path).is_file()]
        for path in paths:
            lifecycle.register(self.dialog, path)
        processing.mark_workflow(
            self.dialog.project_folder,
            f"{'land_cover' if kind == 'lc' else kind}_processing",
            "completed",
            outputs=[registry.key_for(self.dialog.project_folder, path) for path in paths],
            reused=bool(reused),
        )


__all__ = ["MorphologyTaskBridge"]
