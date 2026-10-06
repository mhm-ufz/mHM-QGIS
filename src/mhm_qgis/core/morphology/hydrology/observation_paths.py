"""Hydrology observation output paths."""
from __future__ import annotations

from pathlib import Path

from ...handlers.store.layout import (
    streamflow_observation_folder as project_streamflow_observation_folder,
)


def streamflow_observation_folder(project_folder) -> Path:
    """Return the project-local mHM streamflow observation folder."""
    return Path(project_streamflow_observation_folder(project_folder))
