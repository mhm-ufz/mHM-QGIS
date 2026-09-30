"""Signal wiring for ``netcdf_asc_additional_input.ui``."""
from ..controllers import netcdf_asc_additional_input as controller


def bind(dialog):
    dialog.comboBox_varName.currentTextChanged.connect(
        lambda text: controller.refresh_crs(dialog, text)
    )


__all__ = ["bind"]
