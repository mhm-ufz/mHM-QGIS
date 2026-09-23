"""Reset the plugin-owned temporary morphology workspace."""
from __future__ import annotations

from pathlib import Path

from ...handlers.state import processing
from ...handlers.state.domain_state import load_state, save_state, state_path
from ...handlers.store.paths import temp_morphology_folder


_DOMAIN_OUTPUT_KEYS = (
    "picked",
    "picked_point",
    "picked_x",
    "picked_y",
    "picked_crs",
    "catchment_area",
    "catchment_area_m2",
    "mask_path",
    "mask_value",
    "delineation",
    "confidence",
    "snap_count",
    "snap_distance_m",
    "vector_path",
)


def clear_geometry(project_folder, *, log=None) -> int:
    """Delete files below ``.temp/Morphology`` and invalidate derived state.

    The folder is a plugin-owned scratch boundary. User selections and gauge
    assignments remain in domain state; only locations and files derived from
    the former geometry are removed.
    """
    folder = Path(temp_morphology_folder(project_folder))
    folder.mkdir(parents=True, exist_ok=True)
    deleted = 0
    for path in sorted(folder.rglob("*"), key=lambda item: len(item.parts), reverse=True):
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

    _invalidate_domains(project_folder)
    processing.overlay(
        project_folder,
        {
            "version": processing.STATE_VERSION,
            "outputs": {},
            "workflows": {},
            "grid": {},
            "domains": [],
        },
    )
    return deleted


def _invalidate_domains(project_folder) -> None:
    path = state_path(project_folder)
    if not path.is_file():
        return
    state = load_state(project_folder)
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
        save_state(project_folder, state)


__all__ = ["clear_geometry"]
