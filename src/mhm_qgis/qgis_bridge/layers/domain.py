"""QGIS boundary for domain masks, snapped outlets and gauge observations."""
from __future__ import annotations

import math
import os
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from ...core.grid import local_distance_crs
from ...core.handlers.file.discharge import (
    local_source_path,
    records_from_layer,
    streamflow_filename,
    write_streamflow_observation,
)
from ...core.handlers.file.netcdf.raster import file_path, source
from ...core.handlers.raster.tasks import (
    _read_raster,
    delineate_domains_file,
    merge_domain_masks,
)
from ...core.handlers.state import processing, settings
from ...core.handlers.state.domain_state import (
    DOMAIN_MODE_DEM_EXTENT,
    active_domain_records,
    assign_domain_ids,
    gauged_outlet_ids,
    load_state,
    resolve_output_path,
    save_state,
)
from ...core.handlers.store import registry
from ...core.handlers.store.layout import (
    MERGED_MASK_NAME,
    domain_dem_path,
    morph_folder,
)
from ...core.handlers.store.paths import temp_morphology_folder
from ...core.morphology.hydrology.observation_paths import (
    streamflow_observation_folder,
)
from ...core.morphology.hydrology.outlets import (
    OutletAssignment,
    StationIdError,
    find_outlet_id_field,
    station_id_int,
    station_id_text,
)
from .compat import create_vector_file_writer, qgs_field
from .crs import transform_between
from .loader import open_layer


def _noop(_message: str) -> None:
    pass


def _remove_vector_dataset(path, log: Callable[[str], None] = _noop) -> None:
    """Remove a shapefile and its sidecars."""
    base = os.path.splitext(str(path))[0]
    for extension in (".shp", ".shx", ".dbf", ".prj", ".cpg", ".qpj", ".fix"):
        sidecar = base + extension
        try:
            Path(sidecar).unlink(missing_ok=True)
        except OSError as error:
            log(f"WARNING: Could not remove {sidecar}: {error}")


