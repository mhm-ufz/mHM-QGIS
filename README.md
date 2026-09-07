# mHM QGIS — QGIS plugin for Mesoscale Hydrological Model

Prepare model inputs and namelists for **mHM 5.13 and v6** in QGIS.

- Delineate domains, snap outlets, and assign discharge observations.
- Prepare DEM derivatives, soil, land cover, geology, and LAI.
- Align morphology and meteorology on compatible model grids.
- Edit namelists with nml-tools and inspect rasters or discharge time series.

This plugin prepares and configures mHM; running simulations requires a
separately installed `mhm` executable.

## Install from source

```bash
git clone https://git.ufz.de/mhm/mhm-qgis
cd mhm-qgis
python3 -m pip install -e .
```

For QGIS, copy or symlink `src/mhm_qgis` into your QGIS profile's
`python/plugins/` directory and enable **mHM QGIS** in the Plugin Manager.
Install Python dependencies into the environment used by QGIS.

## Workflow

Select a project folder and mHM version, configure inputs, prepare morphology
and domains, then run **Meteorology/Morphology Setup**. Review the prepared
layers, edit namelists, and run mHM. Project settings and processing state are
saved for reuse.

The `mhm-qgis` command opens the standalone GUI. It is currently a partial
Qt-backed runtime: interactive delineation and remaining QGIS-backed actions
still require QGIS. See the [standalone strategy](src/mhm_qgis/standalone/STANDALONE.md).

## Development

```bash
python3 -m pip install -e '.[dev]'
python3 -m pytest tests/ -q
```

See [CONTRIBUTING.md](CONTRIBUTING.md) and [CHANGELOG.md](CHANGELOG.md).
