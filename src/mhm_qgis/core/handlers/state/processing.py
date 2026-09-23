# -*- coding: utf-8 -*-
"""The single persistence boundary for project processing state.

Every feature that records an output, workflow, grid or reuse fingerprint uses
this module.  Keeping the read-modify-write cycle under one lock prevents two
worker callbacks in the same process from silently replacing each other's
updates.
"""
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from threading import RLock
from typing import Any

from ..file import json as jsonio
from ..store.paths import temp_folder
from ...utils.time import utc_timestamp


STATE_FILENAME = "mhm_qgis_processing_state.json"
STATE_VERSION = 1

# ponytail: one process-wide lock is enough while projects are updated by one
# plugin process; use per-path/inter-process locks only if that runtime changes.
_LOCK = RLock()


def state_path(project_folder) -> Path:
    """Return the processing-state path for one project."""
    return Path(temp_folder(project_folder)) / STATE_FILENAME


def load(project_folder) -> dict[str, Any]:
    """Return the latest usable processing state."""
    with _LOCK:
        return jsonio.read_mapping(state_path(project_folder))


def overlay(project_folder, sections: Mapping[str, Any]) -> dict[str, Any]:
    """Atomically replace the named top-level sections and preserve all others."""
    with _LOCK:
        state = jsonio.read_mapping(state_path(project_folder))
        state.update(sections)
        jsonio.write(state_path(project_folder), state)
        return state


def section(project_folder, name: str, default=None):
    """Return one section from the latest state."""
    value = load(project_folder).get(name, default)
    return value


def set_entry(
    project_folder,
    section_name: str,
    key: str,
    value: Mapping[str, Any],
    *,
    merge: bool = False,
) -> dict[str, Any]:
    """Atomically set one entry in a mapping section."""
    with _LOCK:
        path = state_path(project_folder)
        state = jsonio.read_mapping(path)
        entries = state.get(section_name)
        if not isinstance(entries, dict):
            entries = {}
            state[section_name] = entries
        entry = dict(value)
        if merge and isinstance(entries.get(key), dict):
            entry = {**entries[key], **entry}
        entries[key] = entry
        jsonio.write(path, state)
        return entry


def remove_entry(project_folder, section_name: str, key: str) -> bool:
    """Atomically remove one entry, returning whether it existed."""
    with _LOCK:
        path = state_path(project_folder)
        state = jsonio.read_mapping(path)
        entries = state.get(section_name)
        if not isinstance(entries, dict) or key not in entries:
            return False
        del entries[key]
        jsonio.write(path, state)
        return True


def mark_workflow(project_folder, workflow, status, message="", **metadata) -> dict:
    """Atomically record one workflow transition."""
    timestamp = utc_timestamp()
    entry = section(project_folder, "workflows", {}).get(workflow, {})
    entry = dict(entry) if isinstance(entry, dict) else {}
    entry.update({"status": status, "updated_at": timestamp})
    if status == "running":
        entry["started_at"] = timestamp
        entry.pop("completed_at", None)
        entry.pop("failed_at", None)
    elif status == "completed":
        entry["completed_at"] = timestamp
    elif status == "failed":
        entry["failed_at"] = timestamp
    if message:
        entry["message"] = message
    elif status == "running":
        entry.pop("message", None)
    entry.update({key: value for key, value in metadata.items() if value is not None})
    return set_entry(project_folder, "workflows", workflow, entry)


def workflow(project_folder, name) -> dict:
    """Return one saved workflow entry."""
    entries = section(project_folder, "workflows", {})
    return dict(entries.get(name, {})) if isinstance(entries, dict) else {}


def save_grid(project_folder, l0_header, l2_header, multiplier) -> dict:
    """Validate and persist the common L0/L2 grid contract."""
    from ...grid import validate_l0_l2_alignment

    ratio = int(multiplier)
    validate_l0_l2_alignment(l0_header, l2_header, ratio)
    grid = dict(section(project_folder, "grid", {}) or {})
    grid.update({
        "l0_header": dict(l0_header),
        "l2_header": dict(l2_header),
        "l2_ratio_to_l0": ratio,
        "updated_at": utc_timestamp(),
    })
    overlay(project_folder, {"grid": grid})
    return grid


def save_grid_metadata(project_folder, metadata) -> dict:
    """Persist the complete serializable grid result in processing state."""
    data = dict(metadata or {})
    l0_header, l2_header = data.get("l0_header"), data.get("l2_header")
    ratio = data.get("l2_ratio_to_l0")
    if l0_header and l2_header and ratio is not None:
        from ...grid import validate_l0_l2_alignment

        validate_l0_l2_alignment(l0_header, l2_header, int(ratio))
    current = dict(section(project_folder, "grid", {}) or {})
    current.update(data)
    current["updated_at"] = utc_timestamp()
    overlay(project_folder, {"grid": current})
    return current


def grid_metadata(project_folder) -> dict | None:
    """Return saved grid metadata."""
    current = section(project_folder, "grid", {}) or {}
    if isinstance(current, dict) and current.get("l2_header"):
        return dict(current)
    return None


def saved_grid(project_folder, *, log=None):
    """Return a valid persisted grid contract, or ``None``."""
    from ...grid import validate_l0_l2_alignment

    grid = grid_metadata(project_folder) or {}
    l0_header, l2_header = grid.get("l0_header"), grid.get("l2_header")
    if not isinstance(l0_header, dict) or not isinstance(l2_header, dict):
        return None
    try:
        validate_l0_l2_alignment(
            l0_header, l2_header, int(grid.get("l2_ratio_to_l0"))
        )
    except (TypeError, ValueError) as error:
        if log:
            log(f"WARNING: Discarding the saved L0/L2 grid contract: {error}")
        return None
    return dict(grid)


def save_domains(project_folder, plan) -> list[dict]:
    """Persist the domain inputs required by Morphology Setup."""
    domains = [
        {
            key: entry[key]
            for key in (
                "domain_id",
                "outlet_id",
                "name",
                "mask",
                "directory",
                "dem_path",
            )
        }
        for entry in plan
    ]
    overlay(project_folder, {"domains": domains})
    return domains


__all__ = [
    "STATE_FILENAME",
    "STATE_VERSION",
    "load",
    "grid_metadata",
    "overlay",
    "mark_workflow",
    "remove_entry",
    "save_domains",
    "save_grid",
    "save_grid_metadata",
    "saved_grid",
    "section",
    "set_entry",
    "state_path",
    "workflow",
]
