# -*- coding: utf-8 -*-
"""Everything the plugin asks of nml-tools.

One call crosses this boundary: opening the namelist editor. The editor is
launched in-process and runs modally on the QGIS event loop, so it returns only
once the user closes it.
"""
from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping

LogCallback = Callable[[str], None]


def launch_profile_editor(
    schemas_dir: str | Path,
    output_dir: str | Path,
    initial_values: Mapping[str, Any] | None = None,
    initial_dimensions: Mapping[str, int] | None = None,
    *,
    log: LogCallback | None = None,
) -> int:
    """Open the nml-tools namelist editor over one project.

    `configure_qtpy_api()` must already have run: nml-tools loads Qt on the
    first call, and the API choice has to be settled before that.
    """
    from .mhm_tools_handler import capture_messages

    # Looked up on the module rather than bound at import time, so the editor
    # stays patchable and nml-tools keeps deferring its Qt import until called.
    from nml_tools import gui

    with capture_messages(log, logger_names=("nml_tools",)):
        initial_values = reconcile_domain_values(
            schemas_dir, output_dir, initial_values, initial_dimensions,
        )
        return gui.launch_gui(
            schemas_dir=schemas_dir,
            output_dir=output_dir,
            initial_values=initial_values,
            initial_dimensions=initial_dimensions,
        )


def reconcile_domain_values(schemas_dir, output_dir, initial_values, initial_dimensions):
    """Resize saved domain arrays in memory; the editor alone writes nml.json."""
    path = Path(output_dir) / "nml.json"
    if not path.is_file() or not initial_values or not initial_dimensions:
        return initial_values
    from nml_tools.gui.model import load_document, load_project, suggestion

    project = load_project(schemas_dir, output_dir)
    document = load_document(path, project)
    dimension = "max_domains" if "max_domains" in initial_dimensions else "n_domains"
    if dimension not in initial_dimensions:
        return initial_values
    profiles = document["file_profiles"]
    saved = profiles.get("main", {}).get("values", {})
    current = initial_values.get("main", {})
    block, field = ("directories_general", "dir_Morpho") if dimension == "max_domains" else ("config_input", "dem_path")

    def identities(values):
        return [str((Path(output_dir) / value).resolve())
                for value in values.get(block, {}).get(field, [])]

    old_ids, new_ids = identities(saved), identities(current)
    old_count = document["dimensions"][dimension]
    count = initial_dimensions[dimension]
    if old_count == count and old_ids == new_ids:
        return initial_values
    if len(old_ids) != old_count or len(new_ids) != count or len(set(old_ids)) != old_count:
        raise ValueError("Cannot identify saved namelist domains safely; check their DEM paths.")
    indices = [old_ids.index(value) if value in old_ids else None for value in new_ids]
    sizes = {**project.constants, **document["dimensions"], **initial_dimensions}
    overlay = deepcopy(initial_values)
    for profile in project.profiles:
        values = profiles.get(profile.key, {}).get("values", {})
        for page in profile.pages:
            for name, schema in page.schema.get("properties", {}).items():
                shape = schema.get("x-fortran-shape", [])
                shape = shape if isinstance(shape, list) else [shape]
                if dimension not in shape or name not in values.get(page.name, {}):
                    continue
                fields = overlay.setdefault(profile.key, {}).setdefault(page.name, {})
                if name not in fields:
                    fields[name] = _remap_domain_array(
                        values[page.name][name], suggestion(schema, sizes),
                        shape, dimension, indices,
                    )
    return overlay


def _remap_domain_array(value, default, shape, dimension, indices):
    if not shape:
        return deepcopy(value)
    result = deepcopy(default)
    for index in range(len(result)):
        source = indices[index] if shape[0] == dimension else index
        if source is not None and source < len(value):
            result[index] = _remap_domain_array(
                value[source], result[index], shape[1:], dimension, indices,
            )
    return result


__all__ = ["launch_profile_editor"]
