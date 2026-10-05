"""Validated manifests for time-varying land cover and soil horizons."""
from __future__ import annotations

import csv
import math
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable


LAND_USE_HEADER = ("StartDateTime", "EndDateTime", "FilePath")
SOIL_HEADER = (
    "Horizon",
    "Upper Depth",
    "Lower Depth",
    "Clay Layer",
    "Sand Layer",
    "Silt Layer",
    "Bulk Density Layer",
    "Bulk Density Unit",
)
BULK_DENSITY_UNITS = ("g/cm3", "kg/m3", "cg/cm3")
MAX_LAND_USE_PERIODS = 50
MAX_SOIL_HORIZONS = 10


def period_datetime(value) -> datetime:
    """Parse a year or ISO datetime; use timezone-free UTC throughout."""
    if isinstance(value, datetime):
        result = value
    else:
        text = str(value).strip()
        result = datetime.fromisoformat(text + "-01-01" if len(text) == 4 else text)
    if result.tzinfo is not None:
        raise ValueError("Land-use dates must use timezone-free UTC.")
    return result


@dataclass(frozen=True)
class LandUsePeriod:
    start_time: datetime | str | int
    end_time: datetime | str | int
    file_path: Path

    def __post_init__(self):
        """Normalize dates; integer pairs retain old inclusive-year semantics."""
        start, end = period_datetime(self.start_time), period_datetime(self.end_time)
        if isinstance(self.start_time, int) and isinstance(self.end_time, int):
            end = datetime(end.year + 1, 1, 1)
        object.__setattr__(self, "start_time", start)
        object.__setattr__(self, "end_time", end)

    @classmethod
    def from_dict(cls, value):
        """Read datetime state or an older inclusive-year record."""
        if "start_time" in value:
            return cls(value["start_time"], value["end_time"], Path(value["file_path"]))
        return cls(int(value["start_year"]), int(value["end_year"]), Path(value["file_path"]))

    @property
    def start_year(self):
        return self.start_time.year

    @property
    def end_year(self):
        return (self.end_time - timedelta(microseconds=1)).year

    def as_dict(self) -> dict:
        return {
            "start_time": self.start_time.isoformat(),
            "end_time": self.end_time.isoformat(),
            "file_path": str(self.file_path),
        }


@dataclass(frozen=True)
class LandUseInput:
    periods: tuple[LandUsePeriod, ...]
    lookup_table: Path
    mapping_field: str
    class_field: str

    def as_dict(self) -> dict:
        return {
            "periods": [period.as_dict() for period in self.periods],
            "lookup_table": str(self.lookup_table),
            "mapping_field": self.mapping_field,
            "class_field": self.class_field,
        }


@dataclass(frozen=True)
class SoilHorizon:
    horizon: int
    upper_depth: float
    lower_depth: float
    clay_layer: Path
    sand_layer: Path
    silt_layer: Path
    bulk_density_layer: Path

    def as_dict(self) -> dict:
        return {
            "horizon": self.horizon,
            "upper_depth": self.upper_depth,
            "lower_depth": self.lower_depth,
            "clay_layer": str(self.clay_layer),
            "sand_layer": str(self.sand_layer),
            "silt_layer": str(self.silt_layer),
            "bulk_density_layer": str(self.bulk_density_layer),
        }


@dataclass(frozen=True)
class SoilInput:
    horizons: tuple[SoilHorizon, ...]
    bulk_density_unit: str
    composition_step: float = 5.0
    bulk_density_step: float = 0.1

    @property
    def lower_depths(self) -> tuple[float, ...]:
        return tuple(horizon.lower_depth for horizon in self.horizons)

    def as_dict(self) -> dict:
        return {
            "bulk_density_unit": self.bulk_density_unit,
            "composition_step": self.composition_step,
            "bulk_density_step": self.bulk_density_step,
            "lower_depths": list(self.lower_depths),
            "horizons": [horizon.as_dict() for horizon in self.horizons],
        }


def validate_land_use_input(value: LandUseInput, *, simulation_start=None, simulation_end=None, version=None) -> None:
    """Validate paths and continuous, non-overlapping calendar periods."""
    if not value.periods:
        raise ValueError("Add at least one land-use layer.")
    if len(value.periods) > MAX_LAND_USE_PERIODS:
        raise ValueError(f"Land use supports at most {MAX_LAND_USE_PERIODS} periods.")
    previous_end = None
    for number, period in enumerate(value.periods, 1):
        if period.end_time <= period.start_time:
            raise ValueError(f"Land-use layer {number} ends before it starts.")
        if previous_end is not None and period.start_time != previous_end:
            raise ValueError("Land-use periods must be ordered and gap-free.")
        _require_file(period.file_path, f"land-use layer {number}")
        previous_end = period.end_time
    if simulation_start is not None and value.periods[0].start_time < period_datetime(simulation_start):
        raise ValueError("Land-use periods start before the simulation.")
    if simulation_end is not None and value.periods[-1].end_time > period_datetime(simulation_end):
        raise ValueError("Land-use periods end after the simulation.")
    _require_file(value.lookup_table, "land-use lookup table")
    if not value.mapping_field.strip() or not value.class_field.strip():
        raise ValueError("Select both the land-use mapping and class fields.")


