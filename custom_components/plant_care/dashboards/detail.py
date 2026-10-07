"""One plant in full: every entity it has, then its graphs.

The rows and graphs are the Water, Light and Chores tabs' own, so a plant's
page and those tabs cannot show it differently.
"""

from __future__ import annotations

from custom_components.lovelace_codegen import (
    EntitiesCard,
    MarkdownCard,
    Renderable,
    VerticalStackCard,
    divider,
)

from ..model import Plant, PlantCareConfig
from .chores import care_rows
from .common import NOTHING_YET, Scope
from .light import light_graph, light_rows
from .overview import bars_card
from .water import calibrating_card, moisture_graph, moisture_rows


def plant_detail_card(config: PlantCareConfig, plant: Plant) -> VerticalStackCard:
    cards: list[Renderable] = []
    if (calibrating := calibrating_card(plant)) is not None:
        cards.append(calibrating)

    rows: list[str | dict[str, str]] = []
    for section in (
        moisture_rows(plant),
        light_rows(config, plant),
        care_rows(plant),
    ):
        if not section:
            continue
        if rows:
            rows.append(divider())
        rows.extend(section)

    if rows:
        cards.append(EntitiesCard(title=plant.display, entities=rows))
    else:
        cards.append(
            MarkdownCard(f"### {plant.display}\n\nNo sensors and no care tasks.")
        )

    graphs = (moisture_graph(plant), light_graph(config, plant))
    cards.extend(graph for graph in graphs if graph is not None)
    return VerticalStackCard(cards=cards)


def plant_page_card(config: PlantCareConfig, plant: Plant) -> VerticalStackCard:
    """What a plant's button opens: its bars, for the glance it was opened
    from, then the plant in full."""
    return VerticalStackCard(
        cards=[bars_card(config, [plant]), plant_detail_card(config, plant)]
    )


def debug_card(config: PlantCareConfig, scope: Scope) -> VerticalStackCard:
    """Every plant in full, one after another."""
    return VerticalStackCard(
        cards=[plant_detail_card(config, plant) for plant in scope.plants]
        or [MarkdownCard(NOTHING_YET)]
    )
