"""Reading and writing the plugin's JSON, in one place.

Eight persisted files were being read and written by fourteen modules through
four idioms with a dozen small divergences -- four different `except` tuples on
the same read, three atomic writers differing in whether they wrote a trailing
newline or called `fsync`. The behaviour is preserved exactly; only the number
of copies changes.

Two things deliberately stay outside:

- the `job.json` / `result.json` pair is subprocess IPC in a temporary
  directory, written compact with no `indent` or `sort_keys`, and read by
  `native_worker` which runs without QGIS. Use `read_compact`/`write_compact`.
- `cache.fingerprint` serialises with `default=str` to canonicalise a hash
  input, not to persist. Changing that invalidates every cached stage result.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

#: Every persisted file in this project uses these.
INDENT = 2
SORT_KEYS = True
ENCODING = "utf-8"


def read(path, default=None):
    """Return the parsed object, or `default` when it cannot be read.

    Missing, unreadable, mis-encoded and malformed all collapse to `default`:
    a project with a corrupt state file has to keep opening.
    """
    try:
        text = Path(path).read_text(encoding=ENCODING)
    except (OSError, UnicodeError):
        return default
    try:
        return json.loads(text)
    except ValueError:
        return default


def read_mapping(path, **defaults) -> dict:
    """Return a mapping from `path`, applying `defaults` for absent keys."""
    value = read(path)
    if not isinstance(value, dict):
        value = {}
    for key, fallback in defaults.items():
        value.setdefault(key, fallback)
    return value


def write(path, payload, *, newline: bool = False, fsync: bool = False) -> Path:
    """Write `payload` atomically, replacing `path` only once it is complete.

    A half-written state file is worse than a stale one, so the payload lands
    in a sibling temporary file and is renamed over the target.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(descriptor, "w", encoding=ENCODING) as stream:
            json.dump(payload, stream, indent=INDENT, sort_keys=SORT_KEYS)
            if newline:
                stream.write("\n")
            if fsync:
                stream.flush()
                os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return path


def merge_sections(path, sections: dict[str, Any], **write_kwargs) -> Path:
    """Overlay `sections` onto the file, leaving every other key untouched.

    Several writers share one state file. Dumping a whole in-memory copy erases
    what the others added since it was loaded -- that silently disabled stage
    reuse once already, so a shared file is only ever updated section by
    section.
    """
    merged = read_mapping(path)
    merged.update(sections)
    return write(path, merged, **write_kwargs)


def read_compact(path, default=None):
    """Read a transient payload written by `write_compact`."""
    return read(path, default)


def write_compact(path, payload) -> Path:
    """Write a transient payload -- subprocess IPC, not persisted state."""
    path = Path(path)
    path.write_text(json.dumps(payload), encoding=ENCODING)
    return path


__all__ = [
    "merge_sections",
    "read",
    "read_compact",
    "read_mapping",
    "write",
    "write_compact",
]
