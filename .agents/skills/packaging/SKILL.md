---
name: mhm-qgis-packaging
description: "The PyPI wheel, the QGIS plugin ZIP, the private micromamba runtime and its locks, CI and releases. Read before changing packaging, dependencies, the standalone runtime, CI or a release."
---

# Packaging and releases

Part of the mhm-qgis agent instructions; the index is in `AGENTS.md`.

## Packaging Rules

- `.publish/plan.md` is the canonical plan for the PyPI package, QGIS repository ZIP,
  private micromamba QGIS runtime, and standalone/Desktop map-canvas routing. Read it
  before changing publication, runtime bootstrap, entry points, or map-host code.
- `src/mhm_qgis/_version.py` is the single release version source. PyPI reads it
  dynamically; `tools/build_qgis_plugin.py` writes it into the staged QGIS metadata.
- Build PyPI artifacts from the repository root. Build the QGIS upload with
  `python tools/build_qgis_plugin.py`; its ZIP contains only the `mhm_qgis/` plugin
  root (with its `LICENSE`, `COPYING`, `COPYING.LESSER`, `AUTHORS.md`) plus the
  repository `README.md`. The license is LGPLv3+, like mhm-tools: those four files exist
  at the repository root and as identical copies in `src/mhm_qgis/` (a test keeps them
  equal), so the plugin folder zipped by hand carries them too.
- Never bundle mHM executables. v5 comes from the `mhm` package (latest 5.13); the machine-local v6
  path lives in `QSettings` (`mhm_qgis/executables/v6`) or `MHM_QGIS_MHMV6`.
- Keep package discovery recursive. Every package needs an `__init__.py` or setuptools
  drops it from the wheel. This bites every time a new subpackage is added -- a
  directory that exists but has no `__init__.py` is not importable and not shipped.
- Keep the `mhm-tools` dependency floor synchronized between `pyproject.toml` and
  `requirements.txt` when a handler starts using a newer public API.
- Include plugin assets, schemas, templates and project-template files in the PyPI build.
  Every `*.yml` under `src/mhm_qgis/` ships (package-data `**/*.yml`, `MANIFEST.in`
  `recursive-include`; the plugin ZIP allows `.yml`), so a new YAML schema needs no
  packaging change.
- If adding data files under `src/mhm_qgis/`, update `MANIFEST.in` and package-data
  patterns if the extension is new.
- Do not add `qgis` as a PyPI dependency. QGIS is provided by QGIS itself.
- The build backend is pinned to `setuptools>=69.3,<70`. 69.3 is the first version with
  the PEP 625 sdist name PyPI requires (`mhm_qgis-X.tar.gz`). 77+ rejects the current
  `license = {file}` table plus license classifier, so moving past 70 means switching to
  an SPDX `license` string first.
- `.gitlab-ci.yml` runs on every push to `upstream` (`git.ufz.de/mhm/mhm-qgis`), shaped
  like mhm-tools' pipeline: `isort --check`, `ruff check src tests tools`, the plain test
  suite on Python 3.11 and 3.13 with coverage, and `build` (wheel, sdist, `twine check`,
  plugin ZIP). `test-qgis` runs the whole suite inside the private QGIS 3.44 runtime
  through `tools/run_in_runtime.py`; it downloads a conda QGIS, so it runs on a
  schedule, on tags or by hand. The local QGIS suite stays the gate for a change.
- `.github/workflows/release-check.yml` runs on the GitHub `origin` (tags, manual,
  weekly). It builds the artifacts, runs core tests on Python 3.11 and 3.14, and installs
  the private runtime plus a GUI open/close on Linux, Windows and both macOS
  architectures. Publishing stays manual.
- Until mhm-tools 0.3 and nml-tools 0.6 are on PyPI, both pipelines install them from
  pinned commits, one `pip` call each with `SETUPTOOLS_SCM_PRETEND_VERSION` set to the
  version pymhm pins: git checkouts carry no matching tag, and hatch-vcs ignores the
  per-package `SETUPTOOLS_SCM_PRETEND_VERSION_FOR_*` variables (measured: they installed
  as `0.2.4.dev57` and `0.1.dev285`). Move a pin when the local checkout the
  suites ran against moves; delete the block at the PyPI release.
- The private runtime is `src/mhm_qgis_runtime/locks/<platform>.lock`, generated from
  `tools/runtime/environment.yml` with `uvx conda-lock lock -f tools/runtime/environment.yml
  --virtual-package-spec tools/runtime/virtual-packages.yml --kind explicit
  --filename-template 'src/mhm_qgis_runtime/locks/{platform}.lock' -p <platform>`.
  Lock one platform per run: a failing platform makes conda-lock write nothing, and a
  solve needs ~4 GB RAM and ~10 min. Every compiled dependency of mhm-qgis, mhm-tools
  and nml-tools belongs in `environment.yml`; pip may add only the pure/self-contained
  `mhm`, `mhm-tools`, `nml-tools`, `easy_mpl` and `cdo` wheels. Otherwise PyPI wheels
  bring their own GDAL/PROJ into the QGIS process. conda-forge `mhm` and `python-cdo`
  are deliberately absent: the first pins an older libnetcdf than QGIS, the second
  pulls the CDO binary, which has no Windows or current macOS build.
- Runtime subprocesses get `PYTHONNOUSERSITE=1` and no `PYTHONPATH`/`PYTHONHOME`.
  Without that, stale `~/.local/lib/python3.X` packages shadow the locked ones, and pip
  uninstalls user-site packages while upgrading them.
