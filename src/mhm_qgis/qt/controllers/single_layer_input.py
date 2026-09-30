# -*- coding: utf-8 -*-
"""Widget state for the input-plus-lookup half of the single-layer form."""
from __future__ import annotations
from ...core.handlers import lookup
from ..dialogs.input_selection import _browse_into_combo, _combo_path


def _browse_lookup(dialog, _checked=False):
    _browse_into_combo(
        dialog,
        dialog.lookup_table_combo,
        "Select lookup table",
        "Lookup tables (*.csv *.txt);;All files (*)",
    )
    _refresh_fields(dialog)


def _refresh_fields(dialog, _index=None):
    dialog.mapping_field_combo.clear()
    dialog.class_field_combo.clear()
    path = _combo_path(dialog.lookup_table_combo)
    if not path:
        return
    try:
        fields = lookup.columns(path)
    except Exception:
        return
    dialog.mapping_field_combo.addItems(fields)
    dialog.class_field_combo.addItems(fields)
    for combo, key in (
        (dialog.mapping_field_combo, "mapping_field"),
        (dialog.class_field_combo, "class_field"),
    ):
        index = combo.findText(str(dialog._initial.get(key, "")))
        combo.setCurrentIndex(index)
