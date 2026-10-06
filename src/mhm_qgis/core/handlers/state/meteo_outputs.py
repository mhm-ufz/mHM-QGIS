"""Project-local registry updates for meteorology outputs."""
from __future__ import annotations

from pathlib import Path
from typing import Union

from ..store.registry import key_for, register
from . import processing

STATE_FILENAME = processing.STATE_FILENAME
PathInput = Union[str, Path]


class MeteorologyOutputState:
    """Small helper for recording prepared meteorology outputs."""

    def __init__(self, project_folder, log=None) -> None:
        """Take the project folder, not a dialog: `core` must not hold Qt objects."""
        self.project_folder = project_folder
        self.log = log

    def state_path(self) -> Path | None:
        if not self.project_folder:
            return None
        return processing.state_path(self.project_folder)

    #: The only sections this writer owns in the shared processing state.
    OWNED_SECTIONS = ("version", "outputs")

    def load(self) -> dict[str, object]:
        path = self.state_path()
        if not path:
            return {"version": 1, "outputs": {}}
        state = processing.load(self.project_folder)
        state.setdefault("version", 1)
        state.setdefault("outputs", {})
        return state

    def save(self, state: dict[str, object]) -> None:
        """Write back only this writer's sections of the shared state file.

        `cache` and the morphology processing state write their own sections
        into the same file. Dumping the whole in-memory copy erased whatever
        they had added since it was loaded, which silently disabled stage
        reuse -- so only the owned sections are overlaid, atomically.
        """
        if not self.state_path():
            return
        owned = {k: state[k] for k in self.OWNED_SECTIONS if k in state}
        try:
            processing.overlay(self.project_folder, owned)
        except OSError as error:
            self._log(
                f"WARNING: Could not save processing state: {error}")

    def _log(self, message: str) -> None:
        if self.log is not None:
            self.log(message)

    def output_key(self, path: Path) -> str:
        """Return the registry key recorded for one output."""
        return key_for(self.project_folder, path)

    def mark_prepared(self, path: PathInput, name: str | None = None) -> None:
        """Record one prepared forcing file.

        Goes through the store registry so the `outputs` section has a single
        owner; this class keeps only the meteorology-specific label.
        """
        register(
            self.project_folder, path,
            name=name, category="meteorology",
        )
