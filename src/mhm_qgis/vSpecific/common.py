"""Small QGIS-optional helpers shared by version adapters."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ..core.grid import ceil_cellsize, is_geographic_unit
from ..core.handlers.state.nml_settings import load_settings
from ..core.handlers.store.layout import morph_folder
from ..core.handlers.store.paths import morph_staging_folder, temp_morphology_folder


def model_data_path(dialog: Any) -> str:
    """Return the model-input root relative to this setup's namelists."""
    return "data"


def repeat(value: Any, count: int) -> list[Any]:
    return [value for _ in range(max(1, int(count)))]


def namelist_settings(dialog: Any) -> dict[str, Any]:
    """Return the project-local processing handoff, if configured."""
    project = getattr(dialog, "project_folder", None)
    return load_settings(project) if project else {"version": 1}


def apply_registered(values: dict, settings: dict) -> None:
    """Overlay the plugin-managed `namelist_values`, then the wizard's one-shot `intro_values`."""
    for section in ("namelist_values", "intro_values"):
        for profile, namelists in (settings.get(section) or {}).items():
            if not isinstance(namelists, dict) or profile not in ("main", "parameter", "output", "meta"):
                continue
            for namelist, fields in namelists.items():
                if isinstance(fields, dict):
                    values.setdefault(profile, {}).setdefault(namelist, {}).update(fields)


def domain_count(dialog: Any) -> int:
    """Single-domain workspaces may contain several catchments, not domains."""
    return 1


def geology_rows(dialog: Any) -> list[dict[str, Any]]:
    project = getattr(dialog, "project_folder", None)
    if not project:
        return []
    path = Path(temp_morphology_folder(project)) / "geology_class_metadata.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return []
    rows = []
    for raw in data.get("classes", []) if isinstance(data, dict) else []:
        if not isinstance(raw, dict):
            continue
        try:
            index = int(raw.get("geo_param"))
        except (TypeError, ValueError):
            continue
        if index > 0:
            rows.append({"index": index, "value": raw.get("parameter_value")})
    return sorted(rows, key=lambda row: row["index"])


def geology_count(dialog: Any) -> int:
    rows = geology_rows(dialog)
    if rows:
        return max(len(rows), *(row["index"] for row in rows))
    project = getattr(dialog, "project_folder", None)
    # Published first, then the Execute All staging copy, so the count is
    # available before Morphology Setup has published anything.
    path = None
    if project:
        for folder in (morph_folder(project), morph_staging_folder(project)):
            candidate = Path(folder) / "geology_classdefinition.txt"
            if candidate.is_file():
                path = candidate
                break
    if path:
        try:
            match = re.search(r"nGeo_Formations\s+(\d+)", path.read_text(encoding="utf-8"))
            if match:
                return max(1, int(match.group(1)))
        except (OSError, UnicodeError):
            pass
    return 16


def geoparameter(
    dialog: Any, count: int, *, component_major: bool = True
) -> list[list[float]] | None:
    """Return GeoParam data when plugin metadata provides parameter values."""
    rows = geology_rows(dialog)
    if not rows or not any(row["value"] is not None for row in rows):
        return None
    values = [[1.0, 1000.0, 100.0, 1.0, 1.0] for _ in range(count)]
    for row in rows:
        if row["value"] is None or row["index"] > count:
            continue
        try:
            values[row["index"] - 1][2] = float(row["value"])
        except (TypeError, ValueError):
            continue
    if component_major:
        return [[row[column] for row in values] for column in range(5)]
    return values


def resolution(dialog: Any, method_name: str) -> float | None:
    try:
        value = float(getattr(dialog, method_name)())
    except (AttributeError, TypeError, ValueError):
        return None
    if value <= 0:
        return None
    unit_method = getattr(dialog, "current_grid_unit", None)
    try:
        unit = unit_method() if unit_method else ""
    except Exception:
        unit = ""
    return ceil_cellsize(value, unit)


def coordinate_flag(dialog: Any) -> int:
    method = getattr(dialog, "current_grid_unit", None)
    try:
        return int(is_geographic_unit(method() if method else ""))
    except Exception:
        return 0


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "t", "yes", "y"}
