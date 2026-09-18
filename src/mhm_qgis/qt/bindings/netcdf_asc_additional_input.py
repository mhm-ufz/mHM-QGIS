"""Signal wiring for ``netcdf_asc_additional_input.ui``."""


def bind(dialog):
    dialog.comboBox_varName.currentTextChanged.connect(dialog.refresh_crs)


__all__ = ["bind"]
