"""Chores: every care task counting down, then each plant's tasks with the
buttons that mark them done."""

from __future__ import annotations

from collections.abc import Sequence

from custom_components.lovelace_codegen import (
    ENTITY,
    ICON,
    NAME,
    EntitiesCard,
    VerticalStackCard,
)

from ..model import Plant, naming
from .bars import BarGroup, BarsCard
from .common import Scope, nothing
from .overview import care_bar

NO_CHORES = "No plant has a care task"


def tended(plants: Sequence[Plant]) -> list[Plant]:
    return [plant for plant in plants if plant.care]


def care_rows(plant: Plant) -> list[str | dict[str, str]]:
    """Two rows per task: how long since, and the button to say it is done.

    The interval goes in the row name rather than getting its own entity, so
    the card answers "is this overdue" by eye without another entity per
    task existing purely to be compared against a constant.
    """
    rows: list[str | dict[str, str]] = []
    for task in plant.care:
        rows.append(
            {
                ENTITY: naming.care_days_since(plant, task).full,
                NAME: f"{task.display} (every {task.every_days}d)",
                ICON: task.icon,
            }
        )
        rows.append(
            {
                ENTITY: naming.care_done(plant, task).full,
                NAME: f"Mark {task.display.lower()} done",
                ICON: "mdi:check",
            }
        )
    return rows


def chore_bars_card(plants: Sequence[Plant]) -> BarsCard:
    """Every task, a scheduled watering included: here it is a chore like any
    other, where on the overview it stands in for the water bar."""
    return BarsCard(
        [
            BarGroup(
                title=plant.display,
                bars=tuple(care_bar(plant, task) for task in plant.care),
            )
            for plant in plants
        ]
    )


def plant_chores_card(plant: Plant) -> EntitiesCard:
    return EntitiesCard(title=plant.display, entities=care_rows(plant))


def chores_card(scope: Scope) -> VerticalStackCard:
    plants = tended(scope.plants)
    if not plants:
        return VerticalStackCard(cards=[nothing(NO_CHORES)])
    return VerticalStackCard(
        cards=[
            chore_bars_card(plants),
            *(plant_chores_card(plant) for plant in plants),
        ]
    )
