from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

pytest.importorskip("qgis.core", reason="QGIS runtime libraries are required")

from mhm_qgis.qt.objects.viewport_raster_range import raster_window_range


def test_visible_raster_range_clips_extent_and_ignores_nodata(tmp_path: Path):
    path = tmp_path / "flow.tif"
    values = np.arange(100, dtype="float32").reshape(10, 10)
    values[0, 0] = -9999
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=10,
        height=10,
        count=1,
        dtype="float32",
        crs="EPSG:4326",
        transform=from_origin(0, 10, 1, 1),
        nodata=-9999,
    ) as output:
        output.write(values, 1)

    minimum, maximum = raster_window_range(
        path, (0, 5, 5, 10), "EPSG:4326"
    )

    assert minimum == 1.0
    assert maximum == 44.0