def snap_points_to_network(  # noqa: PLR0912, PLR0915
    pour_points_layer,
    channel_network_layer,
    output_path,
    *,
    order_field_name="Order",
    max_snap_buffer_distance=1000.0,
    project_crs=None,
    log: Callable[[str], None] = _noop,
) -> str:
    """Snap points to the nearest preferred stream segment and write a layer."""
    from qgis.core import (
        QgsCoordinateReferenceSystem,
        QgsFeature,
        QgsFields,
        QgsGeometry,
        QgsRectangle,
        QgsSpatialIndex,
        QgsWkbTypes,
    )

    radius_m = float(max_snap_buffer_distance)
    if not math.isfinite(radius_m) or radius_m < 0:
        raise ValueError("max_snap_buffer_distance must be finite and non-negative metres.")

    if order_field_name not in channel_network_layer.fields().names():
        raise ValueError(
            f"Order field '{order_field_name}' not found in the channel network."
        )

    output_fields = QgsFields()
    for field in pour_points_layer.fields():
        output_fields.append(field)
    for name, kind in (("snap_state", "String"), ("snap_dist", "Double"),
                       ("snap_order", "Int"), ("confidence", "Int"), ("snap_count", "Int")):
        if name not in output_fields.names():
            output_fields.append(qgs_field(name, kind))
    status_index = output_fields.indexOf("snap_state")
    distance_index = output_fields.indexOf("snap_dist")
    order_index = output_fields.indexOf("snap_order")

    output_crs = project_crs if project_crs is not None else channel_network_layer.crs()
    if not output_crs.isValid():
        raise ValueError("Select a valid project CRS before snapping outlets.")
    if not pour_points_layer.crs().isValid() or not channel_network_layer.crs().isValid():
        raise ValueError("Pour points and channel network must have a valid CRS.")
    point_transform = transform_between(pour_points_layer.crs(), output_crs)
    channel_transform = transform_between(channel_network_layer.crs(), output_crs)
    network_index = QgsSpatialIndex(flags=QgsSpatialIndex.Flag.FlagStoreFeatureGeometries)
    network = {}
    for feature in channel_network_layer.getFeatures():
        geometry = QgsGeometry(feature.geometry())
        if geometry.isEmpty():
            continue
        if channel_transform is not None:
            geometry.transform(channel_transform)
        feature.setGeometry(geometry)
        network[feature.id()] = feature
        network_index.addFeature(feature)

    _remove_vector_dataset(output_path, log)
    writer = create_vector_file_writer(
        str(output_path), output_fields, QgsWkbTypes.Type.Point, output_crs
    )
    if writer.hasError():
        raise RuntimeError(f"Could not create snapped points: {writer.errorMessage()}")

    for point_feature in pour_points_layer.getFeatures():
        original = QgsGeometry(point_feature.geometry())
        if original is None or original.isEmpty():
            log(f"WARNING: Skipping empty pour point feature {point_feature.id()}.")
            continue
        if point_transform is not None:
            original.transform(point_transform)

        point = original.asPoint()
        snapped_geometry = None
        snapped_order = -1
        snap_status = "failed"
        snap_distance = None
        metric_text, metres_per_unit = local_distance_crs(output_crs.toWkt(), point.x(), point.y())
        metric_crs = QgsCoordinateReferenceSystem.fromWkt(metric_text)
        if not metric_crs.isValid():
            raise ValueError("Could not construct the local distance CRS.")
        to_metric = transform_between(output_crs, metric_crs)
        from_metric = transform_between(metric_crs, output_crs)
        metric_point = QgsGeometry(original)
        if to_metric is not None:
            metric_point.transform(to_metric)
        center = metric_point.asPoint()

        # Called within this iteration only; the defaults just make that explicit.
        def nearby(distance_m, center=center, metres_per_unit=metres_per_unit,
                   from_metric=from_metric):
            radius = distance_m / metres_per_unit
            bounds = QgsRectangle(center.x() - radius, center.y() - radius,
                                  center.x() + radius, center.y() + radius)
            if from_metric is not None:
                bounds = from_metric.transformBoundingBox(bounds)
            return network_index.intersects(bounds)

        def measured(feature_id, to_metric=to_metric, metric_point=metric_point,
                     metres_per_unit=metres_per_unit):
            feature = network[feature_id]
            geometry = QgsGeometry(feature.geometry())
            if to_metric is not None:
                geometry.transform(to_metric)
            return geometry.distance(metric_point) * metres_per_unit, geometry, feature

        count = 0
        preferred = None
        for feature_id in nearby(radius_m):
            distance, geometry, feature = measured(feature_id)
            if distance < 0 or distance > radius_m:
                continue
            count += 1
            order = int(feature.attribute(order_field_name))
            rank = (order, -float(distance))
            if preferred is None or rank > preferred[0]:
                preferred = (rank, geometry, distance, order)

        if preferred is not None:
            _rank, geometry, distance, snapped_order = preferred
            closest = geometry.closestSegmentWithContext(center)
            snapped_geometry = QgsGeometry.fromPointXY(closest[1])
            snap_status = "high_order"
            snap_distance = float(distance)
        else:
            nearest_ids = network_index.nearestNeighbor(point, 1)
            if nearest_ids:
                nearest = measured(nearest_ids[0])
                # The planar nearest supplies an upper bound; geographic ranking
                # still uses metres, including alternatives nearer at this latitude.
                distance, geometry, feature = min(
                    (measured(fid) for fid in set(nearby(nearest[0])) | set(nearest_ids)),
                    key=lambda item: item[0],
                )
                closest = geometry.closestSegmentWithContext(center)
                snapped_geometry = QgsGeometry.fromPointXY(closest[1])
                snapped_order = int(feature.attribute(order_field_name))
                snap_status = "closest"
                snap_distance = float(distance)

        if snapped_geometry is not None and from_metric is not None:
            snapped_geometry.transform(from_metric)

        feature = QgsFeature(output_fields)
        attributes = list(point_feature.attributes())
        attributes.extend([None] * (len(output_fields) - len(attributes)))
        feature.setAttributes(attributes[: len(output_fields)])
        feature.setGeometry(snapped_geometry or original)
        feature.setAttribute(status_index, snap_status)
        feature.setAttribute(distance_index, snap_distance)
        feature.setAttribute(order_index, snapped_order)
        feature.setAttribute("confidence", 2 if count == 1 else int(count > 1))
        feature.setAttribute("snap_count", count)
        if not writer.addFeature(feature):
            raise RuntimeError(f"Could not write snapped outlet: {writer.lastError()}")

    del writer
    if not Path(output_path).is_file():
        raise RuntimeError("Could not write the snapped pour-point layer.")
    return str(output_path)


