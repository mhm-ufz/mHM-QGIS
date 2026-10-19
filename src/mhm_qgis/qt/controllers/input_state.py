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
from ...core.handlers.store.paths import portable_path, project_path
from ...qgis_bridge import layers as qgis_layers
from . import categorical, forcing, grid, main, model, time

_PATH_KEYS = ("input_path", "lookup_table", "classdefinition_path")


def _state_path(dialog):
    return input_state_path(dialog.project_folder) if dialog.project_folder else None




def _paths(config, convert, root):
    """Return `config` with its known path keys passed through `convert`."""
    if not isinstance(config, dict):
        return {}
    return {
        key: convert(root, value) if key in _PATH_KEYS else value
        for key, value in config.items()
    }


def save(dialog):
    """Save selected inputs to a JSON file in the project folder."""
    if (
        not dialog.project_folder
        or dialog._loading_input_state
        or dialog._suspend_input_state_saves
    ):
        return
    root = dialog.project_folder
    path = _state_path(dialog)
    if dialog._multi_domains is not None:
        # A combined setup holds no inputs of its own, only its domains.
        jsonio.write(path, {
            "version": 4, "setup": "multi",
            "mhm_version": dialog.comboBox_mHMversion.currentText().strip(),
            "domains": list(dialog._multi_domains), "intro": dialog._intro_answers,
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

    meteo_inputs = {
        "l2_multiplier": int(dialog.spinBox_L2ResolutionMultiplier.value()),
        "l2_multiplier_user_selected": bool(dialog._l2_multiplier_user_selected),
        "precipitation": {
            "folder": portable_path(root, grid.precipitation_folder(dialog)),
            "pattern": dialog.lineEdit_precipitationGlobDefinition.text(),
        },
        "variables": dialog._meteo_inputs,
    }
    crs = dialog.get_crs()
    state = {
        "version": 4,
        "mhm_version": dialog.comboBox_mHMversion.currentText().strip(),
        "layers": layers,
        "grid_resolutions": {
            "l1_resolution": grid.current_l1_resolution(dialog),
            "l11_resolution": grid.current_l11_resolution(dialog),
        },
        "lai_input_type": dialog.comboBox_lai_inputType.currentText(),
        "folder_search": dialog.checkBox_enableFolderSearch.isChecked(),
        "categorical_types": {
            kind: categorical.categorical_input_mode(dialog, kind)
            for kind in categorical.KINDS
        },
        "categorical_lookups": {
            kind: _paths(config, portable_path, root)
            for kind, config in dialog._categorical_lookup_configs.items()
        },
        "categorical_ready": {
            kind: _paths(config, portable_path, root)
            for kind, config in dialog._categorical_ready_configs.items()
            if isinstance(config, dict)
        },
        "lai_netcdf": _paths(dialog._lai_input_config, portable_path, root),
        "thread_count": dialog.task_coordinator.max_threads,
        "advanced_inputs": {
            kind: value.as_dict() if hasattr(value, "as_dict") else value
            for kind, value in dialog._advanced_inputs.items()
        },
        "land_cover_ready_source": dialog._land_cover_ready_source,
        "meteo_inputs": meteo_inputs,
        "observation_inputs": dialog._observation_inputs,
        "pour_point_outlet_id_field": main.selected_outlet_id_field(dialog),
        "domain_definition_type": dialog.comboBox_domainDefinitionType.currentText().strip(),
        "crs_authid": crs.authid() if crs and crs.isValid() else "",
        "intro": dialog._intro_answers,
    }
    try:
        jsonio.write(path, state)
    except Exception as error:
        dialog.log_message(f"WARNING: Could not save input state: {error}")


def load(dialog):
    """Load saved input selections from the project folder."""
    path = _state_path(dialog)
    dialog._loading_input_state = True
    try:
        clear(dialog)
        if not path or not os.path.exists(path):
            main.refresh_input_sources(dialog)
            grid.refresh_precipitation_folder(dialog)
            dialog.log_message("No saved input state found for this project.")
            return
        state = jsonio.read(path)
        if not isinstance(state, dict):
            main.refresh_input_sources(dialog)
            dialog.log_message("WARNING: Could not read input state: not a JSON object.")
            return
        _restore_combo_text(dialog.comboBox_mHMversion, state.get("mhm_version", ""))
        # A setup without saved answers keeps the ones given before it was opened.
        dialog._intro_answers = state.get("intro") or dialog._intro_answers
        if state.get("setup") == "multi":
            from . import multi_domain, project

            dialog._multi_domains = list(state.get("domains", []))
            with QSignalBlocker(dialog.comboBox_mhmWorkType):
                dialog.comboBox_mhmWorkType.setCurrentIndex(1)
            project.show_work_type(dialog)
            multi_domain.refresh_table(dialog)
            return
        _restore_resolution_preferences(dialog, state.get("grid_resolutions", {}))
        _restore_combo_text(dialog.comboBox_lai_inputType, state.get("lai_input_type", ""))
        crs = QgsCoordinateReferenceSystem(state.get("crs_authid", "") or "")
        if crs.isValid():
            dialog.mProjectionSelectionWidget_crs.setCrs(crs)
        _restore_categorical_inputs(dialog, state)
        main.refresh_input_sources(dialog)
        grid.refresh_precipitation_folder(dialog)
        _restore_input_layers(dialog, state.get("layers", {}))
        _restore_domain_inputs(dialog, state)
        _restore_meteo_inputs(dialog, state.get("meteo_inputs", {}))
        dialog._observation_inputs = dict(state.get("observation_inputs", {}))
        forcing.populate_observation_table(dialog)
        time.restore(dialog)
        grid.refresh_grid_resolution_controls(dialog)
    finally:
        dialog._loading_input_state = False
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
    for widget in (
        *(categorical.categorical_type_combo(dialog, kind) for kind in categorical.KINDS),
        dialog.comboBox_domainDefinitionType,
        dialog.comboBox_precipitationFile,
    ):
        with QSignalBlocker(widget):
            widget.setCurrentIndex(-1)
    with QSignalBlocker(dialog.comboBox_pourPointOutletID):
        dialog.comboBox_pourPointOutletID.clear()
        dialog.comboBox_pourPointOutletID.addItem("")
    dialog.lineEdit_precipitationGlobDefinition.setText("*.nc")
    dialog._grid_l2_metadata = None
    dialog._grid_l2_header = None
    time.restore(dialog)
    with QSignalBlocker(dialog.spinBox_L2ResolutionMultiplier):
        dialog.spinBox_L2ResolutionMultiplier.setValue(1)
    dialog._l2_multiplier_user_selected = False
    dialog._meteo_inspections = {}
    dialog._meteo_inputs = {}
    dialog._observation_inspections = {}
    dialog._observation_inputs = {}
    dialog._grid_l0_info = None
    forcing.populate_meteo_table(dialog)
    forcing.populate_observation_table(dialog)
    grid.clear_precipitation_resolution_labels(dialog)
    with QSignalBlocker(dialog.checkBox_enableFolderSearch):
        dialog.checkBox_enableFolderSearch.setChecked(False)
    dialog._categorical_lookup_configs = {}
    dialog._categorical_ready_configs = {}
    dialog._lai_input_config = {}
    dialog._categorical_modes = {}
    dialog._advanced_inputs = {}
    dialog._land_cover_ready_source = ""
    dialog._domain_definition_mode = ""
    dialog._preferred_l1_resolution = None
    dialog._preferred_l11_resolution = None
    dialog.task_coordinator.set_max_threads(2)


def _restore_combo_text(combo, text):
    index = combo.findText(str(text)) if text else -1
    if index >= 0:
        combo.setCurrentIndex(index)


def _find_normalized(combo, text):
    """Find `text`, ignoring case and treating `_` like a space."""
    index = combo.findText(text)
    if index >= 0:
        return index
    normalized = text.lower().replace("_", " ")
    return next(
        (candidate for candidate in range(combo.count())
         if combo.itemText(candidate).lower().replace("_", " ") == normalized),
        -1,
    )


def _restore_resolution_preferences(dialog, saved):
    """Remember saved L1/L11 choices for the next combo population."""
    for key, attribute in (
        ("l1_resolution", "_preferred_l1_resolution"),
        ("l11_resolution", "_preferred_l11_resolution"),
    ):
        try:
            value = float(saved[key]) if saved.get(key) is not None else None
        except (TypeError, ValueError):
            value = None
        setattr(dialog, attribute, value)


def _restore_categorical_inputs(dialog, state):
    """Restore folder search, categorical modes, and their source configs."""
    root = dialog.project_folder
    with QSignalBlocker(dialog.checkBox_enableFolderSearch):
        dialog.checkBox_enableFolderSearch.setChecked(bool(state.get("folder_search")))
    for attribute, key in (
        ("_categorical_lookup_configs", "categorical_lookups"),
        ("_categorical_ready_configs", "categorical_ready"),
    ):
        setattr(dialog, attribute, {
            kind: _paths(config, project_path, root)
            for kind, config in state.get(key, {}).items()
            if kind in categorical.KINDS and isinstance(config, dict)
        })
    dialog._lai_input_config = _paths(state.get("lai_netcdf", {}), project_path, root)
    dialog.task_coordinator.set_max_threads(state.get("thread_count", 2))

    for kind, text in state.get("categorical_types", {}).items():
        if kind not in categorical.KINDS:
            continue
        combo = categorical.categorical_type_combo(dialog, kind)
        index = _find_normalized(combo, str(text))
        combo.setCurrentIndex(index)
        dialog._categorical_modes[kind] = str(text) if index >= 0 else ""

    inputs = state.get("advanced_inputs", {})
    dialog._advanced_inputs = dict(inputs) if isinstance(inputs, dict) else {}
    dialog._land_cover_ready_source = project_path(root, state.get("land_cover_ready_source", ""))


def _restore_input_layers(dialog, saved_layers):
    """Restore saved layer selections, loading sources into QGIS if needed."""
    adapters = dict(main.input_layer_widgets(dialog))
    for key, layer_info in saved_layers.items():
        adapter = adapters.get(key)
        if adapter is None or not layer_info:
            continue
        if layer_info.get("origin") == "file":
            source = os.path.abspath(layer_info.get("source", ""))
            if os.path.isfile(source):
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


def _restore_domain_inputs(dialog, state):
    """Restore the selected outlet ID field and domain mode."""
    preferred = str(state.get("pour_point_outlet_id_field", "") or "")
    if not preferred:
        from ...core.handlers.state.domain_state import load_state

        preferred = str(load_state(dialog.project_folder).get("outlet_id_field", ""))
    main.populate_pour_point_outlet_fields(
        dialog, preferred=preferred
    )
    combo = dialog.comboBox_domainDefinitionType
    text = str(state.get("domain_definition_type", "") or "")
    if not text and state.get("dem_domain"):
        text = combo.itemText(0)
    with QSignalBlocker(combo):
        combo.setCurrentIndex(combo.findText(text) if text else -1)
    dialog._domain_definition_mode = text


def _restore_meteo_inputs(dialog, state):
    """Restore configured variables and grid preferences without scanning."""
    if not isinstance(state, dict):
        return
    try:
        multiplier = max(1, int(state.get("l2_multiplier", 1)))
    except (TypeError, ValueError):
        multiplier = 1
    with QSignalBlocker(dialog.spinBox_L2ResolutionMultiplier):
        dialog.spinBox_L2ResolutionMultiplier.setValue(multiplier)
    dialog._l2_multiplier_user_selected = bool(
        state.get("l2_multiplier_user_selected", "l2_multiplier" in state)
    )
    dialog._meteo_inputs = dict(state.get("variables", {}))
    forcing.populate_meteo_table(dialog)

    saved = state.get("precipitation", {})
    if not isinstance(saved, dict):
        return
    folder = project_path(dialog.project_folder, str(saved.get("folder", "") or ""))
    combo = dialog.comboBox_precipitationFile
    if folder and (os.path.isdir(folder) or grid.folder_combo_index(combo, folder) >= 0):
        grid.select_precipitation_folder(dialog, folder)
    else:
        combo.setCurrentIndex(0)
    dialog.lineEdit_precipitationGlobDefinition.setText(saved.get("pattern", "*.nc"))
