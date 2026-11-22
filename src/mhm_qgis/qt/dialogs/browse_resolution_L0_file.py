"""The L0 reference raster, optionally upscaled by a whole factor."""
from qgis.PyQt.QtWidgets import QDialog

from ...core.grid import format_resolution
from ..bindings.browse_resolution_L0_file import bind
from ..ui import load_form
from .browse_resolution_L2_L1_L11_file import _ReferenceFile


class L0FileDialog(_ReferenceFile, QDialog, load_form("browse_resolution_L0_file.ui")):
    def __init__(self, level, domain_crs, initial=None, parent=None):
        super().__init__(parent)
        initial = initial or {}
        self._start(level, domain_crs, initial, bind)
        factor = int(initial.get("factor") or 1)
        self.groupBox_upscaleResolution.setChecked(factor > 1)
        self.spinBox_upscaleResolutionFactor.setValue(factor)
        self.show_l0()

    def factor(self):
        return self.spinBox_upscaleResolutionFactor.value() if self.groupBox_upscaleResolution.isChecked() else 1

    def show_grid(self, *_args):
        super().show_grid()
        header = (self.info or {}).get("header")
        self.spinBox_upscaleResolutionFactor.setRange(1, max(1, min(header["ncols"], header["nrows"])) if header else 1)
        self.show_l0()

    def show_l0(self, *_args):
        """The selected L0: the raster's cell x the upscale factor."""
        info = self.info
        self.label_L0Resolution.setText(format_resolution(info["resolution"] * self.factor(), info["unit"]) if info else "")
        self.label_L0ResolutionUnit.setText(info["unit"] if info else "")

    def selection(self):
        return {**super().selection(), "factor": self.factor()}


__all__ = ["L0FileDialog"]
