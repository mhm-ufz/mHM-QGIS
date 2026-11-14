"""Fixed wiring for the reference-file forms (the L0 form adds its factor, see its module)."""


def bind(dialog):
    # pushButton_browseRasterInput → dialog.browse
    dialog.pushButton_browseRasterInput.clicked.connect(lambda _checked=False: dialog.browse())
    dialog.comboBox_rasterInput.currentTextChanged.connect(dialog.read)
    dialog.comboBox_varName.currentTextChanged.connect(dialog.variable_changed)
    dialog.mProjectionSelectionWidget_crsFileInput.crsChanged.connect(dialog.show_grid)
