# AGENTS.md

Guidance for coding agents working on `mhm_qgis`: a plugin targeting QGIS 3/Qt5 and QGIS 4/Qt6
that prepares mHM inputs and namelists. The GUI requires real QGIS libraries,
whether hosted by QGIS Desktop or launched directly.

## Working Style

- **Invoke the `ponytail` skill on every coding task here** -- writing, fixing,
  refactoring, reviewing or choosing a dependency. Shortest working diff,
  deletion over addition, stdlib before a new helper, and no abstraction with
  one caller. It is the house style, not an option.
- Keep code and schema comments to what is not obvious from the names. The
  reasoning belongs in this file, where it is read once rather than in every
  diff.

## Repository Shape

- `src/mhm_qgis/`: the QGIS plugin folder **and** the Python package. Both at once --
  QGIS loads this directory directly, and it is what the wheel ships. It holds no
  standalone code: the plugin ZIP is this folder as it is.
- `src/mhm_qgis_runtime/`: the standalone app, only in the PyPI wheel (never in the
  plugin ZIP; it imports `mhm_qgis`, never the reverse). `mhm-qgis` and
  `mhm-qgis-runtime` console entry points. With an importable QGIS it runs `app.main`
  (`python -m mhm_qgis_runtime.app`: a real `QgsApplication` without Desktop) there;
  otherwise it installs a private micromamba QGIS runtime (`envs/mhm-qgis-<version>`)
  and relaunches inside it. The runtime gets `mhm-qgis[full]` with this install's
  mhm-qgis and pure-Python dependencies repacked as wheels (`_local_wheels`), so a
  TestPyPI, local-wheel, editable or unreleased-dependency install needs no index for
  them. `pip install mhm-qgis` installs the launcher only (no dependencies); the app's
  packages are the `full` extra, and the plugin reads `requirements.txt` instead.
- `src/mhm_qgis/metadata.txt`, `__init__.py`, `mhm_qgis.py`: QGIS plugin entry points.
- `src/mhm_qgis/nml-schemas/`: versioned by mHM version, currently `v5.13` and `v6`.
- `pyproject.toml`, `MANIFEST.in`: PyPI packaging metadata.
- `tests/`: core checks run in plain Python; GUI checks require real QGIS and
  are skipped when unavailable. `conftest.py` initializes an offscreen QGIS application.
  Checks that reach GDAL (`osgeo`, which comes with QGIS or the system, not PyPI) call
  `pytest.importorskip("osgeo")`, so CI's pip-only environment skips them instead of
  failing; a new test that reaches GDAL needs the same line.

A local QGIS install typically symlinks the plugin at
`/usr/share/qgis/python/plugins/mhm_qgis -> src/mhm_qgis`, which is why a real-QGIS
smoke test exercises the working tree rather than a copy. Check before relying on it.

## Topic Guides

The rest of the instructions are topic guides in `.agents/skills/<topic>/SKILL.md`. They are as
binding as this file: read the matching guide before working in its area.

| Guide | Read before |
|---|---|
| [`architecture`](.agents/skills/architecture/SKILL.md): Package and module layout, the Qt layering rules (controllers, workflows, dialogs, forms) and import/reload pitfalls. | adding, moving or wiring modules and UI code |
| [`qgis-runtime`](.agents/skills/qgis-runtime/SKILL.md): How the plugin uses QGIS (Qt5/Qt6, Processing), mhm-tools through its adapter, and background tasks. | touching QGIS APIs, mhm-tools calls or long-running jobs |
| [`project-state`](.agents/skills/project-state/SKILL.md): Setup state files, domain delineation settings and the fixed output paths. | reading or writing setup state, settings or output paths |
| [`morphology`](.agents/skills/morphology/SKILL.md): The Configuration tab and input preparation: grids and the common L0/L2 extent, the v6 input layout, staging and publication, fingerprinted reuse, categorical inputs and LAI. | changing configuration, grids or any morphology/LAI preparation step |
| [`display`](.agents/skills/display/SKILL.md): Showing layers and results in QGIS Desktop or the standalone map window, and the post-processing views. | changing map display, the map window or the Outputs tab |
| [`namelists`](.agents/skills/namelists/SKILL.md): nml-tools schemas, the namelist editor hand-over, registered values and v5.13/v6 differences. | touching namelists, schemas or the namelist editor |
| [`packaging`](.agents/skills/packaging/SKILL.md): The PyPI wheel, the QGIS plugin ZIP, the private micromamba runtime and its locks, CI and releases. | changing packaging, dependencies, the standalone runtime, CI or a release |

