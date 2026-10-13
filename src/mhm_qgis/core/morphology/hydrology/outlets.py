"""Gauge outlet helpers for the Hydrology page."""
from __future__ import annotations

from dataclasses import dataclass

from ...handlers.state.domain_state import gauged_outlet_ids, load_state, state_path

STATION_ID_FIELD = "STATION_ID"


class StationIdError(ValueError):
    """Raised when a pour-point layer does not provide usable station IDs."""


@dataclass(frozen=True)
class OutletAssignment:
    """One outlet choice, independent of the Qt dialog that collected it."""

    outlet_id: str
    is_gauge: bool
    is_domain: bool = False
    discharge_layer: object | None = None

    @property
    def discharge_source(self) -> str:
        source = getattr(self.discharge_layer, "source", None)
        return str(source() or "") if callable(source) else ""


def selected_outlet_id_field(dialog) -> str | None:
    """Return the outlet ID field selected in the main dialog, if available."""
    selector = getattr(dialog, "selected_outlet_id_field", None)
    if not callable(selector):
        return None
    try:
        return str(selector() or "").strip() or None
    except Exception:
        return None


def configured_domain_state(project_folder):
    """Return saved domain state, or None when no domain setup is configured."""
    if not project_folder or not state_path(project_folder).is_file():
        return None
    state = load_state(project_folder)
    if (
            state.get("outlets")
            or state.get("pour_points_source")
            or state.get("outlet_id_field")
            or state.get("dem_domain")):
        return state
    return None


def configured_gauged_outlet_ids(project_folder) -> list[str] | None:
    """Return configured gauge IDs, using None for the legacy workflow."""
    state = configured_domain_state(project_folder)
    if state is None:
        return None
    return gauged_outlet_ids(state)


def configured_gauge_points(project_folder) -> tuple[tuple[int, float, float, str], ...]:
    """Return saved snapped gauge locations without reopening a QGIS layer."""
    state = configured_domain_state(project_folder)
    if state is None:
        raise StationIdError("Save the Domain Delineator configuration first.")
    points = []
    for outlet_id in gauged_outlet_ids(state):
        record = state["outlets"][outlet_id]
        point = record.get("gauge_point")
        if not isinstance(point, dict):
            raise StationIdError(
                f"Gauged outlet '{outlet_id}' has no saved snapped location."
            )
        try:
            points.append((
                station_id_int(outlet_id),
                float(point["x"]),
                float(point["y"]),
                str(point.get("crs", "") or ""),
            ))
        except (KeyError, TypeError, ValueError) as error:
            raise StationIdError(
                f"Saved gauge location for outlet '{outlet_id}' is invalid."
            ) from error
    if not points:
        raise StationIdError("No outlets are marked as gauged in Domain Delineator.")
    return tuple(points)


def find_outlet_id_field(layer, preferred_field=None) -> str:
    """Return the requested outlet id field, including shapefile truncation."""
    if not layer or not layer.isValid():
        raise StationIdError("Please select a valid pour points layer.")

    field_names = layer.fields().names()
    requested = str(preferred_field or STATION_ID_FIELD).strip()
    if requested in field_names:
        return requested

    for field_name in field_names:
        if field_name.casefold() == requested.casefold():
            return field_name

    truncated = requested[:10].casefold()
    matches = [
        field_name for field_name in field_names
        if field_name.casefold() == truncated
    ]
    if len(matches) == 1:
        return matches[0]

    raise StationIdError(
        f"The pour points layer does not contain the selected outlet ID field "
        f"'{requested}'.")


def find_station_id_field(layer) -> str:
    """Backward-compatible STATION_ID field lookup."""
    return find_outlet_id_field(layer, STATION_ID_FIELD)


def station_id_text(value) -> str:
    """Convert a station id attribute to a stable filename-safe text value."""
    if value is None:
        return ""

    text = str(value).strip()
    if not text or text.upper() == "NULL":
        return ""

    # String fields may deliberately contain leading zeroes used by existing
    # observation filenames.  Preserve that field value verbatim; only
    # normalise values supplied by numeric fields (for example ``7.0``).
    if isinstance(value, str):
        return text

    try:
        numeric = float(text)
        if numeric.is_integer():
            return str(int(numeric))
    except ValueError:  # not a number: keep the text
        pass

    return text


def station_id_int(value) -> int:
    """Convert a station id attribute to the integer value burned in idgauges."""
    text = station_id_text(value)
    if not text:
        raise StationIdError("Empty STATION_ID value found.")

    try:
        numeric = float(text)
        if not numeric.is_integer():
            raise ValueError
        return int(numeric)
    except Exception as e:
        raise StationIdError(
            f"STATION_ID value '{text}' cannot be burned into a raster as an integer."
        ) from e


def outlet_ids_from_layer(
        layer,
        field_name,
        unique: bool = True) -> list[str]:
    """Return values from the selected outlet ID field in feature order."""
    field_name = find_outlet_id_field(layer, field_name)
    station_ids = []
    seen = set()

    for feature in layer.getFeatures():
        station_id = station_id_text(feature.attribute(field_name))
        if not station_id:
            raise StationIdError(
                f"A pour point feature has an empty {field_name} value.")
        if unique:
            if station_id in seen:
                raise StationIdError(
                    f"Duplicate outlet ID '{station_id}' found in {field_name}.")
            seen.add(station_id)
        station_ids.append(station_id)

    return station_ids


def station_ids_from_layer(
        layer,
        unique: bool = True,
        field_name=None) -> list[str]:
    """Return station IDs using STATION_ID unless a field is supplied."""
    return outlet_ids_from_layer(
        layer,
        field_name or STATION_ID_FIELD,
        unique=unique,
    )


def feature_count_text(layer) -> str:
    """Return a display value for the Hydrology page gauged outlet count."""
    if not layer or not layer.isValid():
        return "0"
    try:
        return str(int(layer.featureCount()))
    except Exception:
        return "0"
