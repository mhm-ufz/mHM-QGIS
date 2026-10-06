"""Arc/Info ASCII grids, as mHM reads and writes them.

`pad` streams a grid onto a larger header row by row -- loading one to pad it
cost 3238 MiB on a 13201 x 6001 soil raster and was OOM-killing QGIS; the
streaming form is 39 MiB. `morphology` adds the mHM-specific header handling
and alignment on top.
"""
from .morphology import pad_l0_file_to_header
from .pad import pad_ascii_grid, read_ascii_header

__all__ = ["pad_ascii_grid", "pad_l0_file_to_header", "read_ascii_header"]