def validate_soil_input(value: SoilInput) -> None:
    """Validate contiguous soil horizons and their four source layers."""
    if value.bulk_density_unit not in BULK_DENSITY_UNITS:
        raise ValueError("Select the bulk-density unit.")
    for label, step in (("Composition", value.composition_step),
                        ("Bulk density", value.bulk_density_step)):
        if not math.isfinite(step) or step <= 0:
            raise ValueError(f"{label} step must be a positive finite number.")
    if not value.horizons:
        raise ValueError("Add at least one soil horizon.")
    if len(value.horizons) > MAX_SOIL_HORIZONS:
        raise ValueError(f"Soil supports at most {MAX_SOIL_HORIZONS} horizons.")
    previous_lower = None
    for number, horizon in enumerate(value.horizons, 1):
        if horizon.horizon != number:
            raise ValueError("Soil horizons must be numbered consecutively.")
        if horizon.upper_depth < 0 or horizon.lower_depth <= horizon.upper_depth:
            raise ValueError(f"Soil horizon {number} has invalid depth bounds.")
        if number == 1 and abs(horizon.upper_depth) > 1e-9:
            raise ValueError("The first soil horizon must start at depth 0.")
        if (
            previous_lower is not None
            and abs(horizon.upper_depth - previous_lower) > 1e-9
        ):
            raise ValueError("Soil horizons must be contiguous.")
        for label, path in (
            ("clay", horizon.clay_layer),
            ("sand", horizon.sand_layer),
            ("silt", horizon.silt_layer),
            ("bulk-density", horizon.bulk_density_layer),
        ):
            _require_file(path, f"{label} layer for soil horizon {number}")
        previous_lower = horizon.lower_depth


def write_land_use_manifest(value: LandUseInput, output_file) -> Path:
    """Write ``format-data.csv`` for historical land-use processing."""
    validate_land_use_input(value)
    output = Path(output_file)
    rows = (
        (period.start_time.isoformat(), period.end_time.isoformat(), _manifest_path(period.file_path, output))
        for period in value.periods
    )
    return _write_csv(output, LAND_USE_HEADER, rows)


def write_soil_manifest(value: SoilInput, output_file) -> Path:
    """Write soil ``format-data.csv`` for multi-horizon processing.

    mHM-tools reads the manifest with a plain ``read_csv``, so the bulk-density
    unit is repeated as a column on every row rather than carried in a preamble
    line: a preamble would be consumed as the header.
    """
    validate_soil_input(value)
    output = Path(output_file)
    rows = (
        (
            horizon.horizon,
            _number(horizon.upper_depth),
            _number(horizon.lower_depth),
            _manifest_path(horizon.clay_layer, output),
            _manifest_path(horizon.sand_layer, output),
            _manifest_path(horizon.silt_layer, output),
            _manifest_path(horizon.bulk_density_layer, output),
            value.bulk_density_unit,
        )
        for horizon in value.horizons
    )
    return _write_csv(output, SOIL_HEADER, rows)


def _require_file(path: Path, label: str) -> None:
    if not Path(path).is_file():
        raise ValueError(f"Select a readable {label}: {path}")


def _manifest_path(path: Path, manifest: Path) -> str:
    source = Path(path).expanduser().resolve()
    try:
        return source.relative_to(manifest.parent.resolve()).as_posix()
    except ValueError:
        return str(source)


def _number(value: float):
    return int(value) if float(value).is_integer() else value


def _write_csv(
    output: Path,
    header: Iterable[str],
    rows: Iterable[Iterable],
) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            newline="",
            prefix=f".{output.name}.",
            suffix=".tmp",
            dir=output.parent,
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            writer = csv.writer(handle, lineterminator="\n")
            writer.writerow(header)
            writer.writerows(rows)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, output)
    except Exception:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        raise
    return output


__all__ = [
    "BULK_DENSITY_UNITS",
    "MAX_LAND_USE_PERIODS",
    "MAX_SOIL_HORIZONS",
    "LandUseInput",
    "LandUsePeriod",
    "SoilHorizon",
    "SoilInput",
    "validate_land_use_input",
    "validate_soil_input",
    "write_land_use_manifest",
    "write_soil_manifest",
]
