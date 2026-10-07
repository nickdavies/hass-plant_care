"""Light: each measured plant against its own target, then each measured plant
in full, then every lamp."""

from __future__ import annotations

from collections.abc import Sequence

from custom_components.lovelace_codegen import (
    ENTITY,
    ICON,
    NAME,
    EntitiesCard,
    HistoryGraphCard,
    Renderable,
    VerticalStackCard,
)

from ..model import LightFixture, LuxFixture, Plant, PlantCareConfig, naming
from .common import Scope, fixture_title, nothing, text_row
from .overview import light_target_graph

NO_LIGHT = "No plant is measured or lit"


def measured(plants: Sequence[Plant]) -> list[Plant]:
    """Plants a lux fixture reads. Every DLI objective is one of them."""
    return [plant for plant in plants if plant.lux is not None]


def lux_fixture(config: PlantCareConfig, plant: Plant) -> LuxFixture | None:
    return config.lux(plant.lux) if plant.lux is not None else None


def is_averaged(config: PlantCareConfig, fixture: LuxFixture) -> bool:
    """Whether the fixture's average exists: it is made by whichever DLI
    objective reads the fixture, so a fixture no objective reads has none."""
    return any(
        plant.dli is not None and plant.lux == fixture.name for plant in config.plants
    )


# ---- One plant ----------------------------------------------------------


def light_rows(config: PlantCareConfig, plant: Plant) -> list[str | dict[str, str]]:
    """What this plant's light looks like, when anything measures it.

    The light level is an average, so the sensors it averages follow it: a
    level that looks wrong is most often one member shaded or gone quiet. The
    lamps the plant sits under are rows here too. When a deficit shows up in
    the feed the first question is always whether the lamp is even on, and the
    answer should not be a scroll away.
    """
    rows: list[str | dict[str, str]] = []

    if plant.dli is not None:
        rows.append(
            {
                ENTITY: naming.dli_today(plant).full,
                NAME: (
                    f"DLI today (want {plant.dli.preferred.low:g}"
                    f"–{plant.dli.preferred.high:g})"
                ),
                ICON: "mdi:white-balance-sunny",
            }
        )

    lux = lux_fixture(config, plant)
    if lux is not None:
        if is_averaged(config, lux):
            rows.append(
                {
                    ENTITY: naming.lux_average(lux).full,
                    NAME: "Light level",
                    ICON: "mdi:brightness-5",
                }
            )
        # No name of our own: the sensor's says where it is, which is the
        # point of listing them.
        rows.extend(
            {ENTITY: member, ICON: "mdi:brightness-6"} for member in lux.entities
        )

    for fixture in config.fixtures_for(plant):
        rows.append(
            {
                ENTITY: fixture.switch_entity,
                NAME: fixture_title(fixture),
                ICON: "mdi:lightbulb",
            }
        )

    return rows


def light_graph(config: PlantCareConfig, plant: Plant) -> HistoryGraphCard | None:
    """Today's DLI, the light level it integrates, and the sensors that level
    averages.

    Two days rather than a week, because the shape being read here is the
    daily one: where accumulation flattens is when the light stopped, and a
    week compresses that into nothing. The units differ, so Home Assistant
    draws them as separate charts in the one card, on the same time axis.
    """
    entities: list[str | dict[str, str]] = []
    if plant.dli is not None:
        entities.append({ENTITY: naming.dli_today(plant).full, NAME: "DLI today"})
    lux = lux_fixture(config, plant)
    if lux is not None:
        if is_averaged(config, lux):
            entities.append({ENTITY: naming.lux_average(lux).full, NAME: "Light level"})
        entities.extend({ENTITY: member} for member in lux.entities)
    if not entities:
        return None
    return HistoryGraphCard(
        title=f"{plant.display} — light", hours_to_show=48, entities=entities
    )


def plant_light_card(config: PlantCareConfig, plant: Plant) -> VerticalStackCard:
    cards: list[Renderable] = [
        EntitiesCard(title=plant.display, entities=light_rows(config, plant))
    ]
    if (graph := light_graph(config, plant)) is not None:
        cards.append(graph)
    return VerticalStackCard(cards=cards)


# ---- One lamp -----------------------------------------------------------


def fixture_card(fixture: LightFixture) -> EntitiesCard:
    """One card per grow light.

    The killswitch sits next to the on-time sensor deliberately: freezing a
    fixture is a decision about the plants under it, and the on-time figure
    is the only thing on the dashboard that will tell you what that decision
    actually did.

    The schedule closes the card, as plain text rows rather than entities:
    it is config, not state, and an entity that only ever repeated the YAML
    would be one more thing to keep in step with it. Next to the on-time
    figure it is what makes that figure readable — 480 minutes is a full
    day under one window and a lamp stuck on under another.
    """
    rows: list[str | dict[str, str]] = [
        {
            ENTITY: fixture.switch_entity,
            NAME: "Lamp",
            ICON: "mdi:lightbulb",
        },
        {
            ENTITY: naming.light_on_minutes(fixture).full,
            NAME: "On time today",
            ICON: "mdi:timer-outline",
        },
        {
            ENTITY: naming.light_killswitch(fixture).full,
            NAME: "Killswitch (freeze, does not turn off)",
            ICON: "mdi:hand-back-left",
        },
    ]
    rows.extend(
        text_row(scheduled.label, scheduled.text, "mdi:clock-outline")
        for scheduled in fixture.window.schedule()
    )
    return EntitiesCard(title=fixture_title(fixture), entities=rows)


def lamps_card(fixtures: Sequence[LightFixture]) -> VerticalStackCard:
    return VerticalStackCard(cards=[fixture_card(fixture) for fixture in fixtures])


# ---- The page -----------------------------------------------------------


def light_card(config: PlantCareConfig, scope: Scope) -> VerticalStackCard:
    cards: list[Renderable] = []
    if (graph := light_target_graph(scope.plants)) is not None:
        cards.append(graph)
    cards.extend(plant_light_card(config, plant) for plant in measured(scope.plants))
    cards.extend(fixture_card(fixture) for fixture in scope.fixtures)
    return VerticalStackCard(cards=cards or [nothing(NO_LIGHT)])
