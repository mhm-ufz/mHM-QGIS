"""Small QGIS-optional helpers shared by version adapters."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ..core.grid import ceil_cellsize, is_geographic_unit
from ..core.handlers.store.paths import geometry_folder, morph_staging_folder
from ..core.handlers.store.layout import morph_folder
from ..core.handlers.state.nml_settings import load_settings


def repeat(value: Any, count: int) -> list[Any]:
    return [value for _ in range(max(1, int(count)))]


def namelist_settings(dialog: Any) -> dict[str, Any]:
    """Return the project-local processing handoff, if configured."""
    project = getattr(dialog, "project_folder", None)
    return load_settings(project) if project else {"version": 1}


def domain_count(dialog: Any) -> int:
    """Count saved active domains, with the legacy layer-field fallback."""
    project = getattr(dialog, "project_folder", None)
    if project:
        try:
            # Keep version metadata importable without a GUI runtime.
            from ..core.handlers.state.domain_state import (
                domain_count as saved_domain_count,
                load_state as load_domain_state,
                state_path as domain_state_path,
            )

            state = load_domain_state(project)
            configured = domain_state_path(project).is_file() and bool(
                state.get("outlets")
                or state.get("pour_points_source")
                or state.get("outlet_id_field")
                or state.get("dem_domain")
            )
            if configured:
                return max(1, saved_domain_count(state))
        except Exception:
            pass

    try:
        layer = dialog.input_combo("pour_points").currentLayer()
        if not layer or not layer.isValid():
            return 1
        names = {field.name().lower(): field.name() for field in layer.fields()}
        domain_field = names.get("is_domain")
        if not domain_field:
            return 1
        count = sum(
            1
            for feature in layer.getFeatures()
            if _truthy(feature.attribute(domain_field))
        )
        return max(1, count)
    except Exception:
        return 1


def geology_rows(dialog: Any) -> list[dict[str, Any]]:
    project = getattr(dialog, "project_folder", None)
    if not project:
        return []
    path = Path(geometry_folder(project)) / "geology_class_metadata.json"
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
        return max(len(rows), max(row["index"] for row in rows))
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
