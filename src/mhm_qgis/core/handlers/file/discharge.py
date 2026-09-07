# -*- coding: utf-8 -*-
"""Convert discharge tables to mHM streamflow observation text files."""
from __future__ import annotations

import csv
import math
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import unquote, urlparse


NODATA_VALUE = -9999.0


@dataclass(frozen=True)
class DischargeRecord:
    """One daily discharge observation."""

    timestamp: datetime
    value: float


def records_from_mhm_file(path: Path) -> list[DischargeRecord]:
    """Read saved mHM observations, preserving nodata and omitted steps as gaps."""
    records = {}
    with Path(path).open(encoding="utf-8-sig") as stream:
        next(stream, "")  # Station description.
        header = [next(stream, "").split() for _ in range(4)]
        if (any(len(row) < 2 for row in header)
                or [row[0].lower() for row in header] != ["nodata", "n", "start", "end"]):
            raise ValueError(f"Not an mHM streamflow observation file: {path}")
        nodata, frequency = float(header[0][1]), int(header[1][1])
        if not 1 <= frequency <= 1440:
            raise ValueError("Measurements per day must be between 1 and 1440.")
        step = timedelta(days=1 / frequency)
        for number, line in enumerate(stream, 6):
            if not line.strip():
                continue
            try:
                year, month, day, hour, minute, value = line.split()
                timestamp = datetime(*map(int, (year, month, day, hour, minute)))
                value = float(value)
            except ValueError as error:
                raise ValueError(f"Invalid observation at {path}:{number}") from error
            records[timestamp] = value if value != nodata and math.isfinite(value) else math.nan
    if not records:
        raise ValueError(f"No observations in {path}")
    result = []
    for timestamp, value in sorted(records.items()):
        if result and timestamp - result[-1].timestamp > step:
            result.append(DischargeRecord(result[-1].timestamp + step, math.nan))
        result.append(DischargeRecord(timestamp, value))
    return result


