"""Read the mHM version selected on the main form."""


def selected(dialog) -> str:
    combo = getattr(dialog, "comboBox_mHMversion", None)
    return (combo.currentText().strip() if combo is not None else "") or "6.0"

