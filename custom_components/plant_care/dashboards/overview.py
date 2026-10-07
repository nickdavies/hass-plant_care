"""The overview: a button per plant, each plant's bars, the outstanding feed,
then the graphs putting the plants side by side.

The buttons and bars answer "how is everything" at a glance; a button opens
the plant's own page, for when a bar looks wrong.
"""

from __future__ import annotations

from collections.abc import Sequence

from custom_components.lovelace_codegen import (
    ENTITY,
    NAME,
    ButtonCard,
    GridCard,
    HistoryGraphCard,
    MarkdownCard,
    Renderable,
    VerticalStackCard,
    navigate,
)

from ..model import DEFAULT_POLICY, CareTask, Plant, PlantCareConfig, naming
from .bars import BLUE, GREEN, ORANGE, Bar, BarGroup, BarsCard, FromAttribute, Stop
from .common import NOTHING_YET, Scope, feed_card, fixture_title, plant_link

PLANT_ICON = "mdi:sprout"

# ---- One button per plant -----------------------------------------------


def plant_button(plant: Plant) -> ButtonCard:
    """Opens the plant's own page: every entity it has, with its graphs."""
    return ButtonCard(
        name=plant.display, icon=PLANT_ICON, tap_action=navigate(plant_link(plant))
    )


def plant_grid(plants: Sequence[Plant]) -> GridCard:
    """Three across, which still fits a plant's name on a phone."""
    return GridCard(cards=[plant_button(plant) for plant in plants], columns=3)


# ---- Bars ---------------------------------------------------------------


def care_bar(plant: Plant, task: CareTask) -> Bar:
    """Days left until the task is due, draining as they run out."""
    return Bar(
        entity=naming.care_days_since(plant, task).full,
        name=task.display,
        icon=task.icon,
        ceiling=task.every_days,
        countdown=True,
    )


def water_bar(plant: Plant) -> Bar | None:
    """The best measure of this plant's water there is.

    Available water on the calibrated scale; failing that the probe's own
    reading, which is not comparable between pots but still shows the plant
    drying; failing that, the countdown to its scheduled watering.

    The calibrated bar turns blue where the refill threshold is, so a bar
    still orange is a plant that needs water.
    """
    moisture = plant.moisture
    if moisture is not None and moisture.is_calibrated:
        return Bar(
            entity=naming.available_water(plant).full,
            name="Water",
            icon="mdi:water-percent",
            ceiling=100,
            stops=(
                Stop(0, ORANGE),
                Stop(DEFAULT_POLICY.refill_available_water_pct, BLUE),
            ),
        )
    if moisture is not None:
        return Bar(
            entity=naming.moisture_smoothed(plant).full,
            name="Moisture",
            icon="mdi:water-percent",
            ceiling=100,
            stops=(Stop(0, BLUE),),
        )
    watering = next((task for task in plant.care if task.stands_in_for_probe), None)
    return care_bar(plant, watering) if watering is not None else None


def light_bars(config: PlantCareConfig, plant: Plant) -> list[Bar]:
    """The best measure of this plant's light there is, or none at all.

    Today's DLI towards the top of the preferred band, orange until the bottom
    of it; failing that, how much of today's window each lamp over it has been
    on for.

    A lux reading alone has nothing to fill towards, and a lux fixture has no
    entity until a DLI objective reads it, so there is no bar for it.
    """
    if plant.dli is not None:
        preferred = plant.dli.preferred
        return [
            Bar(
                entity=naming.dli_today(plant).full,
                name="Light",
                icon="mdi:white-balance-sunny",
                ceiling=preferred.high,
                stops=(Stop(0, ORANGE), Stop(preferred.low, GREEN)),
            )
        ]
    fixtures = config.fixtures_for(plant)
    return [
        Bar(
            entity=naming.light_on_minutes(fixture).full,
            name="Lamp" if len(fixtures) == 1 else fixture_title(fixture),
            icon="mdi:lightbulb",
            ceiling=FromAttribute("day_possible_minutes"),
        )
        for fixture in fixtures
    ]


def care_bars(plant: Plant, water: Bar | None) -> list[Bar]:
    """One countdown per care task, except one already shown as the water bar."""
    bars = [care_bar(plant, task) for task in plant.care]
    return [bar for bar in bars if bar != water]


def plant_bars(config: PlantCareConfig, plant: Plant) -> BarGroup:
    water = water_bar(plant)
    bars = [water] if water is not None else []
    bars.extend(light_bars(config, plant))
    bars.extend(care_bars(plant, water))
    return BarGroup(title=plant.display, bars=tuple(bars))


def bars_card(config: PlantCareConfig, plants: Sequence[Plant]) -> BarsCard:
    return BarsCard([plant_bars(config, plant) for plant in plants])


# ---- Graphs across plants -----------------------------------------------
#
# Each plant on a comparable scale, one graph per scale. Each is `None` when no
# plant has anything to put on it, so the caller decides what an empty one
# looks like: left out of a page, or said in words on a fragment.


def available_water_graph(plants: Sequence[Plant]) -> HistoryGraphCard | None:
    """Only calibrated plants, because only there is 0-100 the same thing for
    every pot.

    A week, so a full wet/dry cycle of each fits on screen.
    """
    watered = [
        {ENTITY: naming.available_water(plant).full, NAME: plant.display}
        for plant in plants
        if plant.moisture is not None and plant.moisture.is_calibrated
    ]
    if not watered:
        return None
    return HistoryGraphCard(
        title="Available water — one week", hours_to_show=168, entities=watered
    )


def light_target_graph(plants: Sequence[Plant]) -> HistoryGraphCard | None:
    """Light as a percentage of each plant's own target, because the raw
    totals differ with what each plant wants.

    Two days, because the shape is the daily one: each climbs from midnight,
    and 100 is where the day's minimum was met.
    """
    lit = [
        {ENTITY: naming.dli_target(plant).full, NAME: plant.display}
        for plant in plants
        if plant.dli is not None
    ]
    if not lit:
        return None
    return HistoryGraphCard(
        title="Light, % of target — two days", hours_to_show=48, entities=lit
    )


def comparison_graphs(plants: Sequence[Plant]) -> list[Renderable]:
    graphs = (available_water_graph(plants), light_target_graph(plants))
    return [graph for graph in graphs if graph is not None]


# ---- The whole overview -------------------------------------------------


def overview_card(config: PlantCareConfig, scope: Scope) -> VerticalStackCard:
    if not scope.plants:
        return VerticalStackCard(
            cards=[MarkdownCard(NOTHING_YET), feed_card(scope.feed)]
        )
    return VerticalStackCard(
        cards=[
            plant_grid(scope.plants),
            bars_card(config, scope.plants),
            feed_card(scope.feed),
            *comparison_graphs(scope.plants),
        ]
    )
