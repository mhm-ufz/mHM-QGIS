"""Undo a ▶ (A. Morphology, B. Meteorology, C. Observations): its files and reuse records,
so the next run prepares everything as the first one did. What the Set buttons saved stays."""
from __future__ import annotations

from pathlib import Path

from ..handlers.state import nml_outputs, processing
from ..handlers.state.domain_state import load_state, save_state, state_path
from ..handlers.store import layout
from ..handlers.store.paths import temp_morphology_folder

WORKFLOWS = {
    "morphology": ("execute_all_morphology", "execute_all_morphology_publish", "geology_processing",
                   "land_cover_processing", "soil_processing"),
    "meteorology": ("execute_all_meteo",),
    "observations": ("execute_all_observations",),
}
CATEGORY = {"meteorology": "meteorology", "observations": "observation"}
_DOMAIN_OUTPUT_KEYS = (
    "picked", "picked_point", "picked_x", "picked_y", "picked_crs", "catchment_area", "catchment_area_m2",
    "mask_path", "mask_value", "delineation", "confidence", "snap_count", "snap_distance_m", "vector_path",
)


def clear(setup_folder, group, *, log=None) -> int:
    """Delete `group`'s prepared files and forget its reuse records; returns the files deleted."""
    state, v6 = processing.load(setup_folder), layout.is_v6(setup_folder)
    outputs = state.get("outputs") or {}
    if group == "morphology":
        folders = {temp_morphology_folder(setup_folder), layout.morph_folder(setup_folder),
                   layout.lai_folder(setup_folder)}
        paths = [path for folder in folders for path in Path(folder).rglob("*")]
        invalidate_domains(setup_folder)
        nml_outputs.register_values(setup_folder, "main", "config_input", {},
                                    managed=nml_outputs.MORPHOLOGY_FIELDS, v6=v6)
        sections = {"stages": {}, "exports": {}, "domains": []}
    else:
        paths = [Path(path) for path, entry in outputs.items() if entry.get("category") == CATEGORY[group]]
        if group == "meteorology":
            paths.append(layout.meteo_mask_path(setup_folder))
            nml_outputs.register_meteo(setup_folder, {}, v6=v6)
            sections = {"meteo_source_fingerprint": None, "meteo_period": None}
        else:
            nml_outputs.register_observations(setup_folder, {}, v6=v6)
            sections = {}
    deleted = _delete(paths, log)
    dropped = (*WORKFLOWS[group], "run_mhm")
    processing.overlay(setup_folder, {
        **sections,
        "workflows": {key: entry for key, entry in (state.get("workflows") or {}).items() if key not in dropped},
        "outputs": {path: entry for path, entry in outputs.items() if Path(path).exists()},
    })
    return deleted


def _delete(paths, log) -> int:
    """Files and links first, then the emptied folders, deepest first."""
    deleted = 0
    for path in sorted(paths, key=lambda item: len(item.parts), reverse=True):
        try:
            if path.is_file() or path.is_symlink():
                path.unlink()
                deleted += 1
                if log:
                    log(f"Deleted: {path.name}")
            elif path.is_dir():
                path.rmdir()
        except OSError as error:
            if log:
                log(f"WARNING: Could not remove {path}: {error}")
    return deleted


def invalidate_domains(setup_folder) -> None:
    """Forget the masks and outlet results the deleted geometry gave; gauges and choices stay."""
    if not state_path(setup_folder).is_file():
        return
    state = load_state(setup_folder)
    changed = False
    for key in ("dem_mask_path", "merged_mask_path"):
        if state.get(key):
            state[key] = ""
            changed = True
    for record in state.get("outlets", {}).values():
        for key in _DOMAIN_OUTPUT_KEYS:
            if key in record:
                del record[key]
                changed = True
    if changed:
        save_state(setup_folder, state)
