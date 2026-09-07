"""Public execution plan for morphology preparation."""
from __future__ import annotations

from dataclasses import dataclass, field

from . import commands


@dataclass(frozen=True)
class Stage:
    """One ordered morphology command and its scheduler options."""

    command: str
    label: str = ""
    args: tuple = ()
    options: dict = field(default_factory=dict)
    condition: str = ""

    @property
    def starter(self) -> str:
        """Temporary spelling retained while Qt callers migrate."""
        return self.command


EXECUTE_ALL_STAGES = (
    Stage("dem_derivatives", "DEM derivatives", options={"key": "execute-all-dem"}),
    Stage(
        "categorical",
        "Land cover",
        args=("lc",),
        options={"key": "execute-all-lc", "reuse_existing": True},
    ),
    Stage(
        "categorical",
        "Soil",
        args=("soil",),
        options={"key": "execute-all-soil", "reuse_existing": True},
    ),
    Stage(
        "categorical",
        "Geology",
        args=("geology",),
        options={"key": "execute-all-geology"},
    ),
    Stage("lai", "LAI"),
    Stage(
        "hydrology",
        "Hydrology",
        options={
            "key": "execute-all-hydrology",
            "load": "",
            "accumulation": False,
            "direction": False,
        },
    ),
    Stage("snap_points", "Snap pour points", condition="pour_points"),
    Stage("gauge_position", "Gauge positions", condition="gauged_outlets"),
)


MORPH_SETUP_STAGES = (
    Stage("crop", "Crop all layers"),
    Stage("mask", "Mask all layers"),
    Stage("latlon", "Create latlon.nc", condition="v5"),
    Stage("write", "Write model inputs"),
    Stage("publish", "Publish model inputs"),
    Stage("link_domains", "Link domain inputs"),
    Stage("domain_dems", "Write domain DEMs"),
)


def run_stages(stages, start, *, on_done, on_fail):
    """Run asynchronous stages sequentially through a supplied scheduler."""
    stages = tuple(stages)

    def advance(index=0):
        if index >= len(stages):
            on_done()
            return
        if not start(stages[index], lambda: advance(index + 1)):
            on_fail("Could not start the next preprocessing task.")

    advance()
    return True


# Imported after the plans are defined: ``setup`` consumes
# ``MORPH_SETUP_STAGES`` as its canonical order.
from . import reset, setup


__all__ = [
    "EXECUTE_ALL_STAGES",
    "MORPH_SETUP_STAGES",
    "Stage",
    "commands",
    "reset",
    "run_stages",
    "setup",
]
