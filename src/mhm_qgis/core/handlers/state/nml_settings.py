"""Project-local inputs passed to the namelist GUI."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any
from datetime import datetime
from ....core.handlers.store.paths import temp_folder, workspace_folder


def settings_path(project_folder: str | Path) -> Path:
    return Path(temp_folder(project_folder)) / "nml-settings.json"


def load_settings(project_folder: str | Path) -> dict[str, Any]:
    path = settings_path(project_folder)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        value = {}
    if not isinstance(value, dict):
        value = {}
    value.setdefault("version", 1)
    return value


def update_section(
    project_folder: str | Path,
    section: str,
    value: dict[str, Any] | list[Any],
) -> Path:
    state = load_settings(project_folder)
    state[section] = value
    return save_settings(project_folder, state)


def validate_times(values, version="v6") -> dict[str, str]:
    """Validate model dates without silently discarding unsupported precision."""
    names = ("sim_start", "eval_start", "sim_end")
    dates = [datetime.fromisoformat(str(values[name])) for name in names]
    if any(date.tzinfo is not None or date.minute or date.second or date.microsecond for date in dates):
        raise ValueError("Model dates must be local, whole-hour timestamps.")
    if "5" in version and any(date.hour for date in dates):
        raise ValueError("mHM 5 simulation periods require whole dates (00:00).")
    if not dates[0] <= dates[1] <= dates[2]:
        raise ValueError("Dates must satisfy simulation start ≤ evaluation start ≤ simulation end.")
    return {name: date.isoformat(timespec="minutes") for name, date in zip(names, dates)}


def save_settings(
    project_folder: str | Path,
    state: dict[str, Any],
) -> Path:
    path = settings_path(project_folder)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(state)
    payload["version"] = 1
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2, sort_keys=True)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return path


def relative_workspace_path(project_folder: str | Path, value: str | Path) -> str:
    return os.path.relpath(value, workspace_folder(project_folder)).replace("\\", "/")


def sync_domain_settings(project_folder: str | Path) -> Path:
    """Project saved domain and gauge records into the namelist handoff."""
    from .domain_state import (
        assign_domain_ids,
        gauge_records,
        load_state as load_domain_state,
    )

    domain_state = load_domain_state(project_folder)
    assign_domain_ids(domain_state)
    state = load_settings(project_folder)
    state["domain_definition"] = {
        "mode": str(domain_state.get("definition_mode", "") or ""),
        "dem_domain": bool(domain_state.get("dem_domain", False)),
    }
    gauges = gauge_records(domain_state)
    from ..store.layout import domain_dem_path, morph_folder
    from ..store.paths import data_folder
    state["domains"] = [{
        "domain_id": 1, "name": "master", "outlet_id": "",
        "is_dem_domain": not any(record.get("is_domain") for record in domain_state.get("outlets", {}).values()),
        "directory": relative_workspace_path(project_folder, morph_folder(project_folder)),
        "dem_path": relative_workspace_path(project_folder, domain_dem_path(project_folder, "master")),
        "data_path": relative_workspace_path(project_folder, data_folder(project_folder)) + "/",
        "gauge_ids": [int(gauge["gauge_id"]) for gauge in gauges
                      if gauge.get("gauge_id") is not None and gauge.get("domain_ids")],
    }]
    state["gauges"] = gauges
    return save_settings(project_folder, state)


__all__ = [
    "load_settings",
    "relative_workspace_path",
    "save_settings",
    "settings_path",
    "sync_domain_settings",
    "update_section",
]
