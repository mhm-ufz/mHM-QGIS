"""The set of layers the QGIS project currently holds.

Registering a layer, finding one again, and dropping the ones that came out of
a folder being rebuilt. Nothing here constructs a layer -- see `loader`.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from urllib.parse import unquote, urlparse

_NETCDF_SOURCE = re.compile(r'^NETCDF:"([^"]+)"(.*)$', re.IGNORECASE)


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
    tail. Only the physical path uses the operating system's case rules;
    provider suffixes retain their own semantics.
    """
    if not source:
        return ""

    path, suffix = _source_parts(source)
    if path is not None:
        normalized = os.path.normcase(os.path.abspath(path)).replace("\\", "/")
        return normalized + suffix

    return str(source).replace("\\", "/")


def _source_parts(source) -> tuple[str | None, str]:
    """Return a local provider path and the source suffix used to address it."""
    text = str(source or "")
    match = _NETCDF_SOURCE.match(text)
    if match:
        return match.group(1), f"|NETCDF{match.group(2)}"

    split_at = min((index for marker in ("|", "?") if (index := text.find(marker)) >= 0),
                   default=len(text))
    base, suffix = text[:split_at], text[split_at:]
    parsed = urlparse(base)
    if parsed.scheme == "file":
        path = unquote(parsed.path)
        if parsed.netloc:
            path = f"//{parsed.netloc}{path}"
        if re.match(r"^/[A-Za-z]:/", path):
            path = path[1:]
        return path, suffix
    if os.path.isabs(base) or os.path.exists(base):
        return base, suffix
    return None, text


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

    root = Path(folder).resolve()
    doomed = []
    for layer_id, layer in project.mapLayers().items():
        source, _suffix = _source_parts(layer.source())
        if source is not None and Path(source).resolve().is_relative_to(root):
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