class _DomainMask:
    """Point-in-domain tests straight off a delineation mask raster."""

    def __init__(self, path: str) -> None:
        from qgis.core import QgsCoordinateReferenceSystem

        raster = _read_raster(path)
        self._values = raster["array"]
        self._transform = raster["geotransform"]
        self.crs = QgsCoordinateReferenceSystem(raster.get("projection") or "")

    def any(self) -> bool:
        return bool((self._values != 0).any())

    def contains(self, point) -> bool:
        origin_x, pixel_width, _rx, origin_y, _ry, pixel_height = self._transform
        col = int((point.x() - origin_x) / pixel_width)
        row = int((point.y() - origin_y) / pixel_height)
        rows, cols = self._values.shape
        return 0 <= row < rows and 0 <= col < cols and bool(
            self._values[row, col] != 0
        )


@dataclass(frozen=True)
class _PreparedGauge:
    assignment: OutletAssignment
    gauge_id: int
    input_path: Path
    output_path: Path


class DomainWorkflow:
    """Apply domain and gauge choices through paths and explicit callbacks."""

    def __init__(
        self,
        project_folder,
        pour_points_layer,
        outlet_id_field: str,
        outlet_ids: Iterable[str],
        *,
        prepare: Callable[[], object] | None = None,
        log: Callable[[str], None] | None = None,
        project_crs=None,
    ) -> None:
        self.project_folder = str(project_folder)
        self.pour_points_layer = pour_points_layer
        self.outlet_id_field = str(outlet_id_field)
        self.outlet_ids = [str(value) for value in outlet_ids]
        self.prepare = prepare or (lambda: None)
        self.log = log or _noop
        self.project_crs = project_crs

    @property
    def filled_dem_path(self) -> str:
        return str(Path(temp_morphology_folder(self.project_folder)) / "filled_dem.nc")

    @property
    def flow_accumulation_path(self) -> str:
        return source(Path(temp_morphology_folder(self.project_folder)) / "dem_derivatives.nc", "facc")

    @property
    def channel_network_path(self) -> str:
        return str(Path(temp_morphology_folder(self.project_folder)) / "2_channel_network.shp")

    @property
    def snapped_points_path(self) -> str:
        return str(
            Path(temp_morphology_folder(self.project_folder)) / "2_pour_points_snapped.shp"
        )

    def _require_prepared(self, *paths: str) -> None:
        self.prepare()
        missing = [path for path in paths if not file_path(path).is_file()]
        if missing:
            raise RuntimeError(
                "Domain prerequisites were not prepared: "
                + ", ".join(Path(path).name for path in missing)
            )

    def load_synced_state(
        self,
        definition_mode: str = "",
        dem_domain: bool | None = None,
    ) -> dict:
        """Load state and align it with the current pour-point selection."""
        source = self.layer_source(self.pour_points_layer)
        state = load_state(self.project_folder)
        selection_changed = (
            state.get("pour_points_source")
            and state["pour_points_source"] != source
        ) or (
            state.get("outlet_id_field")
            and state["outlet_id_field"] != self.outlet_id_field
        )

        records = state.setdefault("outlets", {})
        state["outlets"] = {}
        for outlet_id in self.outlet_ids:
            old = records.get(outlet_id, {})
            record = {} if selection_changed else dict(old)
            preview = old.get("delineation") or {}
            if (preview.get("pour_points_source", state["pour_points_source"]) == source
                    and preview.get("outlet_id_field", state["outlet_id_field"]) == self.outlet_id_field):
                if preview:
                    record["delineation"] = preview
            else:
                record.pop("delineation", None)
            state["outlets"][outlet_id] = record
        state["outlet_order"] = list(self.outlet_ids)
        state["pour_points_source"] = source
        state["outlet_id_field"] = self.outlet_id_field
        if definition_mode:
            state["definition_mode"] = definition_mode
        if dem_domain is not None:
            state["dem_domain"] = bool(dem_domain)
        return assign_domain_ids(state)

    def apply_dem_extent(self, assignments: Iterable[OutletAssignment]) -> dict:
        """Create one valid-DEM domain and commit optional gauges."""
        assignments = self._validated_assignment_list(assignments)
        prepared = self.validate_gauge_assignments(assignments)
        previously_gauged = set(gauged_outlet_ids(load_state(self.project_folder)))
        state = self.load_synced_state(DOMAIN_MODE_DEM_EXTENT, True)
        self.apply_assignment_records(state, assignments, prepared)
        for record in state["outlets"].values():
            record["is_domain"] = False
        assign_domain_ids(state)

        self._require_prepared(self.filled_dem_path)
        dem_path = domain_dem_path(self.project_folder, "dem_extent")
        result = delineate_domains_file(
            self.filled_dem_path,
            (),
            dem_domain=(
                int(state["dem_domain_id"]),
                self.dem_mask_path(),
                dem_path,
            ),
            merged_path=self.merged_mask_path(self.project_folder),
        )
        state["dem_domain_directory"] = morph_folder(self.project_folder)
        state["dem_domain_path"] = dem_path
        registry.register(
            self.project_folder,
            result["merged_path"],
            name="4_watershed_merged",
            loaded=False,
        )
        self.update_gauge_domain_ids(state)
        self.write_gauges(prepared)
        save_state(self.project_folder, state)
        self.remove_deselected_gauges(previously_gauged, state)
        return state

    def validate_gauge_assignments(
        self, assignments: Iterable[OutletAssignment]
    ) -> dict[str, _PreparedGauge]:
        """Validate every selected gauge before writing any output."""
        prepared: dict[str, _PreparedGauge] = {}
        numeric_ids: dict[int, str] = {}
        output_folder = Path(streamflow_observation_folder(self.project_folder))
        for assignment in assignments:
            if not assignment.is_gauge:
                continue
            gauge_id = station_id_int(assignment.outlet_id)
            previous = numeric_ids.get(gauge_id)
            if previous is not None:
                raise StationIdError(
                    f"Outlet IDs '{previous}' and '{assignment.outlet_id}' "
                    f"map to the same numeric gauge ID {gauge_id}."
                )
            numeric_ids[gauge_id] = assignment.outlet_id
            layer = assignment.discharge_layer
            if layer is None:
                raise ValueError(
                    f"Select a discharge table for outlet {assignment.outlet_id}."
                )
            is_valid = getattr(layer, "isValid", None)
            if callable(is_valid) and not is_valid():
                raise ValueError(
                    f"The discharge table for outlet {assignment.outlet_id} is invalid."
                )
            input_path = local_source_path(layer)
            if input_path is None or input_path.suffix.lower() not in {".csv", ".txt"}:
                raise ValueError(
                    "The discharge table for outlet "
                    f"{assignment.outlet_id} must be a local CSV or TXT file."
                )
            if not records_from_layer(layer):
                raise ValueError(
                    f"No valid discharge records were found for outlet {assignment.outlet_id}."
                )
            output_path = output_folder / streamflow_filename(assignment.outlet_id)
            prepared[assignment.outlet_id] = _PreparedGauge(
                assignment, gauge_id, input_path.resolve(), output_path
            )
        return prepared

    def update_gauge_domain_ids(self, state: dict, point_layer=None) -> None:
        """Store IDs of every active domain intersecting each gauge point."""
        from qgis.core import QgsGeometry

        assign_domain_ids(state)
        point_layer = point_layer or self.pour_points_layer
        missing_points = [
            outlet_id
            for outlet_id in gauged_outlet_ids(state)
            if not isinstance(
                state["outlets"].get(outlet_id, {}).get("gauge_point"), dict
            )
        ]
        features = (
            self._features_by_outlet(point_layer, require_exact=False)
            if missing_points
            else {}
        )
        domain_masks = []
        for domain in active_domain_records(state):
            path = self.domain_mask_path(domain)
            if not path or not os.path.exists(path):
                raise ValueError(
                    f"Domain mask is missing for outlet {domain['outlet_id']}."
                )
            mask = _DomainMask(path)
            if not mask.any():
                raise ValueError(
                    f"Domain mask is empty for outlet {domain['outlet_id']}."
                )
            domain_masks.append((int(domain["domain_id"]), mask))

        for outlet_id in gauged_outlet_ids(state):
            point_geometry, source_crs = self._effective_gauge_geometry(
                state["outlets"][outlet_id], features.get(outlet_id), point_layer
            )
            memberships = []
            for domain_id, mask in domain_masks:
                candidate = QgsGeometry(point_geometry)
                transform = transform_between(source_crs, mask.crs)
                if transform is not None:
                    candidate.transform(transform)
                if mask.contains(candidate.asPoint()) and domain_id not in memberships:
                    memberships.append(domain_id)
            if not memberships:
                raise ValueError(
                    f"Gauge outlet {outlet_id} is not inside an active domain."
                )
            state["outlets"][outlet_id]["domain_ids"] = memberships

    def domain_mask_path(self, domain: dict) -> str:
        if domain.get("is_dem_domain"):
            return self.dem_mask_path()
        value = domain.get("mask_path")
        return str(resolve_output_path(self.project_folder, value)) if value else ""

    @staticmethod
    def _effective_gauge_geometry(record, feature, point_layer):
        from qgis.core import QgsCoordinateReferenceSystem, QgsGeometry, QgsPointXY

        point = record.get("gauge_point") if isinstance(record, dict) else None
        if isinstance(point, dict):
            try:
                geometry = QgsGeometry.fromPointXY(
                    QgsPointXY(float(point["x"]), float(point["y"]))
                )
            except (KeyError, TypeError, ValueError):
                geometry = None
            if geometry is not None and not geometry.isEmpty():
                return geometry, QgsCoordinateReferenceSystem(
                    str(point.get("crs", "") or "")
                )
        if feature is None or feature.geometry().isEmpty():
            raise ValueError("A gauged outlet has no point geometry.")
        return QgsGeometry(feature.geometry()), point_layer.crs()

    def regenerate_snapped_points(self) -> str:
        """Recreate snapped points from the current layer and fixed channel path."""
        self._require_prepared(self.channel_network_path)
        channel = open_layer(
            self.channel_network_path, "Channel Network", is_raster=False
        )
        if channel is None:
            raise RuntimeError("The prepared channel network is invalid.")
        result = snap_points_to_network(
            self.pour_points_layer,
            channel,
            self.snapped_points_path,
            project_crs=self.project_crs,
            max_snap_buffer_distance=settings.read(self.project_folder)["max_snap_buffer_distance"],
            log=self.log,
        )
        registry.register(
            self.project_folder,
            result,
            name="2_pour_points_snapped",
            loaded=False,
        )
        return result

    @staticmethod
    def validate_unique_state_gauge_ids(state: dict) -> None:
        seen = {}
        for outlet_id in gauged_outlet_ids(state):
            gauge_id = station_id_int(outlet_id)
            previous = seen.get(gauge_id)
            if previous is not None:
                raise StationIdError(
                    f"Outlet IDs '{previous}' and '{outlet_id}' map to the "
                    f"same numeric gauge ID {gauge_id}."
                )
            seen[gauge_id] = outlet_id
            state["outlets"][outlet_id]["gauge_id"] = gauge_id

    @staticmethod
    def require_active_domain(state: dict) -> None:
        if not active_domain_records(state):
            raise ValueError("Select at least one domain outlet.")

    def merge_active_domains(self, state: dict) -> str | None:
        """Merge active masks, removing the former union when none remain."""
        masks = []
        for domain in active_domain_records(state):
            path = self.domain_mask_path(domain)
            if not path or not os.path.exists(path):
                raise ValueError(
                    f"Delineate and save domain {domain['outlet_id']} first."
                )
            masks.append((int(domain["domain_id"]), path))

        merged_path = self.merged_mask_path(self.project_folder)
        if not masks:
            Path(merged_path).unlink(missing_ok=True)
            processing.remove_entry(
                self.project_folder,
                registry.SECTION,
                registry.key_for(self.project_folder, merged_path),
            )
            return None
        merge_domain_masks(masks, merged_path)
        registry.register(
            self.project_folder,
            merged_path,
            name="4_watershed_merged",
            loaded=False,
        )
        return merged_path

    def domain_output_folder(self) -> str:
        path = Path(temp_morphology_folder(self.project_folder)) / "Watersheds" / "DomainDelineation"
        path.mkdir(parents=True, exist_ok=True)
        return str(path)

    def outlet_mask_path(self, outlet_id, state: dict) -> str:
        ordered = list(state.get("outlet_order", self.outlet_ids))
        try:
            index = ordered.index(str(outlet_id)) + 1
        except ValueError:
            index = len(ordered) + 1
        safe = re.sub(r"[^A-Za-z0-9_-]+", "_", str(outlet_id)).strip("_")
        return str(
            Path(self.domain_output_folder())
            / f"4_watershed_{index}_{safe or 'outlet'}.tif"
        )

    def dem_mask_path(self) -> str:
        return str(Path(self.domain_output_folder()) / "4_watershed_DEM.tif")

    @staticmethod
    def merged_mask_path(project_folder) -> str:
        return str(
            Path(temp_morphology_folder(project_folder)) / "Watersheds" / MERGED_MASK_NAME
        )

    @staticmethod
    def layer_source(layer) -> str:
        source = str(layer.source() or "")
        local_path = source.split("|", 1)[0]
        return str(Path(local_path).resolve()) if os.path.exists(local_path) else source

    def remove_observation_file(self, outlet_id: str) -> None:
        try:
            filename = streamflow_filename(outlet_id)
        except ValueError as error:
            self.log(f"WARNING: Could not remove old discharge output: {error}")
            return
        path = Path(streamflow_observation_folder(self.project_folder)) / filename
        try:
            path.unlink(missing_ok=True)
            processing.remove_entry(
                self.project_folder,
                registry.SECTION,
                registry.key_for(self.project_folder, path),
            )
        except OSError as error:
            self.log(f"WARNING: Could not remove old discharge output: {error}")

    def _validated_assignment_list(
        self, assignments: Iterable[OutletAssignment]
    ) -> list[OutletAssignment]:
        assignments = list(assignments)
        ids = [record.outlet_id for record in assignments]
        if len(ids) != len(set(ids)):
            raise ValueError("Outlet assignments contain duplicate IDs.")
        if ids != self.outlet_ids:
            raise ValueError(
                "Outlet assignments no longer match the selected pour-point layer."
            )
        return assignments

    def apply_assignment_records(
        self,
        state: dict,
        assignments: Iterable[OutletAssignment],
        prepared: dict[str, _PreparedGauge],
    ) -> None:
        for assignment in assignments:
            record = state["outlets"][assignment.outlet_id]
            record["is_gauged"] = bool(assignment.is_gauge)
            record["is_domain"] = bool(assignment.is_domain)
            if not assignment.is_gauge:
                record["discharge_file"] = ""
                record.pop("gauge_id", None)
                record.pop("gauge_filename", None)
                record.pop("gauge_path", None)
                record["domain_ids"] = []
                continue
            gauge = prepared[assignment.outlet_id]
            record.update(
                {
                    "discharge_file": str(gauge.input_path),
                    "gauge_id": gauge.gauge_id,
                    "gauge_filename": gauge.output_path.name,
                    "gauge_path": str(gauge.output_path),
                }
            )

    def write_gauges(self, prepared: dict[str, _PreparedGauge]) -> None:
        for gauge in prepared.values():
            output = write_streamflow_observation(
                gauge.assignment.discharge_layer,
                gauge.assignment.outlet_id,
                gauge.output_path.parent,
            )
            registry.register(
                self.project_folder, output, name=output.name, loaded=False
            )

    def remove_deselected_gauges(self, previous: set[str], state: dict) -> None:
        for outlet_id in previous - set(gauged_outlet_ids(state)):
            self.remove_observation_file(outlet_id)

    def _features_by_outlet(self, layer, *, require_exact: bool) -> dict[str, object]:
        field = find_outlet_id_field(layer, self.outlet_id_field)
        features = {}
        for feature in layer.getFeatures():
            outlet_id = station_id_text(feature.attribute(field))
            if not outlet_id:
                raise StationIdError(
                    f"A pour point has an empty {self.outlet_id_field} value."
                )
            if outlet_id in features:
                raise StationIdError(
                    f"Duplicate outlet ID '{outlet_id}' found in snapped pour points."
                )
            features[outlet_id] = feature
        missing = set(self.outlet_ids) - set(features)
        extra = set(features) - set(self.outlet_ids)
        if missing or (require_exact and extra):
            details = []
            if missing:
                details.append(f"missing: {', '.join(sorted(missing))}")
            if require_exact and extra:
                details.append(f"unexpected: {', '.join(sorted(extra))}")
            raise StationIdError(
                "Snapped pour-point IDs do not match the current layer ("
                + "; ".join(details)
                + "). Recreate snapped points."
            )
        return features


__all__ = ["DomainWorkflow", "snap_points_to_network"]
