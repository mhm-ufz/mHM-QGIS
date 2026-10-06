"""The project's own persisted state, one module per store.

Every JSON file the plugin keeps inside a user project is read and written from
here. Qt-free by contract, so the state can be exercised and migrated without a
dialog: the modules take a project folder, not a dialog.

- `processing`       mhm_qgis_processing_state.json (the sole writer)
- `domain_state`     mhm_qgis_domain_delineation_state.json
- `settings`         editable settings.yaml in the outer project folder
- `nml_settings`     nml-settings.json, the handoff to nml-tools
- `cache`            fingerprinted stage reuse, inside the processing state
- `meteo_outputs`    temporary compatibility facade over `processing`
- `morphology`       temporary compatibility facade over `processing`

Feature modules may calculate cache, morphology and meteorology entries, but
all mutations of `mhm_qgis_processing_state.json` go through `processing` so a
read-modify-write operation is atomic within the plugin process.
"""
