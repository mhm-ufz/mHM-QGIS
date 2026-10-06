"""Qt-free station discovery and discharge plot data."""
from datetime import UTC
from pathlib import Path

from ...handlers.file.discharge import records_from_mhm_file
from ...handlers.state.domain_state import gauge_records, load_state
from ...handlers.store.layout import streamflow_observation_folder
from ...handlers.store.paths import workspace_folder


def stations(project_folder):
    """List saved gauges, not stale files left by removed stations."""
    if not project_folder:
        return []
    workspace = Path(workspace_folder(project_folder))
    folder = Path(streamflow_observation_folder(project_folder))
    return [
        {"id": record["outlet_id"], "path": str(
            workspace / record["gauge_path"] if record["gauge_path"]
            else folder / record["gauge_filename"]
        )}
        for record in gauge_records(load_state(project_folder))
    ]


def discharge_series(path):
    """Return UTC epoch seconds and m³/s values; missing observations are NaN."""
    records = records_from_mhm_file(Path(path))
    return (
        [record.timestamp.replace(tzinfo=UTC).timestamp() for record in records],
        [record.value for record in records],
    )