def write_streamflow_observation(layer, station_id: str, output_folder: Path) -> Path:
    """Write one selected QGIS table layer as an mHM streamflow file."""
    records = records_from_layer(layer)
    if not records:
        raise ValueError(
            f"No valid discharge records were found in layer '{layer.name()}'.")

    output_folder.mkdir(parents=True, exist_ok=True)
    output_file = output_folder / streamflow_filename(station_id)
    start_date = records[0].timestamp
    end_date = records[-1].timestamp

    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{output_file.name}.", suffix=".tmp", dir=output_folder
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(f"{station_id} Gauge {station_id} (daily discharge)\n")
            stream.write("nodata   -9999\n")
            stream.write("n       1       measurements per day [1, 1440]\n")
            stream.write(
                f"start  {start_date.year:04d} {start_date.month:02d} {start_date.day:02d} 00 00   (YYYY MM DD HH MM)\n")
            stream.write(
                f"end    {end_date.year:04d} {end_date.month:02d} {end_date.day:02d} 00 00   (YYYY MM DD HH MM)\n")

            for record in records:
                dt = record.timestamp
                stream.write(
                    f"{dt.year:04d}  {dt.month:02d}  {dt.day:02d}  00  00   {record.value:10.3f}\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, output_file)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)

    return output_file


def streamflow_filename(station_id: str) -> str:
    """Return an observation filename without changing the outlet field value."""
    value = str(station_id or "").strip()
    if (
        not value
        or value in {".", ".."}
        or "\x00" in value
        or "/" in value
        or "\\" in value
    ):
        raise ValueError(f"Outlet ID '{value}' is not safe for a filename.")
    return f"{value}.txt"


def records_from_layer(layer) -> list[DischargeRecord]:
    """Read discharge records from a QGIS table layer or its source file."""
    source_path = local_source_path(layer)
    if source_path and source_path.exists():
        records = records_from_grdc_file(source_path)
        if records:
            return records
        records = records_from_delimited_file(source_path)
        if records:
            return records

    return records_from_qgis_table(layer)


def records_from_grdc_file(path: Path) -> list[DischargeRecord]:
    """Parse a GRDC daily command text file, if the source has GRDC structure."""
    try:
        lines = path.read_text(encoding="latin-1", errors="ignore").splitlines()
    except Exception:
        return []

    data_start_idx = None
    for i, line in enumerate(lines):
        if line.strip().startswith("# DATA"):
            data_start_idx = i + 1
            break

    if data_start_idx is None:
        return []

    records = []
    for line in lines[data_start_idx:]:
        line = line.strip()
        if not line or line.startswith("#"):
            continue

        parts = line.split(";")
        if len(parts) < 3:
            continue

        timestamp = parse_datetime_value(parts[0].strip())
        if timestamp is None:
            continue

        records.append(DischargeRecord(
            timestamp=timestamp,
            value=parse_discharge_value(parts[2].strip()),
        ))

    return sort_and_deduplicate(records)


def records_from_delimited_file(path: Path) -> list[DischargeRecord]:
    """Parse common date/value columns from a CSV or delimited TXT file."""
    if path.suffix.lower() not in {".csv", ".txt"}:
        return []
    try:
        text = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        text = path.read_text(encoding="latin-1", errors="ignore")
    except OSError:
        return []

    try:
        dialect = csv.Sniffer().sniff(text[:8192], delimiters=",;\t|")
        reader = csv.DictReader(text.splitlines(), dialect=dialect)
    except (csv.Error, TypeError):
        return []

    field_names = [str(name) for name in (reader.fieldnames or []) if name]
    date_field = find_field(
        field_names,
        ("date", "datetime", "time", "timestamp", "valid_time", "datum", "yyyymmdd"),
    )
    value_field = find_field(
        field_names,
        ("q", "qobs", "q_day", "discharge", "streamflow", "flow", "value", "runoff"),
    )
    year_field = find_field(field_names, ("year", "yyyy", "yr"))
    month_field = find_field(field_names, ("month", "mm", "mon"))
    day_field = find_field(field_names, ("day", "dd"))
    if value_field is None or (
            date_field is None
            and not (year_field and month_field and day_field)):
        return []

    hour_field = find_field(field_names, ("hour", "hh"))
    minute_field = find_field(field_names, ("minute", "min"))
    records = []
    for row in reader:
        timestamp = (
            parse_datetime_value(row.get(date_field))
            if date_field
            else parse_ymd_fields(
                row.get(year_field),
                row.get(month_field),
                row.get(day_field),
                row.get(hour_field) if hour_field else 0,
                row.get(minute_field) if minute_field else 0,
            )
        )
        if timestamp is not None:
            records.append(DischargeRecord(
                timestamp=timestamp,
                value=parse_discharge_value(row.get(value_field)),
            ))
    return sort_and_deduplicate(records)


def records_from_qgis_table(layer) -> list[DischargeRecord]:
    """Parse common date/value or year/month/day discharge table schemas."""
    field_names = layer.fields().names()
    date_field = find_field(
        field_names,
        ("date", "datetime", "time", "timestamp", "valid_time", "datum", "yyyymmdd"),
    )
    value_field = find_field(
        field_names,
        ("q", "qobs", "q_day", "discharge", "streamflow", "flow", "value", "runoff"),
    )

    year_field = find_field(field_names, ("year", "yyyy", "yr"))
    month_field = find_field(field_names, ("month", "mm", "mon"))
    day_field = find_field(field_names, ("day", "dd"))
    hour_field = find_field(field_names, ("hour", "hh"))
    minute_field = find_field(field_names, ("minute", "min"))

    if value_field is None:
        raise ValueError(
            "Could not find a discharge value field. Expected one of: "
            "Q, Qobs, discharge, streamflow, flow, value, runoff.")

    if date_field is None and not (year_field and month_field and day_field):
        raise ValueError(
            "Could not find a date field or year/month/day fields in the discharge table.")

    records = []
    for feature in layer.getFeatures():
        if date_field:
            timestamp = parse_datetime_value(feature.attribute(date_field))
        else:
            timestamp = parse_ymd_fields(
                feature.attribute(year_field),
                feature.attribute(month_field),
                feature.attribute(day_field),
                feature.attribute(hour_field) if hour_field else 0,
                feature.attribute(minute_field) if minute_field else 0,
            )

        if timestamp is None:
            continue

        records.append(DischargeRecord(
            timestamp=timestamp,
            value=parse_discharge_value(feature.attribute(value_field)),
        ))

    return sort_and_deduplicate(records)


def local_source_path(layer) -> Path | None:
    """Extract a local file path from a QGIS layer source URI."""
    source = (layer.source() or "").split("|")[0].split("?")[0]
    if not source:
        return None

    parsed = urlparse(source)
    if parsed.scheme == "file":
        source = unquote(parsed.path)
        if re.match(r"^/[A-Za-z]:/", source):
            source = source[1:]

    path = Path(source)
    return path if path.exists() else None


def find_field(field_names: list[str], aliases: tuple[str, ...]) -> str | None:
    """Find a field using normalized exact aliases, then contains matching."""
    normalized = {normalize_field_name(name): name for name in field_names}
    for alias in aliases:
        match = normalized.get(normalize_field_name(alias))
        if match:
            return match

    for field_name in field_names:
        normalized_name = normalize_field_name(field_name)
        if any(normalize_field_name(alias) in normalized_name for alias in aliases):
            return field_name

    return None


def normalize_field_name(name: str) -> str:
    """Normalize a field name for loose schema matching."""
    return re.sub(r"[^a-z0-9]", "", str(name).strip().lower())


def parse_datetime_value(value) -> datetime | None:
    """Parse common QGIS, Python, and text date values."""
    if value is None:
        return None
    if hasattr(value, "toPyDateTime"):
        return value.toPyDateTime()
    if hasattr(value, "toPyDate"):
        py_date = value.toPyDate()
        return datetime(py_date.year, py_date.month, py_date.day)
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)

    text = str(value).strip()
    if not text or text.upper() == "NULL":
        return None

    for fmt in (
            "%Y-%m-%d",
            "%Y/%m/%d",
            "%d.%m.%Y",
            "%d/%m/%Y",
            "%Y%m%d",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d;%H:%M",
            "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue

    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None


def parse_ymd_fields(year, month, day, hour=0, minute=0) -> datetime | None:
    """Build a datetime from split year/month/day table fields."""
    try:
        return datetime(
            int(float(str(year).strip())),
            int(float(str(month).strip())),
            int(float(str(day).strip())),
            int(float(str(hour).strip() or 0)),
            int(float(str(minute).strip() or 0)),
        )
    except Exception:
        return None


def parse_discharge_value(value) -> float:
    """Parse a discharge value and normalize missing values to mHM nodata."""
    if value is None:
        return NODATA_VALUE

    text = str(value).strip().replace(",", ".")
    if not text or text.upper() == "NULL":
        return NODATA_VALUE

    try:
        parsed = float(text)
    except Exception:
        return NODATA_VALUE

    if not math.isfinite(parsed) or parsed < 0 or parsed in (-999.0, -9999.0):
        return NODATA_VALUE
    return parsed


def sort_and_deduplicate(records: list[DischargeRecord]) -> list[DischargeRecord]:
    """Sort records by date and keep the last value for duplicated dates."""
    by_day = {}
    for record in records:
        key = datetime(
            record.timestamp.year,
            record.timestamp.month,
            record.timestamp.day,
        )
        by_day[key] = DischargeRecord(timestamp=key, value=record.value)
    return [by_day[key] for key in sorted(by_day)]
