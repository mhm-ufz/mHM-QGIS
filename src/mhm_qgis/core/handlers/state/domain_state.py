# -*- coding: utf-8 -*-
"""QGIS-free persistence helpers for watershed domain delineation."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Mapping, Union
from ....core.handlers.store.paths import workspace_folder


STATE_FILENAME = "mhm_qgis_domain_delineation_state.json"
STATE_VERSION = 2
DEM_DOMAIN_ID = "__dem__"
DOMAIN_MODE_DEM_EXTENT = "dem_extent"
DOMAIN_MODE_DELINEATOR = "domain_delineator"
DOMAIN_MODES = {
    DOMAIN_MODE_DEM_EXTENT,
    DOMAIN_MODE_DELINEATOR,
}
PathInput = Union[str, Path]


def default_state() -> Dict[str, Any]:
    """Return a new empty domain-delineation state."""
    return {
        "version": STATE_VERSION,
        "definition_mode": "",
        "pour_points_source": "",
        "outlet_id_field": "",
        "outlet_order": [],
        "dem_domain": False,
        "dem_domain_id": None,
        "dem_domain_directory": "",
        "dem_domain_path": "",
        "dem_mask_path": "",
        "merged_mask_path": "",
        "outlets": {},
    }


def state_path(project_folder: PathInput) -> Path:
    """Return the state path inside the plugin-owned workspace."""
    return Path(workspace_folder(project_folder)) / STATE_FILENAME


def _normalized_state(value: object) -> Dict[str, Any]:
    state = default_state()
    if not isinstance(value, Mapping):
        return state

    for key in (
        "pour_points_source",
        "outlet_id_field",
        "dem_domain_directory",
        "dem_domain_path",
        "dem_mask_path",
        "merged_mask_path",
    ):
        item = value.get(key)
        if isinstance(item, str):
            state[key] = item

    definition_mode = value.get("definition_mode", value.get("definition_type"))
    if definition_mode in DOMAIN_MODES:
        state["definition_mode"] = definition_mode

    if isinstance(value.get("dem_domain"), bool):
        state["dem_domain"] = value["dem_domain"]

    outlets = value.get("outlets")
    if isinstance(outlets, Mapping):
        state["outlets"] = {
            str(outlet_id): dict(record)
            for outlet_id, record in outlets.items()
            if isinstance(record, Mapping)
        }
    requested_order = value.get("outlet_order", [])
    if isinstance(requested_order, (list, tuple)):
        for outlet_id in requested_order:
            text = str(outlet_id)
            if text in state["outlets"] and text not in state["outlet_order"]:
                state["outlet_order"].append(text)
    for outlet_id in state["outlets"]:
        if outlet_id not in state["outlet_order"]:
            state["outlet_order"].append(outlet_id)
    assign_domain_ids(state)
    return state


def ordered_outlet_ids(state: Mapping[str, Any]) -> List[str]:
    """Return outlet IDs in their explicit stable feature order."""
    outlets = state.get("outlets", {})
    if not isinstance(outlets, Mapping):
        return []
    ordered: List[str] = []
    requested = state.get("outlet_order", [])
    if isinstance(requested, (list, tuple)):
        for value in requested:
            outlet_id = str(value)
            if outlet_id in outlets and outlet_id not in ordered:
                ordered.append(outlet_id)
    for value in outlets:
        outlet_id = str(value)
        if outlet_id not in ordered:
            ordered.append(outlet_id)
    return ordered


def assign_domain_ids(state: Dict[str, Any]) -> Dict[str, Any]:
    """Assign deterministic contiguous IDs to the active domains in-place."""
    outlets = state.get("outlets", {})
    if not isinstance(outlets, dict):
        state["outlets"] = {}
        outlets = state["outlets"]
    state["outlet_order"] = ordered_outlet_ids(state)
    selected = False
    for outlet_id in state["outlet_order"]:
        record = outlets.get(outlet_id)
        if not isinstance(record, dict):
            continue
        if _enabled(record, "is_domain", "domain"):
            record["domain_id"] = 1
            selected = True
        else:
            record.pop("domain_id", None)
    state["dem_domain"] = not selected
    state["dem_domain_id"] = 1 if not selected else None
    return state


def load_state(project_folder: PathInput) -> Dict[str, Any]:
    """Load state, returning defaults for missing or malformed content."""
    path = state_path(project_folder)
    try:
        with path.open("r", encoding="utf-8") as state_file:
            return _normalized_state(json.load(state_file))
    except (OSError, ValueError, TypeError):
        return default_state()


def _absolute_path(base: Path, value: PathInput) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = base / path
    return path.resolve()


def serialize_input_path(project_folder: PathInput, value: PathInput) -> str:
    """Serialize an input path relative to the outer project when possible."""
    if not str(value):
        return ""
    project = Path(project_folder).resolve()
    path = _absolute_path(project, value)
    try:
        return path.relative_to(project).as_posix()
    except ValueError:
        return str(path)


def resolve_input_path(project_folder: PathInput, value: PathInput) -> Path:
    """Resolve a stored input path relative to the outer project."""
    return _absolute_path(Path(project_folder).resolve(), value)


def serialize_output_path(project_folder: PathInput, value: PathInput) -> str:
    """Serialize a generated output relative to the plugin workspace."""
    if not str(value):
        return ""
    workspace = Path(workspace_folder(project_folder)).resolve()
    path = _absolute_path(workspace, value)
    try:
        return path.relative_to(workspace).as_posix()
    except ValueError as error:
        message = "Domain outputs must be inside the plugin workspace."
        raise ValueError(message) from error


def resolve_output_path(project_folder: PathInput, value: PathInput) -> Path:
    """Resolve a stored output path, rejecting paths outside the workspace."""
    workspace = Path(workspace_folder(project_folder)).resolve()
    path = _absolute_path(workspace, value)
    try:
        path.relative_to(workspace)
    except ValueError as error:
        message = "Domain outputs must be inside the plugin workspace."
        raise ValueError(message) from error
    return path


def _serialized_state(
    project_folder: PathInput,
    value: Mapping[str, Any],
) -> Dict[str, Any]:
    state = _normalized_state(value)
    for record in state["outlets"].values():
        if isinstance(record.get("delineation"), dict) and record["delineation"].get("raster_path"):
            record["delineation"] = dict(record["delineation"])
            record["delineation"]["raster_path"] = serialize_output_path(
                project_folder, record["delineation"]["raster_path"]
            )
        if record.get("discharge_file"):
            record["discharge_file"] = serialize_input_path(
                project_folder, record["discharge_file"]
            )
        for key in ("mask_path", "vector_path", "domain_directory", "dem_path"):
            if record.get(key):
                record[key] = serialize_output_path(project_folder, record[key])
        if record.get("gauge_path"):
            record["gauge_path"] = serialize_output_path(
                project_folder, record["gauge_path"]
            )
    for key in ("dem_domain_directory", "dem_domain_path", "dem_mask_path", "merged_mask_path"):
        if state.get(key):
            state[key] = serialize_output_path(project_folder, state[key])
    return state


def delineation_fingerprint(filled_dem, x, y):
    """Cheap DEM and effective-point identity; domain numbering is irrelevant."""
    from .cache import fingerprint

    return fingerprint([filled_dem], {"x": float(x), "y": float(y), "delineation": 1})


def cached_delineation(project_folder, filled_dem, x, y, entry):
    """Resolve a reusable mask without reading the raster on the UI thread."""
    if not isinstance(entry, dict) or not Path(filled_dem).is_file():
        return None
    if entry.get("fingerprint") != delineation_fingerprint(filled_dem, x, y):
        return None
    if not all(key in entry for key in ("raster_path", "mask_value", "catchment_area_m2", "cell_center")):
        return None
    try:
        path = resolve_output_path(project_folder, entry["raster_path"])
    except ValueError:
        return None
    return dict(entry, raster_path=str(path)) if path.is_file() else None


def save_preview(project_folder, source, id_field, outlet_id, result):
    """Persist only cache metadata, never the dialog's uncommitted assignments."""
    state = load_state(project_folder)
    if not state["pour_points_source"]:
        state["pour_points_source"] = source
        state["outlet_id_field"] = id_field
    record = state["outlets"].setdefault(str(outlet_id), {})
    record["delineation"] = dict(result, pour_points_source=source, outlet_id_field=id_field)
    if state["pour_points_source"] == source and state["outlet_id_field"] == id_field:
        for key in ("confidence", "snap_count", "snap_distance_m"):
            record[key] = result.get(key)
    save_state(project_folder, state)


