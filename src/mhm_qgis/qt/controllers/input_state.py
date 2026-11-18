"""Save and restore the main form's selections in `mhm_qgis_input_state.json`.

Paths inside the domain are stored relative to it, so a cloned domain keeps
working. Restoring never scans meteorology folders.
"""
from __future__ import annotations

import os

from qgis.core import QgsCoordinateReferenceSystem, QgsMapLayer
from qgis.PyQt.QtCore import QSignalBlocker

from ...core.handlers.file import json as jsonio
from ...core.handlers.store.layout import input_state_path
from ...externals.mhm_tools_handler import VARIABLES
from ...qgis_bridge import layers as qgis_layers
from ..dialogs.input_selection import _combo_path, _populate_file_combo
from . import categorical, forcing, grid, main, model, process_cases, time, version


def _state_path(dialog):
    return input_state_path(dialog.setup_folder) if dialog.setup_folder else None


def save(dialog):
    """Save selected inputs to a JSON file in the project folder."""
    if (
        not dialog.setup_folder
        or dialog._loading_input_state
        or dialog._suspend_input_state_saves
    ):
        return
    path = _state_path(dialog)
    if dialog._multi_domains is not None:
        # A combined setup holds no inputs of its own, only its domains.
        jsonio.write(path, {
            "version": 4, "setup": "multi",
            "mhm_version": version.selected(dialog),
            "domains": list(dialog._multi_domains), "intro": dialog._intro_answers,
            "process_cases": process_cases.current(dialog),
        })
        return
    existing_layers = jsonio.read_mapping(path).get("layers", {})
    if not isinstance(existing_layers, dict):
        existing_layers = {}

    layers = {}
    for key, combo_box in main.input_layer_widgets(dialog):
        item_data = combo_box.currentData()
        if isinstance(item_data, dict) and item_data.get("origin") == "file":
            source = item_data.get("path", "")
            layers[key] = {
                "name": os.path.basename(source),
                "source": source,
                "type": "vector" if os.path.splitext(source)[1].lower() == ".shp" else "raster",
                "origin": "file",
                "manual": bool(item_data.get("manual")),
                "variable": str(item_data.get("variable", "")),
                "source_crs": str(item_data.get("source_crs", "")),
            }
            continue
        layer = combo_box.currentLayer()
        if not layer:
            preserve = dialog._preserve_missing_layer_state and key in existing_layers
            layers[key] = existing_layers.get(key) if preserve else None
            continue
        layers[key] = {
            "name": layer.name(),
            "source": layer.source(),
            "type": "raster" if layer.type() == QgsMapLayer.LayerType.RasterLayer else "vector",
            "origin": "qgis",
        }

    state = {
        "version": 5,
        "mhm_version": version.selected(dialog),
        "layers": layers,
        "resolution_files": dialog._resolution_files,  # by level: path, variable, crs (L0: factor)
        "extent_levels": grid.ticked(dialog),
        "grid_resolutions": {level.lower(): grid.entered(dialog, level) for level in ("L1", "L11", "L2")},
        "lai_input_type": categorical.categorical_input_mode(dialog, "lai"),
        "categorical_types": {
            kind: categorical.categorical_input_mode(dialog, kind)
            for kind in categorical.KINDS
        },
        "categorical_lookups": {
            kind: config for kind, config in dialog._categorical_lookup_configs.items()
            if isinstance(config, dict)
        },
        "categorical_ready": {
            kind: config for kind, config in dialog._categorical_ready_configs.items()
            if isinstance(config, dict)
        },
        "lai_netcdf": dialog._lai_input_config if isinstance(dialog._lai_input_config, dict) else {},
        "thread_count": dialog.task_coordinator.max_threads,
        "advanced_inputs": {
            kind: value.as_dict() if hasattr(value, "as_dict") else value
            for kind, value in dialog._advanced_inputs.items()
        },
        "land_cover_ready_source": dialog._land_cover_ready_source,
        "drafts": dialog._drafts,
        "meteo_inputs": {"variables": dialog._meteo_inputs},
        "observation_inputs": dialog._observation_inputs,
        "time_period_source": dialog._time_source,
        "pour_point_outlet_id_field": main.selected_outlet_id_field(dialog),
        "domain_definition_type": dialog._domain_definition_mode,
        "gauge_input": "raster" if dialog.radioButton_gaugesRasterInput.isChecked() else "point",
        "gauge_raster": _combo_path(dialog.comboBox_gaugeRaterInput),
        "crs_authid": dialog._set_crs,  # a CRS not yet Set is not kept
        "upscale_factor": dialog._upscale_factor,  # the DEM x this lies on L0, see grid.set_dem
        "dem_replacements": dialog._dem_replacements,  # derivative row -> raster, see grid.set_derivative
        "intro": dialog._intro_answers,
        "process_cases": process_cases.current(dialog),
    }
    try:
        jsonio.write(path, state)
    except Exception as error:
        dialog.log_message(f"WARNING: Could not save input state: {error}")
    process_cases.inputs(dialog)  # the tree's statuses and ▶ follow every input change


