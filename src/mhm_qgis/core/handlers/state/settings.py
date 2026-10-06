"""Project-local numeric settings; the packaged file supplies initial defaults."""
import math
import re
from pathlib import Path

from ..store.paths import settings_path

DEFAULTS = {"grid_alignment_gap_limit": 1.0e-7, "max_snap_buffer_distance": 1000.0}
PACKAGED_SETTINGS = Path(__file__).parents[3] / "settings.yaml"


def ensure(project_folder):
    """Copy defaults once, never overwriting a project's choices."""
    path = Path(settings_path(project_folder))
    path.parent.mkdir(parents=True, exist_ok=True)
    content = PACKAGED_SETTINGS.read_text(encoding="utf-8")
    try:
        with path.open("x", encoding="utf-8") as destination:
            destination.write(content)
    except FileExistsError:
        pass
    return path


def read(project_folder=None, *, path=None):
    """Reload the supported flat numeric YAML settings and validate their units."""
    path = Path(path) if path is not None else (
        ensure(project_folder) if project_folder else PACKAGED_SETTINGS
    )
    values = dict(DEFAULTS)
    try:
        content = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return values
    for key in DEFAULTS:
        match = re.search(rf"(?m)^\s*{key}\s*:\s*([^#\n]*)", content)
        if match:
            try:
                values[key] = float(match[1].strip())
            except ValueError as error:
                raise ValueError(f"{key} in {path} must be numeric.") from error
        if not math.isfinite(values[key]) or values[key] < 0:
            raise ValueError(f"{key} in {path} must be finite and non-negative.")
    return values
