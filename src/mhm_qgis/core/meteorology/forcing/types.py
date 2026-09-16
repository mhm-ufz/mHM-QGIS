# -*- coding: utf-8 -*-
"""Value objects shared by meteorology inspection and preparation."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


MHM_READY = "mHM ready"
AUTO = "auto"
ERA5LAND = "ERA5Land"
_KINDS = {
    "pre": "pre",
    "precipitation": "pre",
    "temp": "tavg",
    "temperature": "tavg",
    **{name: name for name in ("tavg", "tmin", "tmax", "tann", "ssrd", "strd", "eabs", "windspeed")},
    "pet": "pet",
    "q_bkfl": "Q_bkfl",
    **{name: name for name in ("et", "sm", "neutrons", "twsa")},
}


def normalize_kind(kind: str) -> str:
    """Return the canonical meteorology input kind."""
    normalized = _KINDS.get(str(kind).strip().lower())
    if not normalized:
        raise ValueError(f"Unsupported meteorology data type: {kind}")
    return normalized


def normalize_source(source: str) -> str:
    """Normalize UI and legacy spellings of a meteorology data source."""
    compact = str(source).strip().lower().replace("_", "").replace("-", "")
    compact = compact.replace(" ", "")
    if compact == AUTO:
        return AUTO
    if compact == "mhmready":
        return MHM_READY
    if compact == "era5land":
        return ERA5LAND
    raise ValueError(f"Unsupported meteorology data source: {source}")


@dataclass(frozen=True)
class MeteoFolderSpec:
    """One user-selected meteorology input folder."""

    kind: str
    folder: Path | str
    source: str = AUTO
    crs: str | None = None
    pattern: str = "*.nc"
    temporal_resolution: str = "Daily"
    output_temporal_resolution: str = ""
    aggregation: str = ""
    unit: str = ""
    cumulative: bool = False
    cumulative_resolution: str = ""

    def normalized(self) -> "MeteoFolderSpec":
        return MeteoFolderSpec(
            kind=normalize_kind(self.kind),
            folder=Path(self.folder),
            source=normalize_source(self.source),
            crs=self.crs,
            pattern=self.pattern,
            temporal_resolution=self.temporal_resolution,
            output_temporal_resolution=(
                self.output_temporal_resolution or self.temporal_resolution
            ),
            aggregation=(
                self.aggregation
                if (self.output_temporal_resolution or self.temporal_resolution)
                != self.temporal_resolution
                else ""
            ),
            unit=self.unit,
            cumulative=self.cumulative,
            cumulative_resolution=self.cumulative_resolution if self.cumulative else "",
        )


@dataclass(frozen=True)
class SpatialMetadata:
    """Spatial metadata shared by all NetCDF files in an input folder."""

    kind: str
    source: str
    files: tuple[Path, ...]
    shape: tuple[int, int]
    x_coordinate: str
    y_coordinate: str
    x_resolution: float
    y_resolution: float
    resolution: float
    bounds: tuple[float, float, float, float]
    crs: str | None
    unit: str
    start_time: str = ""
    end_time: str = ""


__all__ = [
    "ERA5LAND",
    "MHM_READY",
    "MeteoFolderSpec",
    "SpatialMetadata",
    "normalize_kind",
    "normalize_source",
]