def load(dialog):
    """Load saved input selections from the project folder."""
    path = _state_path(dialog)
    dialog._loading_input_state = True
    try:
        clear(dialog)
        if not path or not os.path.exists(path):
            main.refresh_input_sources(dialog)
            dialog.log_message("No saved input state found for this project.")
            return
        state = jsonio.read(path)
        if not isinstance(state, dict):
            main.refresh_input_sources(dialog)
            dialog.log_message("WARNING: Could not read input state: not a JSON object.")
            return
        crs = QgsCoordinateReferenceSystem(state.get("crs_authid", "") or "")
        if crs.isValid():  # first and painted now: the rest of the load holds the event loop
            dialog.mProjectionSelectionWidget_crs.setCrs(crs)
            dialog.mProjectionSelectionWidget_crs.repaint()
        _restore_combo_text(dialog.comboBox_mHMversion, state.get("mhm_version", ""))
        # A setup without saved answers keeps the ones given before it was opened.
        dialog._intro_answers = state.get("intro") or dialog._intro_answers
        process_cases.restore(dialog, state)
        if state.get("setup") == "multi":
            from . import multi_domain, project

            dialog._multi_domains = list(state.get("domains", []))
            with QSignalBlocker(dialog.comboBox_mhmWorkType):
                dialog.comboBox_mhmWorkType.setCurrentIndex(1)
            project.show_work_type(dialog)
            multi_domain.refresh_table(dialog)
            return
        dialog._set_crs = dialog.get_crs().authid()
        dialog._upscale_factor = max(1, int(state.get("upscale_factor") or 1))
        dialog.groupBox_upscaleTopography.setChecked(dialog._upscale_factor > 1)
        dialog.spinBox_upscaleTopographyFactor.setMaximum(max(dialog.spinBox_upscaleTopographyFactor.maximum(),
                                                              dialog._upscale_factor))
        dialog.spinBox_upscaleTopographyFactor.setValue(dialog._upscale_factor)
        _restore_categorical_inputs(dialog, state)
        main.refresh_input_sources(dialog)
        _restore_input_layers(dialog, state.get("layers", {}))
        _restore_domain_inputs(dialog, state)
        _restore_meteo_inputs(dialog, state.get("meteo_inputs", {}))
        dialog._observation_inputs = {key: value for key, value in state.get("observation_inputs", {}).items()
                                      if key in VARIABLES}  # a kind since removed (Q_bkfl) is dropped
        forcing.populate_observation_table(dialog)
        time.restore(dialog)
        dialog._time_source = state.get("time_period_source") or {}
        time.show_source(dialog)
        grid.restore(dialog, state)
    finally:
        dialog._loading_input_state = False
        process_cases.inputs(dialog)
        model.populate_run_names(dialog, "")
        main.refresh_observation_stations(dialog)
        if dialog._discharge_plot is not None:
            dialog._discharge_plot.close()
    dialog.log_message(f"Input state loaded: {path}")


def clear(dialog):
    """Prevent selections from leaking between project folders."""
    dialog._multi_domains = None
    for _, combo in main.input_layer_widgets(dialog):
        combo.setCurrentIndex(-1)
    for name in (*categorical.PAGES.values(), "domainDefinition"):
        main.check(dialog, name, "")
    main.embed(dialog, "domainDefinition", None)
    with QSignalBlocker(dialog.comboBox_pourPointOutletID):
        dialog.comboBox_pourPointOutletID.clear()
        dialog.comboBox_pourPointOutletID.addItem("")
    time.restore(dialog)
    dialog._meteo_inspections = {}
    dialog._meteo_inputs = {}
    dialog._observation_inspections = {}
    dialog._observation_inputs = {}
    dialog._time_source = {}
    time.show_source(dialog)
    grid.clear(dialog)
    forcing.populate_meteo_table(dialog)
    forcing.populate_observation_table(dialog)
    dialog._categorical_lookup_configs = {}
    dialog._categorical_ready_configs = {}
    dialog._lai_input_config = {}
    dialog._categorical_modes = {}
    dialog._advanced_inputs = {}
    dialog._drafts = {}
    dialog._failed_step = None
    dialog._set_crs = dialog.get_crs().authid()
    dialog._upscale_factor = 1
    dialog.groupBox_upscaleTopography.setChecked(False)
    dialog._land_cover_ready_source = ""
    categorical.fill(dialog)
    dialog._domain_definition_mode = ""
    main.check(dialog, "gauges", "Point Input")
    main.show_gauge_type(dialog)
    _populate_file_combo(dialog.comboBox_gaugeRaterInput, dialog.setup_folder, "gauges", "")
    dialog.task_coordinator.set_max_threads(2)


