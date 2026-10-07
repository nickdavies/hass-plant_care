"""Water: every probe's raw reading, available water across the calibrated
plants, then each probe in full."""

from __future__ import annotations

from collections.abc import Sequence

from custom_components.lovelace_codegen import (
    ENTITY,
    ICON,
    NAME,
    EntitiesCard,
    HistoryGraphCard,
    MarkdownCard,
    Renderable,
    VerticalStackCard,
)

from ..model import Plant, naming
from .common import Scope, nothing
from .overview import available_water_graph

NO_PROBES = "No plant has a moisture probe"


def probed(plants: Sequence[Plant]) -> list[Plant]:
    return [plant for plant in plants if plant.moisture is not None]


CALIBRATING = """### {display} — calibrating

No watering threshold yet, by design: one would be a number nobody measured.
Water by hand, then read the two endpoints off the graph below —

- **fieldCapacity**: the reading 60 minutes after watering to runoff
- **dryPoint**: the reading when the skewer test says it is time

Put both in `inputs/plants.yaml` and regenerate."""


def calibrating_card(plant: Plant) -> MarkdownCard | None:
    """Why a probed plant has no available water yet, on the dashboard rather
    than only in a config comment nobody reading the dashboard will see."""
    if plant.moisture is None or plant.moisture.is_calibrated:
        return None
    return MarkdownCard(CALIBRATING.format(display=plant.display))


def moisture_rows(plant: Plant) -> list[str | dict[str, str]]:
    moisture = plant.moisture
    if moisture is None:
        return []

    rows: list[str | dict[str, str]] = []
    if moisture.is_calibrated:
        rows.append(
            {
                ENTITY: naming.available_water(plant).full,
                NAME: "Available water",
                ICON: "mdi:water-percent",
            }
        )
    rows.append(
        {
            ENTITY: naming.moisture_smoothed(plant).full,
            NAME: "Moisture (median)",
            ICON: "mdi:water-percent",
        }
    )
    # The raw reading is what the health checks watch and what gets recorded
    # during calibration — the median lags it by design.
    rows.append(
        {
            ENTITY: moisture.moisture_entity.entity_id,
            NAME: "Moisture (raw)",
            ICON: "mdi:water",
        }
    )
    # Not decoration: capacitive readings drift with soil temperature. If
    # moisture oscillates in phase with this daily, that is the pot near a vent
    # or in sun, not water moving.
    if moisture.temperature_entity is not None:
        rows.append(
            {
                ENTITY: moisture.temperature_entity.entity_id,
                NAME: "Soil temperature",
                ICON: "mdi:thermometer",
            }
        )
    if moisture.battery_entity is not None:
        rows.append(
            {
                ENTITY: moisture.battery_entity.entity_id,
                NAME: "Probe battery",
                ICON: "mdi:battery",
            }
        )
    return rows


def moisture_graph(plant: Plant) -> HistoryGraphCard | None:
    """A week, so one full wet/dry cycle fits on screen — the shape is the
    point. Soil temperature shares the card so the temperature-artifact check
    can be made without flipping between graphs."""
    moisture = plant.moisture
    if moisture is None:
        return None
    graph: list[str | dict[str, str]] = [
        {ENTITY: moisture.moisture_entity.entity_id, NAME: "Moisture (raw)"},
        {ENTITY: naming.moisture_smoothed(plant).full, NAME: "Moisture (median)"},
    ]
    if moisture.temperature_entity is not None:
        graph.append({ENTITY: moisture.temperature_entity.entity_id, NAME: "Soil temp"})
    return HistoryGraphCard(
        title=f"{plant.display} — one week", hours_to_show=168, entities=graph
    )


def raw_moisture_graph(plants: Sequence[Plant]) -> HistoryGraphCard | None:
    """Every probe's own reading, calibrated or not.

    Not comparable between pots — each probe sits differently in different
    soil — but it is the one graph every probe is on, and the first place a
    probe that has stopped reporting shows.
    """
    readings = [
        {ENTITY: plant.moisture.moisture_entity.entity_id, NAME: plant.display}
        for plant in plants
        if plant.moisture is not None
    ]
    if not readings:
        return None
    return HistoryGraphCard(
        title="Moisture (raw) — one week", hours_to_show=168, entities=readings
    )


def plant_water_card(plant: Plant) -> VerticalStackCard:
    """One probe in full: how far it is through its calibration, every entity
    it reads or makes, and its week."""
    cards: list[Renderable] = []
    if (calibrating := calibrating_card(plant)) is not None:
        cards.append(calibrating)
    cards.append(EntitiesCard(title=plant.display, entities=moisture_rows(plant)))
    if (graph := moisture_graph(plant)) is not None:
        cards.append(graph)
    return VerticalStackCard(cards=cards)


def water_card(scope: Scope) -> VerticalStackCard:
    plants = probed(scope.plants)
    if not plants:
        return VerticalStackCard(cards=[nothing(NO_PROBES)])
    graphs = (raw_moisture_graph(plants), available_water_graph(plants))
    return VerticalStackCard(
        cards=[
            *(graph for graph in graphs if graph is not None),
            *(plant_water_card(plant) for plant in plants),
        ]
    )
