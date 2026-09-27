"""The `custom:plant-care-bars` card, typed for the dashboard code.

The card is this component's own: `bars-card.js` beside this file, served at
setup. `BarsCard` is a lovelace_codegen `Renderable`, so it nests in stacks,
views and fragments like any of that library's cards.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from custom_components.lovelace_codegen import DBT, ENTITY, ICON, NAME, Renderable

CARD_JS = Path(__file__).parent / "bars-card.js"
CARD_TYPE = "custom:plant-care-bars"

# Home Assistant's theme palette, so the colours follow the theme.
ORANGE = "var(--orange-color)"
GREEN = "var(--green-color)"
BLUE = "var(--blue-color)"


@dataclass(frozen=True)
class Stop:
    """From `start` along the bar, it is drawn in `color`, until the next stop."""

    start: float
    color: str


@dataclass(frozen=True)
class FromAttribute:
    """A ceiling read from an attribute of the bar's own entity, for one that
    changes from day to day."""

    attribute: str


@dataclass(frozen=True)
class Bar:
    """One entity's state as a bar filling towards `ceiling`.

    A `countdown` reads the state as time elapsed and shows what is left of
    `ceiling`, draining as it runs out. Stops apply along the drawn bar either
    way.
    """

    entity: str
    name: str
    icon: str
    ceiling: float | FromAttribute
    stops: tuple[Stop, ...] = ()
    countdown: bool = False

    def render(self) -> DBT:
        config: dict[str, Any] = {ENTITY: self.entity, NAME: self.name, ICON: self.icon}
        if isinstance(self.ceiling, FromAttribute):
            config["max_attribute"] = self.ceiling.attribute
        else:
            config["max"] = self.ceiling
        if self.stops:
            config["stops"] = [
                {"from": stop.start, "color": stop.color} for stop in self.stops
            ]
        if self.countdown:
            config["countdown"] = True
        return config


@dataclass(frozen=True)
class BarGroup:
    title: str
    bars: tuple[Bar, ...]


class BarsCard(Renderable):
    """Titled groups of bars, laid out as many across as the card is wide."""

    def __init__(self, groups: Sequence[BarGroup]) -> None:
        self.groups = groups

    def render(self) -> DBT:
        return {
            "type": CARD_TYPE,
            "groups": [
                {"title": group.title, "bars": [bar.render() for bar in group.bars]}
                for group in self.groups
            ],
        }
