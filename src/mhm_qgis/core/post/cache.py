"""Where a run's post-processing products live, and when they are stale.

`.temp/Post/<run>/` of the setup holds `cells_<file>.npy`, `weights/<basin>.npy`,
`area.npy`, `sealed.npy`, `series/<var>.npy`, `basins/<var>.npy`,
`stats/<var>.npz`, `monthly/<var>.npy`, `annual/<var>_max.npy`/`_min.npy` and
`index.json`. Each variable's products carry a fingerprint of their sources (size
and mtime, `handlers.state.cache.fingerprint`); only stale variables are rescanned.
"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from ..handlers.state.cache import fingerprint
from ..handlers.store.paths import temp_folder

SCAN_VERSION = 1
ASK_ABOVE_BYTES = 2 * 1024 ** 3  # the user confirms a series cache larger than this


def folder(ref) -> Path:
    return Path(temp_folder(ref.setup)) / "Post" / f"{ref.name or 'current'}_{ref.index}"


def load_index(root) -> dict:
    try:
        return json.loads((Path(root) / "index.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"variables": {}}


def save_index(root, index) -> None:
    path = Path(root) / "index.json"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(index, indent=1), encoding="utf-8")
    os.replace(temporary, path)


def variable_fingerprint(sources, name, series) -> str:
    return fingerprint(sources, {"variable": name, "series": bool(series), "scan": SCAN_VERSION})


def stale(root, fingerprints) -> list[str]:
    """The variables whose recorded fingerprint differs from `fingerprints`."""
    recorded = load_index(root)["variables"]
    return [name for name, digest in fingerprints.items()
            if recorded.get(name, {}).get("fingerprint") != digest]


def series_bytes(cells, steps, variables) -> int:
    return 4 * cells * steps * len(variables)


def check_disk(root, needed) -> None:
    """Refuse a cache that does not fit on the disk (the LAI pattern)."""
    existing = Path(root)
    while not existing.exists():
        existing = existing.parent
    free = shutil.disk_usage(existing).free
    if needed > free:
        raise ValueError(f"The series cache needs {needed / 1e9:.1f} GB but only "
                         f"{free / 1e9:.1f} GB is free on {existing}. Choose fewer variables.")
