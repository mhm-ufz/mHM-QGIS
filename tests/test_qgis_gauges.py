"""Gauge extraction at the QGIS/core boundary."""
from __future__ import annotations

# isort: off
import pytest

pytest.importorskip("qgis.core", reason="QGIS runtime libraries are required")

from qgis.core import QgsCoordinateReferenceSystem, QgsPointXY

from mhm_qgis.core.handlers.state.domain_state import save_state
from mhm_qgis.qgis_bridge.layers import gauges
# isort: on


class _Fields:
    def names(self):
        return ["outlet_id"]


class _Geometry:
    def __init__(self, x, y):
        self._point = QgsPointXY(x, y)

    def isEmpty(self):
        return False

    def asPoint(self):
        return self._point


class _Feature:
    def __init__(self, outlet_id, x, y):
        self._outlet_id = outlet_id
        self._geometry = _Geometry(x, y)

    def attribute(self, _field):
        return self._outlet_id

    def geometry(self):
        return self._geometry


class _Layer:
    def __init__(self, features):
        self._features = features

    def isValid(self):
        return True

    def fields(self):
        return _Fields()

    def crs(self):
        return QgsCoordinateReferenceSystem("EPSG:32632")

    def getFeatures(self):
        return iter(self._features)


class _OffsetTransform:
    def transform(self, point):
        return QgsPointXY(point.x() + 100, point.y() + 200)


def test_saved_gauge_point_wins_and_is_transformed(tmp_path, monkeypatch):
    save_state(
        tmp_path,
        {
            "outlet_id_field": "outlet_id",
            "outlet_order": ["1", "2"],
            "outlets": {
                "1": {"is_gauged": False},
                "2": {
                    "is_gauged": True,
                    "gauge_point": {"x": 20, "y": 30, "crs": "EPSG:4326"},
                },
            },
        },
    )
    layer = _Layer([_Feature("1", 1, 2), _Feature("2", 3, 4)])
    monkeypatch.setattr(gauges, "open_layer", lambda *_args, **_kwargs: layer)
    monkeypatch.setattr(
        gauges,
        "crs_of",
        lambda _path: QgsCoordinateReferenceSystem("EPSG:3857"),
    )
    monkeypatch.setattr(
        gauges,
        "transform_between",
        lambda source, _target: (
            _OffsetTransform() if source.authid() == "EPSG:4326" else None
        ),
    )

    assert gauges.gauge_coordinates(tmp_path, "snapped.shp", "dem.tif") == (
        (2, 120.0, 230.0),
    )


def test_legacy_project_uses_every_snapped_point(tmp_path, monkeypatch):
    layer = _Layer([_Feature("1", 1, 2), _Feature("2", 3, 4)])
    monkeypatch.setattr(gauges, "open_layer", lambda *_args, **_kwargs: layer)
    monkeypatch.setattr(gauges, "crs_of", lambda _path: layer.crs())
    monkeypatch.setattr(gauges, "transform_between", lambda *_args: None)

    assert gauges.gauge_coordinates(
        tmp_path,
        "snapped.shp",
        "dem.tif",
        preferred_field="outlet_id",
    ) == ((1, 1.0, 2.0), (2, 3.0, 4.0))