## Current Conventions

- The selected mHM version comes from `comboBox_mHMversion`.
- File-profile filenames come from the versioned schema configuration. v5.13 uses
  `mhm_parameter.nml`; the current v6 profiles use `parameters.nml` and `outputs.nml`.
- v5.13 schemas live under `nml-schemas/v5.13`, v6 under `nml-schemas/v6`. There is no
  `nml-templates/` directory; nml-tools owns rendering.
- v5.13 multi-domain template values should render as one-shot arrays where possible,
  e.g. `resolution_Hydrology = 12000, 24000`, not repeated `resolution_Hydrology(1)` /
  `(2)` lines.
- UI-facing logs go through `self.log_message(...)`.
- No success popups for routine generated classdefinition files; log silently unless an
  error needs user action.

## Verifying A Change

- `python3 -m pytest tests/ -q` runs core checks in plain Python and skips GUI
  checks without QGIS. Run it in QGIS Python for the complete suite. Run collection -- know
  it before you start, so a regression is unmistakable.
- `python3 -m compileall -q src/mhm_qgis` catches misplaced imports, broken indentation
  and `__future__` placement. `ast.parse` does **not** enforce `__future__` placement;
  `compile()` does.
- After moving any module, statically resolve every intra-package import against the real
  module tree rather than trusting the test suite -- function-local imports and
  module-level imports in modules no test touches will otherwise pass silently.
- When a file format is a contract (a manifest, a namelist, an ASCII header, geology
  metadata), generate it before and after the change from identical inputs and `cmp` the
  bytes.
- For real-QGIS checks, prepend `/usr/share/qgis/python` and
  `/usr/share/qgis/python/plugins` to `sys.path` **after `src`**:
  `PYTHONPATH=src:/usr/share/qgis/python:/usr/share/qgis/python/plugins`. The plugins
  folder holds the `mhm_qgis` symlink to the main checkout, so with it first a worktree
  or scratch copy silently tests the main checkout's code. Then call `QgsApplication.setPrefixPath` and
  `initQgis()`. Set `locale/userLocale` in `QSettings` **after** `initQgis()` or
  `classFactory` fails on a headless machine. Useful assertions: the main dialog
  constructs with 2 input adapters (DEM, pour points) and 6 tabs.
- `PyYAML` is a declared dependency (`pyproject.toml`, both `requirements.txt`), so
  `dependency_bootstrap` checks it before the dialog opens; the plugin's own schemas
  may use `yaml.safe_load`.
- Do not claim full runtime verification unless it was actually tested inside QGIS.
- A test that opens a pyqtgraph window must `deleteLater()` its dialog and flush
  `DeferredDelete` before returning, while it still holds the plot. Left to the cycle
  collector, pyqtgraph's C++ items can outlive their Python wrappers and segfault the
  session teardown in `conftest.pytest_sessionfinish` (exit 139 after every test passed;
  `test_discharge_graph.py` did this in about one run in three).

## Development Workflow

- Use `rg` / `rg --files` for search.
- The worktree may already contain user changes. Do not revert unrelated modifications.
- Many modules import QGIS/PyQt and cannot be fully imported in plain system Python.
- Keep changes narrow and aligned with existing module boundaries.

## Style

- Lint: `isort .` then `ruff check src tests tools` (config in `pyproject.toml`). isort
  is the only import sorter, so ruff's `I` rule is off. `D`, `EM`, `PTH` and `ARG` are a
  deferred ratchet: don't add new violations of them. `RUF046` stays off because NumPy 1.x
  `round()` of a float32 returns float32, so `int(round(x))` is deliberate. A `noqa`
  carries its reason.

- Keep UI code focused on UI state, validation and logging.
- Keep the `externals/` boundary QGIS-free.
- Prefer clear errors over silent fallbacks when the user is expected to provide
  fields/layers.
- Prefer deleting a duplicate over adding a delegator. Several restructuring passes left
  thin forwarding stubs behind; they were removed as redundancy, and new ones are not
  wanted. A one-line method on a dialog is the documented exception, because other
  packages hold a reference to the dialog by name.

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

When the user types `/graphify`, use the installed graphify skill or instructions before doing anything else.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- Dirty graphify-out/ files are expected after hooks or incremental updates; dirty graph files are not a reason to skip graphify. Only skip graphify if the task is about stale or incorrect graph output, or the user explicitly says not to use it.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
