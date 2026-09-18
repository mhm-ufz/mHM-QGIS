# -*- coding: utf-8 -*-
"""Widget state for the input-plus-lookup half of the single-layer form."""
from __future__ import annotations
from ...core.handlers import lookup


def _selection():
    """Import lazily: `input_selection` imports this module at load time."""
    from ..dialogs import input_selection

    return input_selection

def _browse_lookup(dialog, _checked=False):
    _selection()._browse_into_combo(
        dialog,
        dialog.lookup_table_combo,
        "Select lookup table",
        "Lookup tables (*.csv *.txt);;All files (*)",
    )
    dialog._refresh_fields()


def _refresh_fields(dialog, _index=None):
    dialog.mapping_field_combo.clear()
    dialog.class_field_combo.clear()
    path = _selection()._combo_path(dialog.lookup_table_combo)
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
