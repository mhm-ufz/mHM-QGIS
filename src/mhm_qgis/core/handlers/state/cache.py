"""Fingerprinted result cache kept in the project processing state.

Both Execute All and Morphology Setup repeat expensive work whose result only
changes when its inputs change: inspecting hundreds of NetCDF files, formatting
geology, resampling LAI. Each stage records a fingerprint of its inputs next to
its outputs here, so an unchanged project reuses the result instead of redoing
it. The fingerprint is size and modification time, never file content, so
checking it stays cheap on large inputs.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from ...utils.time import utc_timestamp
from . import processing

STATE_FILENAME = processing.STATE_FILENAME
CACHE_VERSION = 1


def state_path(project_folder) -> Path:
    """Return the project-local processing state path."""
    return processing.state_path(project_folder)


def load_state(project_folder) -> dict:
    """Load the processing state, returning an empty mapping when unusable."""
    return processing.load(project_folder)


def save_state(project_folder, state: dict) -> None:
    """Compatibility wrapper that overlays the supplied sections atomically."""
    processing.overlay(project_folder, state)


def fingerprint(paths=(), config=None) -> str:
    """Return a digest of input paths plus a scalar configuration mapping.

    Missing paths are recorded as missing rather than skipped, so a deleted
    input invalidates the cache instead of silently matching.
    """
    digest = hashlib.sha256()
    digest.update(f"v{CACHE_VERSION}\n".encode())
    for value in sorted(str(path) for path in paths if str(path)):
        try:
            status = os.stat(value)
            digest.update(
                f"{value}|{status.st_size}|{status.st_mtime_ns}\n".encode())
        except OSError:
            digest.update(f"{value}|missing\n".encode())
    if config:
        digest.update(
            json.dumps(config, sort_keys=True, default=str).encode("utf-8"))
    return digest.hexdigest()


def cached_payload(project_folder, section: str, key: str, digest: str):
    """Return the stored payload when its fingerprint still matches."""
    entry = (load_state(project_folder).get(section) or {}).get(key)
    if not isinstance(entry, dict) or entry.get("fingerprint") != digest:
        return None
    return entry.get("payload")


def store_payload(project_folder, section: str, key: str, digest: str,
                  payload) -> None:
    """Record a payload against the fingerprint of the inputs that produced it."""
    processing.set_entry(project_folder, section, key, {
        "fingerprint": digest,
        "payload": payload,
        "updated_at": utc_timestamp(),
    })


def outputs_present(payload) -> bool:
    """Return True when every path a cached payload names is still on disk."""
    if not isinstance(payload, dict):
        return False
    paths = payload.get("outputs") or ()
    return bool(paths) and all(Path(path).is_file() for path in paths)


def outputs_newer_than_inputs(inputs, outputs) -> bool:
    """Return True when every output postdates every input.

    Used to adopt results that are already on disk but were produced before the
    cache existed. It is a heuristic, not a proof: an output older than one of
    its inputs certainly is stale, so refuse those and rebuild instead.
    """
    output_times = []
    for path in outputs:
        try:
            output_times.append(os.stat(str(path)).st_mtime_ns)
        except OSError:
            return False
    if not output_times:
        return False
    newest_input = 0
    for path in inputs:
        if not str(path):
            continue
        try:
            newest_input = max(newest_input, os.stat(str(path)).st_mtime_ns)
        except OSError:
            return False
    return min(output_times) >= newest_input


__all__ = [
    "cached_payload",
    "fingerprint",
    "load_state",
    "outputs_newer_than_inputs",
    "outputs_present",
    "save_state",
    "state_path",
    "store_payload",
]
