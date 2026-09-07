# -*- coding: utf-8 -*-
"""Canvas half of `groupBox_displayMorphLayers`.

Which file to show is decided by the QGIS-free resolver in
:mod:`mhm_qgis.core.handlers.store.morphology`; this module only drives the canvas.
"""
from __future__ import annotations

from ...core.handlers.store.morphology import DisplayOutput
from .canvas import show_raster


def show_output(dialog, output: DisplayOutput):
    """Display a resolved output without reading any selection widgets."""
    return show_raster(
        dialog,
        output.path,
        output.name,
        variable=output.variable,
        band=output.band,
        is_raster=output.is_raster,
    )


__all__ = ["show_output"]