def save_state(
    project_folder: PathInput,
    value: Mapping[str, Any],
) -> Path:
    """Atomically save normalized state and return its path."""
    path = state_path(project_folder)
    path.parent.mkdir(parents=True, exist_ok=True)
    state = _serialized_state(project_folder, value)
    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(file_descriptor, "w", encoding="utf-8") as state_file:
            json.dump(state, state_file, indent=2, sort_keys=True)
            state_file.write("\n")
            state_file.flush()
            os.fsync(state_file.fileno())
        os.replace(temporary_name, path)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)
    return path


def _enabled(record: Mapping[str, Any], key: str, legacy_key: str) -> bool:
    return bool(record.get(key, record.get(legacy_key, False)))


def gauged_outlet_ids(state: Mapping[str, Any]) -> List[str]:
    """Return IDs of outlets marked as gauged, in stored order."""
    outlets = state.get("outlets", {})
    if not isinstance(outlets, Mapping):
        return []
    return [
        outlet_id
        for outlet_id in ordered_outlet_ids(state)
        for record in (outlets.get(outlet_id),)
        if isinstance(record, Mapping)
        and _enabled(record, "is_gauged", "gauged")
    ]


def active_domain_records(state: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """Return active outlet domains plus the optional synthetic DEM domain."""
    state = _normalized_state(state)
    records: List[Dict[str, Any]] = []
    outlets = state.get("outlets", {})
    if isinstance(outlets, Mapping):
        for outlet_id in ordered_outlet_ids(state):
            record = outlets.get(outlet_id)
            if not isinstance(record, Mapping):
                continue
            if _enabled(record, "is_domain", "domain"):
                active = dict(record)
                active["outlet_id"] = str(outlet_id)
                active["is_dem_domain"] = False
                records.append(active)

    if state.get("dem_domain") is True:
        records.append(
            {
                "outlet_id": DEM_DOMAIN_ID,
                "domain_id": state["dem_domain_id"],
                "is_domain": True,
                "is_dem_domain": True,
                "gauged_outlet_ids": gauged_outlet_ids(state),
                "domain_directory": state.get("dem_domain_directory", ""),
                "dem_path": state.get("dem_domain_path", ""),
            }
        )
    return records


def domain_count(state: Mapping[str, Any]) -> int:
    """Catchment selections always describe one model domain."""
    return 1


def gauge_records(state: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """Return normalized gauge metadata suitable for namelist projection."""
    state = _normalized_state(state)
    outlets = state["outlets"]
    records: List[Dict[str, Any]] = []
    for outlet_id in gauged_outlet_ids(state):
        record = outlets[outlet_id]
        try:
            gauge_id = int(record.get("gauge_id"))
        except (TypeError, ValueError):
            try:
                numeric = float(outlet_id)
                gauge_id = int(numeric) if numeric.is_integer() else None
            except (TypeError, ValueError):
                gauge_id = None
        domain_ids = []
        for value in record.get("domain_ids", []):
            try:
                domain_id = int(value)
            except (TypeError, ValueError):
                continue
            if domain_id > 0 and domain_id not in domain_ids:
                domain_ids.append(domain_id)
        records.append(
            {
                "outlet_id": outlet_id,
                "gauge_id": gauge_id,
                "gauge_filename": str(
                    record.get("gauge_filename") or f"{outlet_id}.txt"
                ),
                "gauge_path": str(record.get("gauge_path", "") or ""),
                "domain_ids": [1] if domain_ids else [],
            }
        )
    return records
