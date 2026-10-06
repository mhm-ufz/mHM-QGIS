"""Where a project's files are, and whether they are there yet.

The bridge between the Qt layer asking "can I show this?" and the processing
layer asking "can I run on this?". Both used to answer it themselves, four
different ways.

- `paths`    pure path construction; the leaf that `state` imports
- `layout`   which layout version a project uses, and the folders that follow
- `registry` what has actually been produced, persisted through `state`

Docstring-only on purpose: re-exporting the path helpers here would execute
`layout` at package import, and `layout` reads state.
"""
