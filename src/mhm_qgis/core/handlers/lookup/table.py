"""Read mHM categorical lookup tables and resolve their columns.

A lookup table arrives either as `.csv`, whose format mHM-tools owns, or as a
delimited `.txt` that pandas has to sniff. Column names in the wild carry a unit
suffix and sometimes a leading `*` marking a column mHM-tools ignores, so no
comparison is ever done on the raw name -- everything goes through
`normalize_key`.

`pandas` and `mhm_tools` are imported inside the functions on purpose: importing
mhm_tools alone costs ~843 ms, which must not land in plugin startup.
"""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory


def normalize_key(value) -> str:
    """Fold a column name: drop a leading `*`, a `[unit]` suffix, case and punctuation."""
    text = str(value).strip().lstrip("*").split("[", 1)[0]
    return "".join(char.lower() for char in text if char.isalnum())


def read(lookup_table):
    """Read CSV lookups with mHM-tools and delimited TXT lookups with pandas."""
    lookup_path = Path(lookup_table)
    if lookup_path.suffix.lower() != ".txt":
        from mhm_tools.common.lookup_handler import read_lookup_table

        return read_lookup_table(lookup_path)

    import pandas as pd

    try:
        table = pd.read_csv(
            lookup_path,
            sep=None,
            engine="python",
            encoding="utf-8-sig",
        )
    except Exception as error:
        raise ValueError(
            f"Could not read lookup table {lookup_path}: {error}"
        ) from error
    if table.empty:
        raise ValueError(f"Lookup table {lookup_path} is empty.")
    return table


def columns(lookup_table) -> list[str]:
    """Return the selectable field names, hiding geometry and starred columns."""
    return visible_columns(read(lookup_table).columns)


def visible_columns(available) -> list[str]:
    """Filter already-read columns down to the ones a user may choose."""
    return [
        str(column)
        for column in available
        if str(column) != "geometry" and not str(column).strip().startswith("*")
    ]


def resolve_field(available, requested, *, label="Lookup"):
    """Return the single non-starred column matching `requested`, or raise."""
    normalized = normalize_key(requested)
    matches = [
        column
        for column in available
        if not str(column).strip().startswith("*")
        and normalize_key(column) == normalized
    ]
    if len(matches) != 1:
        names = ", ".join(str(column) for column in available)
        raise ValueError(
            f"{label} field {requested!r} was not found uniquely. "
            f"Available fields: {names or '<none>'}."
        )
    return matches[0]


def optional_field(available, *names, label="Lookup"):
    """Resolve the first of `names` that is present, or None when none are."""
    for name in names:
        try:
            return resolve_field(available, name, label=label)
        except ValueError:
            pass
    return None


def category_key(value):
    """Normalize a vector/lookup category the way mHM-tools rasterization does."""
    import pandas as pd

    if pd.isna(value):
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        number = float(text)
    except ValueError:
        return text
    return str(int(number)) if number.is_integer() else text


def resolve_vector_mapping_field(input_file, lookup_table, mapping_field):
    """Resolve a vector field by name or unambiguous lookup-value coverage."""
    import geopandas as gpd

    frame = gpd.read_file(input_file, ignore_geometry=True)
    normalized = normalize_key(mapping_field)
    matches = [
        column
        for column in frame.columns
        if normalize_key(column) == normalized
    ]
    if len(matches) == 1:
        return str(matches[0])

    lookup = read(lookup_table)
    lookup_column = next(
        (
            column
            for column in lookup.columns
            if normalize_key(column) == normalized
        ),
        None,
    )
    if lookup_column is None:
        raise ValueError(f"Lookup mapping field {mapping_field!r} was not found.")
    lookup_values = {
        key for key in map(category_key, lookup[lookup_column]) if key is not None
    }
    candidates = []
    for column in frame.columns:
        values = {key for key in map(category_key, frame[column]) if key is not None}
        if values and values.issubset(lookup_values):
            candidates.append(str(column))
    if len(candidates) == 1:
        return candidates[0]
    available = ", ".join(str(column) for column in frame.columns)
    raise ValueError(
        f"Could not identify the vector field corresponding to lookup field "
        f"{mapping_field!r}. Available vector fields: {available or '<none>'}."
    )


@contextmanager
def as_csv(lookup_table, *, dir=None, prefix="mhm_qgis_lookup_"):
    """Yield a path mHM-tools can read, converting a TXT lookup to a temporary CSV.

    mHM-tools only parses CSV, so a delimited TXT lookup is re-emitted into a
    scratch directory that is removed on exit. A CSV lookup is yielded as-is and
    no temporary directory is created.
    """
    path = Path(lookup_table)
    if path.suffix.lower() != ".txt":
        yield path
        return
    with TemporaryDirectory(prefix=prefix, dir=dir) as temporary:
        converted = Path(temporary) / "lookup.csv"
        read(path).to_csv(converted, index=False)
        yield converted


__all__ = [
    "as_csv",
    "category_key",
    "columns",
    "normalize_key",
    "optional_field",
    "read",
    "resolve_field",
    "resolve_vector_mapping_field",
    "visible_columns",
]
