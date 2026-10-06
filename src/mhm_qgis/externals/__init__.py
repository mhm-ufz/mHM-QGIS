"""Handlers for external packages and executables the plugin does not own.

One module per external dependency. Package calls and version-specific mHM
execution go through this boundary, so each integration has one obvious home.

Their imports stay inside the functions: importing ``mhm_tools`` costs roughly
850 ms and pulls in geopandas, pandas, rasterio, scipy and xarray, which must
not be paid when QGIS loads the plugin.
"""
