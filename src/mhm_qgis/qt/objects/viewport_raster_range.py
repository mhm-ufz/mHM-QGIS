"""Visible-extent raster statistics and gray-renderer updates."""
from __future__ import annotations

import contextlib
import math

from qgis.PyQt import QtCore


def raster_window_range(path, bounds, bounds_crs="", max_samples=262_144):
    """Return finite min/max values for one clipped, capped raster window."""
    import numpy as np
    import rasterio
    from rasterio.warp import transform_bounds
    from rasterio.windows import from_bounds

    with rasterio.open(path) as source:
        left, bottom, right, top = map(float, bounds)
        if bounds_crs and source.crs and str(source.crs) != str(bounds_crs):
            left, bottom, right, top = transform_bounds(
                bounds_crs, source.crs, left, bottom, right, top, densify_pts=21
            )
        dataset_bounds = source.bounds
        left = max(left, dataset_bounds.left)
        right = min(right, dataset_bounds.right)
        bottom = max(bottom, dataset_bounds.bottom)
        top = min(top, dataset_bounds.top)
        if left >= right or bottom >= top:
            return None
        window = from_bounds(left, bottom, right, top, source.transform)
        width = max(1, int(math.ceil(window.width)))
        height = max(1, int(math.ceil(window.height)))
        scale = max(1.0, math.sqrt((width * height) / max_samples))
        out_shape = (
            max(1, int(math.ceil(height / scale))),
            max(1, int(math.ceil(width / scale))),
        )
        values = source.read(1, window=window, out_shape=out_shape, masked=True)
        compressed = np.asarray(values.compressed(), dtype=float)
        compressed = compressed[np.isfinite(compressed)]
        if compressed.size == 0:
            return None
        minimum = float(np.min(compressed))
        maximum = float(np.max(compressed))
        if minimum == maximum:
            maximum = minimum + max(abs(minimum) * 1e-9, 1e-9)
        return minimum, maximum


class ViewportRasterRangeController(QtCore.QObject):
    """Debounce extent changes and apply only the newest statistics result."""

    def __init__(self, canvas, layer, path, coordinator, parent=None):
        super().__init__(parent or canvas)
        self.canvas = canvas
        self.layer = layer
        self.path = str(path)
        self.coordinator = coordinator
        self._closed = False
        self._generation = 0
        self._pending = None
        self._timer = QtCore.QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(200)
        self._timer.timeout.connect(self._request)
        self.canvas.extentsChanged.connect(self.schedule)
        self.schedule()

    def schedule(self):
        if self._closed:
            return
        self._generation += 1
        self._timer.start()

    def _request(self):
        extent = self.canvas.extent()
        crs = self.canvas.mapSettings().destinationCrs()
        request = (
            self._generation,
            (
                extent.xMinimum(),
                extent.yMinimum(),
                extent.xMaximum(),
                extent.yMaximum(),
            ),
            crs.authid() if crs is not None and crs.isValid() else "",
        )
        self._pending = request
        generation, bounds, authid = request
        def runner(_task):
            return raster_window_range(self.path, bounds, authid)
        started = self.coordinator.submit(
            "flow-accumulation-visible-range",
            "Flow accumulation visible range",
            runner,
            on_success=lambda result: self._completed(generation, result),
            on_error=lambda _message: self._completed(generation, None),
        )
        if started:
            self._pending = None

    def _completed(self, generation, result):
        if self._closed:
            return
        if generation == self._generation and result is not None:
            self._apply(*result)
        if self._pending is not None:
            self._timer.start(0)

    def _apply(self, minimum, maximum):
        from qgis.core import QgsContrastEnhancement, QgsSingleBandGrayRenderer

        provider = self.layer.dataProvider()
        renderer = QgsSingleBandGrayRenderer(provider, 1)
        contrast = QgsContrastEnhancement(provider.dataType(1))
        contrast.setMinimumValue(float(minimum))
        contrast.setMaximumValue(float(maximum))
        contrast.setContrastEnhancementAlgorithm(
            QgsContrastEnhancement.ContrastEnhancementAlgorithm.StretchToMinimumMaximum
        )
        renderer.setContrastEnhancement(contrast)
        self.layer.setRenderer(renderer)
        self.layer.triggerRepaint()
        self.canvas.refresh()

    def close(self):
        self._closed = True
        self._timer.stop()
        with contextlib.suppress(TypeError, RuntimeError):
            self.canvas.extentsChanged.disconnect(self.schedule)


__all__ = ["ViewportRasterRangeController", "raster_window_range"]
