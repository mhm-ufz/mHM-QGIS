# -*- coding: utf-8 -*-
"""Cache the meteorology folder inspection between sessions.

`inspect_meteo_folder` opens every NetCDF in a folder twice: once for its
spatial signature and once to validate its time axis. On a multi-decade
ERA5-Land record that is over a thousand file opens per folder, which froze the
plugin for around half a minute on every project load. The result only depends
on the files themselves, so it is fingerprinted and reused.
"""
from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from .api import _files_for_spec, inspect_meteo_folder
from .types import MeteoFolderSpec, SpatialMetadata
from ...handlers.state.cache import cached_payload, fingerprint, store_payload


SECTION = "meteo_inspection"


# What `inspect_meteo_folder` actually reads. The output cadence and the
# aggregation are preparation choices it never sees, so including them only made
# a changed aggregation silently re-open every file in the folder.
_INSPECTED_FIELDS = (
    "kind", "source", "crs", "pattern",
    "temporal_resolution", "unit", "cumulative", "cumulative_resolution",
)


def inspection_fingerprint(spec: MeteoFolderSpec) -> tuple[str, list[Path]]:
    """Return the inspection fingerprint and the file list it was taken from.

    Listing the folder is cheap; opening the files is not, so the fingerprint is
    built from the listing plus each file's size and modification time.
    """
    values = asdict(spec)
    files = _files_for_spec(spec)
    digest = fingerprint(files, {
        **{name: values[name] for name in _INSPECTED_FIELDS},
        "folder": str(spec.folder),
        "inspection_version": 4,
    })
    return digest, files


def preparation_fingerprint(spec: MeteoFolderSpec) -> str:
    """Return a digest covering every option the prepared forcing depends on."""
    return fingerprint(_files_for_spec(spec), {
        **asdict(spec), "folder": str(spec.folder), "preparation_version": 1})


def _as_payload(metadata: SpatialMetadata) -> dict:
    payload = asdict(metadata)
    payload["files"] = [str(path) for path in metadata.files]
    payload["shape"] = list(metadata.shape)
    payload["bounds"] = list(metadata.bounds)
    return payload


def _from_payload(payload) -> SpatialMetadata | None:
    if not isinstance(payload, dict):
        return None
    try:
        values = dict(payload)
        values["files"] = tuple(Path(path) for path in values.get("files", ()))
        values["shape"] = tuple(values["shape"])
        values["bounds"] = tuple(values["bounds"])
        return SpatialMetadata(**values)
    except (KeyError, TypeError, ValueError):
        return None


def inspect_meteo_folder_cached(project_folder, spec, log=None):
    """Inspect one configured variable, reusing identical metadata options."""
    spec = spec.normalized()
    if not project_folder:
        return inspect_meteo_folder(spec)
    digest, files = inspection_fingerprint(spec)
    metadata = _from_payload(cached_payload(project_folder, SECTION, spec.kind, digest))
    if metadata is not None:
        return metadata
    metadata = inspect_meteo_folder(spec)
    store_payload(project_folder, SECTION, spec.kind, digest, _as_payload(metadata))
    return metadata


def inspect_meteo_inputs_cached(project_folder, inputs, log=None):
    """Inspect selected variables without preparing forcing."""
    return {spec.normalized().kind: inspect_meteo_folder_cached(project_folder, spec, log=log)
            for spec in inputs}
