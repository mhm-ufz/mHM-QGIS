# -*- coding: utf-8 -*-
"""QGIS-free categorical lookup preparation and atomic publication.

`native_worker` runs this in a child process. Formatting a vector categorical
input onto an L0 grid peaked at 5.5 GiB of resident memory and the Linux OOM
killer took the whole QGIS process with it, so this work must never run inside
QGIS.
"""
from __future__ import annotations

import os
from pathlib import Path
from tempfile import TemporaryDirectory

from .geology import write_geology_metadata


def _publish(replacements, removals, temporary):
    destinations = [Path(destination) for _, destination in replacements]
    tracked = []
    for path in destinations + [Path(path) for path in removals]:
        if path not in tracked:
            tracked.append(path)
    backups = []
    published = []
    try:
        for index, path in enumerate(tracked):
            if path.is_file():
                backup = Path(temporary) / f".backup_{index}_{path.name}"
                os.replace(path, backup)
                backups.append((backup, path))
        for source, destination in replacements:
            Path(destination).parent.mkdir(parents=True, exist_ok=True)
            os.replace(source, destination)
            published.append(Path(destination))
    except Exception:
        for path in published:
            path.unlink(missing_ok=True)
        for backup, path in reversed(backups):
            if backup.is_file():
                os.replace(backup, path)
        raise


def run_lookup_job(job, progress=None):
    """Rasterize and format one categorical lookup input, then publish it.

    QGIS-free so it can run inside `native_worker`: formatting a vector input on
    an L0 grid peaked at 5.5 GiB and was OOM-killing the QGIS process.
    """
    from ....externals.mhm_tools_handler import prepare_categorical_file

    def report(value):
        if progress is not None:
            progress(value)

    geometry = Path(job["geometry"])
    geometry.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=f'mhm_qgis_{job["kind"]}_', dir=geometry) as temp:
        temporary = Path(temp)
        output = Path(job["output"])
        prepared_output = temporary / output.name
        definition = Path(job["definition"]) if job.get("definition") else None
        prepared_definition = temporary / definition.name if definition else None
        metadata = Path(job["metadata"]) if job.get("metadata") else None
        prepared_metadata = temporary / metadata.name if metadata else None
        if prepared_metadata is not None:
            write_geology_metadata(
                job["lookup_table"], job["class_field"], prepared_metadata
            )
        report(15)
        prepare_categorical_file(
            job["kind"],
            job["input_file"],
            job["filled_dem"],
            prepared_output,
            job["lookup_table"],
            job["mapping_field"],
            job["class_field"],
            input_variable=job.get("input_variable") or None,
            is_vector=job["is_vector"],
            classdefinition_file=prepared_definition,
            input_crs=job["input_crs"],
            dem_crs=job["dem_crs"],
        )
        report(85)
        replacements = [(prepared_output, output)]
        if prepared_definition is not None:
            replacements.append((prepared_definition, definition))
        if prepared_metadata is not None:
            replacements.append((prepared_metadata, metadata))
        _publish(replacements, job["removals"], temporary)
    report(100)
    return {
        "kind": job["kind"],
        "output": str(output),
        "definition": str(definition) if definition else "",
        "metadata": str(metadata) if metadata else "",
    }


__all__ = ["run_lookup_job"]
