"""What the plugin's workflow buttons actually run.

One subpackage per workflow: `morpho` for the ▶ of A. Morphology in the project tree,
`meteo` for the ▶ of B. Meteorology. These sequence the steps and
report progress; the computation itself lives in `core/morphology` and
`core/meteorology`, and anything the user must be told travels back through a
`report=` callback rather than a dialog these modules import.
"""
