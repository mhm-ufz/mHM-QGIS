"""The morphology half of `mhm_qgis_processing_state.json`.

This compatibility module keeps the former session-facing API while all
persistence is performed by :mod:`processing`. New code should use explicit
project-folder APIs rather than carrying a mutable state copy on a session.

Functions take the session, so nothing here needs a dialog or a processor.
"""
from __future__ import annotations

import os

from ...utils.time import utc_timestamp
from ..store.registry import available, key_for, register
from . import processing

STATE_FILENAME = processing.STATE_FILENAME

#: The only sections this writer owns in the shared state file.
OWNED_SECTIONS = ("version", "outputs", "workflows", "grid", "domains")

#: What an absent or unreadable state file looks like.
EMPTY = {"version": 1, "outputs": {}, "workflows": {}, "grid": {}}


def state_path(session):
    """Return the project-local processing state file, or None without a project."""
    if not session.project_folder:
        return None
    return str(processing.state_path(session.project_folder))


def load(session) -> dict:
    """Load the morphology registry onto the session and return it."""
    if not session.project_folder:
        session.processing_state = _empty()
        return session.processing_state

    state = processing.load(session.project_folder)
    for key, default in EMPTY.items():
        state.setdefault(key, default if not isinstance(default, dict) else {})
    session.processing_state = state
    return state


def save(session) -> None:
    """Write the sections this registry owns, preserving the others.

    Kept only for callers that still mutate the compatibility session directly.
    New operations below update exactly one section or entry atomically.
    """
    if not session.project_folder:
        return
    state = session.processing_state or {}
    owned = {name: state[name] for name in OWNED_SECTIONS if name in state}
    try:
        session.processing_state = processing.overlay(
            session.project_folder, owned)
    except Exception as error:
        session.say(f"WARNING: Could not save processing state: {error}")


def _empty() -> dict:
    return {
        key: value if not isinstance(value, dict) else {}
        for key, value in EMPTY.items()
    }


def output_key(session, path) -> str:
    """Return a stable registry key for an output path."""
    return key_for(session.project_folder, path)


def mark_prepared(session, path, name=None, loaded=False, algorithm=None) -> None:
    """Record that an output file has been prepared."""
    entry = register(session.project_folder, path,
                     name=name, loaded=loaded, algorithm=algorithm)
    if entry is not None:
        session.processing_state = processing.load(session.project_folder)


def is_prepared(session, path) -> bool:
    """Return True when an output is recorded and present on disk."""
    return available(session.project_folder, path)


def record_outputs(session, algorithm, params, result) -> None:
    """Record the file outputs a processing call declared."""
    if not result:
        return

    produced = [value for key, value in params.items()
                if key.upper().startswith("OUTPUT") and isinstance(value, str)]
    if isinstance(result, dict):
        produced += [value for key, value in result.items()
                     if key.upper().startswith("OUTPUT") and isinstance(value, str)]

    for path in produced:
        if path and path != "TEMPORARY_OUTPUT":
            mark_prepared(session, path, name=os.path.basename(path),
                          loaded=False, algorithm=algorithm)


def mark_workflow(session, workflow, status, message="", **metadata) -> None:
    """Record a project-local workflow status such as execute-all completion."""
    if not workflow:
        return

    timestamp = utc_timestamp()
    workflows = processing.section(session.project_folder, "workflows", {})
    if not isinstance(workflows, dict):
        workflows = {}
    entry = workflows.get(workflow, {})
    if not isinstance(entry, dict):
        entry = {}
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

    for key, value in metadata.items():
        if value is not None:
            entry[key] = value

    processing.set_entry(
        session.project_folder, "workflows", workflow, entry)
    session.processing_state = processing.load(session.project_folder)


def workflow_status(session, workflow) -> dict:
    """Return a saved workflow status entry."""
    workflows = processing.section(session.project_folder, "workflows", {})
    return workflows.get(workflow, {}) if isinstance(workflows, dict) else {}


def save_domain_plan(session, plan) -> list:
    """Record each domain's mask and target DEM for Morphology Setup."""
    domains = [
        {key: entry[key] for key in
         ("domain_id", "outlet_id", "name", "mask", "directory", "dem_path")}
        for entry in plan
    ]
    session.processing_state = processing.overlay(
        session.project_folder, {"domains": domains})
    return domains


def saved_domain_plan(session) -> list:
    """Return the recorded domain plan."""
    plan = session.processing_state.get("domains")
    if not isinstance(plan, list) and session.project_folder:
        plan = processing.section(session.project_folder, "domains")
    return list(plan) if isinstance(plan, list) else []


def save_grid_contract(session, l0_header, l2_header, multiplier) -> dict:
    """Record the validated L0/L2 headers and multiplier for later resumes."""
    from ...grid import validate_l0_l2_alignment

    ratio = int(multiplier)
    validate_l0_l2_alignment(l0_header, l2_header, ratio)
    grid = {
        "l0_header": dict(l0_header),
        "l2_header": dict(l2_header),
        "l2_ratio_to_l0": ratio,
        "updated_at": utc_timestamp(),
    }
    session.processing_state = processing.overlay(
        session.project_folder, {"grid": grid})
    return grid


def saved_grid_contract(session):
    """Return the saved L0/L2 grid contract, or None when it is unusable."""
    from ...grid import validate_l0_l2_alignment

    grid = session.processing_state.get("grid") or {}
    if not grid and session.project_folder:
        grid = processing.section(session.project_folder, "grid", {}) or {}
    l0_header, l2_header = grid.get("l0_header"), grid.get("l2_header")
    if not isinstance(l0_header, dict) or not isinstance(l2_header, dict):
        return None
    try:
        validate_l0_l2_alignment(l0_header, l2_header, int(grid.get("l2_ratio_to_l0")))
    except (TypeError, ValueError) as error:
        session.say(f"WARNING: Discarding the saved L0/L2 grid contract: {error}")
        return None
    return grid


__all__ = [
    "EMPTY", "OWNED_SECTIONS", "STATE_FILENAME",
    "is_prepared", "load", "mark_prepared", "mark_workflow", "output_key",
    "record_outputs", "save", "save_domain_plan", "save_grid_contract",
    "saved_domain_plan", "saved_grid_contract", "state_path", "workflow_status",
]