def _restore_combo_text(combo, text):
    index = combo.findText(str(text)) if text else -1
    if index >= 0:
        combo.setCurrentIndex(index)


def _restore_categorical_inputs(dialog, state):
    """Restore categorical modes and their source configs."""
    for attribute, key in (
        ("_categorical_lookup_configs", "categorical_lookups"),
        ("_categorical_ready_configs", "categorical_ready"),
    ):
        setattr(dialog, attribute, {
            kind: dict(config)
            for kind, config in state.get(key, {}).items()
            if kind in categorical.KINDS and isinstance(config, dict)
        })
    lai = state.get("lai_netcdf")
    dialog._lai_input_config = dict(lai) if isinstance(lai, dict) else {}
    dialog.task_coordinator.set_max_threads(state.get("thread_count", 2))

    for kind, text in {"lai": state.get("lai_input_type", ""), **state.get("categorical_types", {})}.items():
        if kind in categorical.KINDS:
            dialog._categorical_modes[kind] = categorical.saved_mode(kind, text)

    inputs = state.get("advanced_inputs", {})
    dialog._advanced_inputs = dict(inputs) if isinstance(inputs, dict) else {}
    categorical.fill(dialog)
    dialog._land_cover_ready_source = state.get("land_cover_ready_source", "")
    drafts = state.get("drafts")
    dialog._drafts = dict(drafts) if isinstance(drafts, dict) else {}


def _restore_input_layers(dialog, saved_layers):
    """Restore saved layer selections, loading sources into QGIS if needed."""
    adapters = dict(main.input_layer_widgets(dialog))
    for key, layer_info in saved_layers.items():
        adapter = adapters.get(key)
        if adapter is None or not layer_info:
            continue
        if layer_info.get("origin") == "file":
            source = os.path.abspath(layer_info.get("source", ""))
            if os.path.isfile(source.split("|", 1)[0]):  # a GeoPackage source names its layer
                main.add_file_input(
                    dialog, key, source, manual=bool(layer_info.get("manual")),
                    variable=str(layer_info.get("variable", "")),
                    source_crs=str(layer_info.get("source_crs", "")),
                )
            else:
                dialog.log_message(f"WARNING: Saved input file is missing: {source}")
            continue
        layer = _find_or_load_layer(dialog, layer_info)
        if layer is None:
            dialog.log_message(
                f"WARNING: Could not restore saved layer '{key}': "
                f"{layer_info.get('source', '')}"
            )
            continue
        adapter.setLayer(layer)


def _find_or_load_layer(dialog, layer_info):
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
    dialog.log_message(f"Restored input layer: {name} | {source}")
    return layer


_OLD_DOMAIN_TYPES = {  # saved under earlier radio labels → the index of today's in main.DOMAIN_TYPES
    "Single Domain [DEM extent]": 0, "Entire DEM extent in Domain": 0,
    "Select Domains [using Domain Delineator]": 1, "Select Catchments in Domain [using Domain Delineator]": 1}


def _restore_domain_inputs(dialog, state):
    """Restore the selected outlet ID field and domain mode."""
    preferred = str(state.get("pour_point_outlet_id_field", "") or "")
    if not preferred:
        from ...core.handlers.state.domain_state import load_state

        preferred = str(load_state(dialog.setup_folder).get("outlet_id_field", ""))
    main.populate_pour_point_outlet_fields(
        dialog, preferred=preferred
    )
    text = str(state.get("domain_definition_type", "") or "")
    if not text and state.get("dem_domain"):
        text = main.DOMAIN_TYPES[0]
    text = main.DOMAIN_TYPES[_OLD_DOMAIN_TYPES[text]] if text in _OLD_DOMAIN_TYPES else text
    main.check(dialog, "domainDefinition", text)
    dialog._domain_definition_mode = text
    main.check(dialog, "gauges", "Raster Input" if state.get("gauge_input") == "raster" else "Point Input")
    main.show_gauge_type(dialog)
    _populate_file_combo(dialog.comboBox_gaugeRaterInput, dialog.setup_folder, "gauges", state.get("gauge_raster", ""))


def _restore_meteo_inputs(dialog, state):
    """Restore the configured variables without scanning."""
    if isinstance(state, dict):
        dialog._meteo_inputs = dict(state.get("variables", {}))
    forcing.populate_meteo_table(dialog)
