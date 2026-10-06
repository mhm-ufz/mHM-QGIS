"""Running QGIS Processing algorithms.

The one place that imports `processing`. That import only resolves inside a
configured QGIS runtime, so keeping it here means a module that merely *sequences* algorithms stays
importable outside QGIS.

`run` never raises: a failed algorithm returns None, having reported through
the callbacks, because every caller already branches on a falsy result.
"""
from __future__ import annotations

from ...core.utils.report import CRITICAL


def initialize():
    """Initialize real Processing providers when running outside QGIS Desktop."""
    import sys
    from pathlib import Path

    from qgis.core import QgsApplication

    for root in (QgsApplication.pkgDataPath(), QgsApplication.prefixPath()):
        plugins = str(Path(root) / "python" / "plugins")
        if Path(plugins).is_dir() and plugins not in sys.path:
            sys.path.append(plugins)
    from processing.core.Processing import Processing

    Processing.initialize()


def run(name: str, params: dict, *, log=None, report=None):
    """Run one Processing algorithm, returning None when it fails."""
    if log is not None:
        log(f"Running algorithm: {name}...")
    try:
        import processing
    except ImportError as error:
        message = f"QGIS Processing is unavailable: {error}"
        if log is not None:
            log(f"ERROR: {message}")
        if report is not None:
            report(CRITICAL, "Processing Error", message)
        return None

    try:
        result = processing.run(name, params)
    except Exception as error:
        if log is not None:
            log(f"ERROR: Algorithm '{name}' failed. Details: {error}")
        if report is not None:
            report(CRITICAL, "Processing Error",
                   f"Algorithm '{name}' failed.\nCheck the log for details.")
        return None

    if log is not None:
        log(f"Algorithm '{name}' finished successfully.")
    return result


__all__ = ["initialize", "run"]
