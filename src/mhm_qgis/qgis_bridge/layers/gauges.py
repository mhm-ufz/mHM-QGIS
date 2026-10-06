"""Read gauge points from QGIS layers for the core raster command."""
from __future__ import annotations

from pathlib import Path

from ...core.handlers.state.domain_state import gauged_outlet_ids, load_state
from ...core.morphology.hydrology.outlets import (
    StationIdError,
    configured_domain_state,
    find_outlet_id_field,
    station_id_int,
    station_id_text,
)
from .crs import crs_of, transform_between
from .loader import open_layer


def gauge_coordinates(
    project_folder,
    snapped_points_path,
    filled_dem_path,
    *,
    preferred_field: str | None = None,
) -> tuple[tuple[int, float, float], ...]:
    """Return ``(station_id, x, y)`` values in the filled DEM's CRS.

    A location explicitly saved by Domain Delineator wins over the snapped
    feature geometry.  When no domain configuration exists, all snapped
    features are retained for compatibility with older projects.
    """
    snapped_layer = open_layer(
        snapped_points_path,
        Path(snapped_points_path).stem,
        is_raster=False,
    )
    if snapped_layer is None:
        raise StationIdError(
            f"Could not read snapped pour points: {snapped_points_path}"
        )

    configured = configured_domain_state(project_folder)
    state = configured if configured is not None else load_state(project_folder)
    configured_ids = gauged_outlet_ids(state) if configured is not None else None
    requested_field = preferred_field or state.get("outlet_id_field") or None
    station_field = find_outlet_id_field(snapped_layer, requested_field)

    allowed_ids = (
        {station_id_text(value) for value in configured_ids}
        if configured_ids is not None
        else None
    )
    saved_points = {
        station_id_text(outlet_id): record.get("gauge_point")
        for outlet_id, record in state.get("outlets", {}).items()
        if isinstance(record, dict) and isinstance(record.get("gauge_point"), dict)
    }

    target_crs = crs_of(filled_dem_path)
    layer_crs = snapped_layer.crs()
    layer_transform = transform_between(layer_crs, target_crs)
    seen_ids: set[str] = set()
    result: list[tuple[int, float, float]] = []

    for feature in snapped_layer.getFeatures():
        raw_id = feature.attribute(station_field)
        station_text = station_id_text(raw_id)
        if not station_text:
            raise StationIdError(
                f"A snapped pour point has an empty {station_field} value."
            )
        if station_text in seen_ids:
            raise StationIdError(
                f"Duplicate outlet ID '{station_text}' found in snapped pour points."
            )
        seen_ids.add(station_text)

        if allowed_ids is not None and station_text not in allowed_ids:
            continue

        point, source_crs = _gauge_point(
            feature,
            saved_points.get(station_text),
            layer_crs,
            station_text,
        )
        transform = (
            transform_between(source_crs, target_crs)
            if source_crs is not None and source_crs.isValid()
            else layer_transform
        )
        if transform is not None:
            point = transform.transform(point)
        result.append(
            (station_id_int(raw_id), float(point.x()), float(point.y()))
        )

    missing_ids = allowed_ids - seen_ids if allowed_ids is not None else set()
    if missing_ids:
        raise StationIdError(
            "Configured gauged outlet IDs are missing from the snapped pour points: "
            + ", ".join(sorted(missing_ids))
            + "."
        )
    if not result:
        message = (
            "No outlets are marked as gauged in Domain Delineator."
            if configured_ids is not None
            else "The snapped pour points do not contain any gauges."
        )
        raise StationIdError(message)
    return tuple(result)


def _gauge_point(feature, saved_point, layer_crs, station_text):
    """Return one point and the CRS in which its coordinates are expressed."""
    if isinstance(saved_point, dict):
        try:
            from qgis.core import QgsCoordinateReferenceSystem, QgsPointXY

            point = QgsPointXY(
                float(saved_point["x"]),
                float(saved_point["y"]),
            )
            source_crs = QgsCoordinateReferenceSystem(
                str(saved_point.get("crs", "") or "")
            )
            return point, source_crs
        except (KeyError, TypeError, ValueError) as error:
            raise StationIdError(
                f"Saved gauge location for outlet '{station_text}' is invalid."
            ) from error

    geometry = feature.geometry()
    if geometry is None or geometry.isEmpty():
        raise StationIdError(
            f"Outlet ID '{station_text}' has an empty geometry."
        )
    return geometry.asPoint(), layer_crs


__all__ = ["gauge_coordinates"]
