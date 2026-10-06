"""The set of layers the QGIS project currently holds.

Registering a layer, finding one again, and dropping the ones that came out of
a folder being rebuilt. Nothing here constructs a layer -- see `loader`.
"""
from __future__ import annotations

import os


def _project():
    """Return the running QgsProject, or None when QGIS is absent."""
    try:
        from qgis.core import QgsProject
    except ImportError:
        return None
    return QgsProject.instance()


def add(layer):
    """Register a layer with the project so it appears in the layer tree."""
    project = _project()
    if project is None or layer is None:
        return None
    project.addMapLayer(layer)
    return layer


def find_by_name(name: str):
    """Return the loaded layer with this name, if the project already has one."""
    project = _project()
    if project is None:
        return None
    matches = project.mapLayersByName(name)
    return matches[0] if matches else None


def normalized_source(source) -> str:
    """Normalize a QGIS layer source for comparison.

    Sources arrive as bare paths and as provider URIs with a `|layername=...`
    tail, in either slash style and either case, so equality only means
    anything after the path half is resolved and the whole thing is folded.
    """
    if not source:
        return ""

    base_source = source.split("|")[0]
    if os.path.exists(base_source):
        suffix = source[len(base_source):]
        base_source = os.path.abspath(base_source)
        return (base_source + suffix).replace("\\", "/").lower()

    return source.replace("\\", "/").lower()


def find_by_source(source):
    """Return a loaded layer reading from the same source path or URI."""
    project = _project()
    if project is None:
        return None
    target = normalized_source(source)
    for layer in project.mapLayers().values():
        if normalized_source(layer.source()) == target:
            return layer
    return None


def remove_under(folder, log=None) -> int:
    """Drop every loaded layer whose source lives under `folder`.

    Called when a folder is about to be rebuilt: the files are going away, so
    the layers pointing at them have to go first or QGIS keeps stale handles.
    Returns how many were removed.
    """
    project = _project()
    if project is None or not folder:
        return 0

    folder = str(folder).replace("\\", "/")
    doomed = []
    for layer_id, layer in project.mapLayers().items():
        if folder in layer.source().replace("\\", "/"):
            doomed.append(layer_id)
            if log is not None:
                log(f"Removing layer from QGIS: {layer.name()}")

    for layer_id in doomed:
        project.removeMapLayer(layer_id)
    return len(doomed)


__all__ = [
    "add",
    "find_by_name",
    "find_by_source",
    "normalized_source",
    "remove_under",
]
