"""Widget wiring for ``mhm_ready_morph_input.ui``."""

RASTER_FILTER = "Raster files (*.asc *.nc *.tif *.tiff);;All files (*)"
CLASS_DEFINITION_FILTER = "Class definition (*.txt *.csv);;All files (*)"


def bind_mhm_ready(dialog, title):
    from ..dialogs.input_selection import _browse_into_combo, _browse_raster_into_combo

    dialog.pushButton_browseMorphFile.clicked.connect(
        lambda: _browse_raster_into_combo(
            dialog,
            dialog.comboBox_inputFile,
            f"Select mHM-ready {title}",
            RASTER_FILTER,
        )
    )
    dialog.pushButton_browseclassDefinitionFile.clicked.connect(
        lambda: _browse_into_combo(
            dialog,
            dialog.comboBox_classDefinitionInputFile,
            "Select class definition",
            CLASS_DEFINITION_FILTER,
        )
    )


__all__ = ["bind_mhm_ready"]
