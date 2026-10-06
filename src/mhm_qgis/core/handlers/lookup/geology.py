"""Geology parameter metadata derived from a lookup table."""

from __future__ import annotations

from ..file import json as jsonio
from . import table as lookup

LABEL = "Geology lookup"


def _integer(value, field, row):
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(
            f"Row {row} has invalid value {value!r} for {field!r}."
        ) from error
    if not number.is_integer():
        raise ValueError(f"Row {row} has non-integer value {value!r} for {field!r}.")
    return int(number)


def _boolean(value, field, row):
    text = str(value).strip().lower()
    if text in {"1", "true", "t", "yes", "y"}:
        return 1
    if text in {"0", "false", "f", "no", "n"}:
        return 0
    raise ValueError(f"Row {row} has invalid boolean value {value!r} for {field!r}.")


def write_geology_metadata(lookup_table, class_field, output_file):
    """Write metadata consumed by mhm_qgis's geology parameter configuration."""
    table = lookup.read(lookup_table)
    class_column = lookup.resolve_field(table.columns, class_field, label=LABEL)
    geo_column = lookup.optional_field(
        table.columns, "GEO_CLASS", "GEO_ID", label=LABEL
    ) or class_column
    karst_column = lookup.resolve_field(table.columns, "KARSTIC", label=LABEL)
    parameter_column = lookup.resolve_field(table.columns, "PARAMETER_VALUE", label=LABEL)
    rows = []
    for row_number, (_, row) in enumerate(table.iterrows(), start=2):
        rows.append(
            {
                "geo_param": _integer(row[geo_column], geo_column, row_number),
                "geology_class": _integer(
                    row[class_column], class_column, row_number
                ),
                "karstic": _boolean(row[karst_column], karst_column, row_number),
                "parameter_value": _integer(
                    row[parameter_column], parameter_column, row_number
                ),
            }
        )
    rows.sort(key=lambda row: (row["geo_param"], row["geology_class"]))
    return jsonio.write(
        output_file,
        {
            "version": 1,
            "geology_class_count": len(rows),
            "classes": rows,
        },
    )


__all__ = ["write_geology_metadata"]
