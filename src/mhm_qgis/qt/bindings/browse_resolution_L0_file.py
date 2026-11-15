"""Fixed wiring for browse_resolution_L0_file.ui."""
from .browse_resolution_L2_L1_L11_file import bind as bind_file


def bind(dialog):
    bind_file(dialog)
    dialog.groupBox_upscaleResolution.toggled.connect(dialog.show_l0)
    dialog.spinBox_upscaleResolutionFactor.valueChanged.connect(dialog.show_l0)
