# -*- coding: utf-8 -*-
"""Widget state for `single_layer_input_with_lookup_config_dialog.ui`.

Two dialogs share this form: `LookupConfigDialog` configures a lookup on its
own, `SingleLayerInputDialog` pairs it with an input layer. Their field
refreshing is kept separate because they read different widgets.
"""
from __future__ import annotations
from ...core.handlers import lookup

from qgis.PyQt import QtWidgets


def _selection():
    """Import lazily: `input_selection` imports this module at load time."""
    from ..dialogs import input_selection

    return input_selection

def _refresh_fields(dialog, _index=None):
    dialog.mapping_field_combo.clear()
    dialog.class_field_combo.clear()
    error_label = getattr(dialog, "error_label", None)
    if error_label is not None:
        error_label.clear()
    path = dialog.lookup_table_combo.currentData()
    if not path:
        dialog._update_ok()
        return
    try:
        fields = lookup.columns(path)
    except Exception as error:
        if error_label is not None:
            error_label.setText(str(error))
        dialog._update_ok()
        return

    dialog.mapping_field_combo.addItems(fields)
    dialog.class_field_combo.addItems(fields)
    for combo, name in (
        (dialog.mapping_field_combo, "mapping_field"),
        (dialog.class_field_combo, "class_field"),
    ):
        preferred = dialog._initial_value(name)
        index = combo.findText(str(preferred)) if preferred else -1
        combo.setCurrentIndex(index)
    dialog._update_ok()


def _update_ok(dialog, _index=None):
    button = dialog.buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Ok)
    if button is not None:
        button.setEnabled(dialog.selected_config() is not None)
