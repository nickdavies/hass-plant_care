"""The generated Plants dashboard, and the fragments it is built from.

Built from the plant list, so adding a plant needs no dashboard edit. One
overview tab for everything, then one per person; then every lamp; then every
plant in full, for debugging.

An overview is each plant's water, light and care tasks as progress bars, the
outstanding feed, then graphs putting the plants side by side. The bars answer
"how is everything" at a glance; the full cards with every entity are on the
debug tab, for when a bar looks wrong.

Each card is built by one function, and the ones worth embedding elsewhere are
registered with lovelace_codegen as fragments, so a hand-written dashboard can
show them (`custom:codegen-fragment`, source `plant_care`) and cannot drift from
this one.
"""

from __future__ import annotations

from collections.abc import Sequence
from string import Template

import probatio
from custom_components.lovelace_codegen import (
    DBT,
    ENTITY,
    ICON,
    NAME,
    Dashboard,
    EntitiesCard,
    Fragment,
    GeneratedDashboard,
    HistoryGraphCard,
    MarkdownCard,
    Params,
    Renderable,
    VerticalStackCard,
    View,
    divider,
)

from ..model import (
    DEFAULT_POLICY,
    CareTask,
    Entity,
    LightFixture,
    Plant,
    PlantCareConfig,
    naming,
)
from .bars import BLUE, GREEN, ORANGE, Bar, BarGroup, BarsCard, FromAttribute, Stop

# The list arrives already rendered, in `model/markdown.py`. This card is not
# the only one showing it — the household dashboards show the same feed — and a
# copy of the Jinja on each is one that will not be updated when an item grows a
# field. The count in the heading is the sensor's own state.
# A `Template` rather than `str.format`, because the body is Jinja.
OVERVIEW = Template("""## Needs attention ({{ states('$entity') }})

{{ state_attr('$entity', 'markdown') }}""")


def feed_card(entity: Entity) -> MarkdownCard:
    return MarkdownCard(OVERVIEW.substitute(entity=entity.full))


NOTHING_YET = "### No plants are yours yet"


def text_row(name: str, text: str, icon: str) -> dict[str, str]:
    """A row inside an EntitiesCard showing a fixed string rather than an entity.

    Lives here rather than in `lovelace_codegen` because this is the only
    dashboard that needs it. Move it there the day the second one does.
    """
    return {"type": "text", NAME: name, "text": text, ICON: icon}


def fixture_title(fixture: LightFixture) -> str:
    return f"{fixture.name.replace('_', ' ').title()} lamp"


# ---- The overview: bars -------------------------------------------------


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


# ---- The overview: graphs across plants ---------------------------------


def comparison_graphs(plants: Sequence[Plant]) -> list[Renderable]:
    """Every plant with a comparable scale on one graph per scale.

    Only calibrated plants for water, because only there is 0-100 the same
    thing for every pot. Light as a percentage of each plant's own target,
    because the raw totals differ with what each plant wants.
    """
    graphs: list[Renderable] = []

    watered = [
        {ENTITY: naming.available_water(plant).full, NAME: plant.display}
        for plant in plants
        if plant.moisture is not None and plant.moisture.is_calibrated
    ]
    # A week, so a full wet/dry cycle of each fits on screen.
    if watered:
        graphs.append(
            HistoryGraphCard(
                title="Available water — one week", hours_to_show=168, entities=watered
            )
        )

    lit = [
        {ENTITY: naming.dli_target(plant).full, NAME: plant.display}
        for plant in plants
        if plant.dli is not None
    ]
    # Two days, because the shape is the daily one: each climbs from midnight,
    # and 100 is where the day's minimum was met.
    if lit:
        graphs.append(
            HistoryGraphCard(
                title="Light, % of target — two days", hours_to_show=48, entities=lit
            )
        )

    return graphs


def overview_card(
    config: PlantCareConfig, feed: Entity, plants: Sequence[Plant]
) -> VerticalStackCard:
    if not plants:
        return VerticalStackCard(cards=[MarkdownCard(NOTHING_YET), feed_card(feed)])
    return VerticalStackCard(
        cards=[
            bars_card(config, plants),
            feed_card(feed),
            *comparison_graphs(plants),
        ]
    )


# ---- Lamps --------------------------------------------------------------


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


# ---- Debug: every entity of one plant -----------------------------------


CALIBRATING = """### {display} — calibrating

No watering threshold yet, by design: one would be a number nobody measured.
Water by hand, then read the two endpoints off the graph below —

- **fieldCapacity**: the reading 60 minutes after watering to runoff
- **dryPoint**: the reading when the skewer test says it is time

Put both in `inputs/plants.yaml` and regenerate."""


def _care_rows(plant: Plant) -> list[dict[str, str]]:
    """Two rows per task: how long since, and the button to say it is done.

    The interval goes in the row name rather than getting its own entity, so
    the card answers "is this overdue" by eye without another entity per
    task existing purely to be compared against a constant.
    """
    rows: list[dict[str, str]] = []
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


def _light_rows(config: PlantCareConfig, plant: Plant) -> list[str | dict[str, str]]:
    """What this plant's light looks like, when anything measures it.

    The lamps it sits under are rows here too. When a deficit shows up in
    the feed the first question is always whether the lamp is even on, and
    the answer should not be two dashboards away.
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

    if plant.lux is not None:
        lux = config.lux(plant.lux)
        if lux is not None:
            rows.append(
                {
                    ENTITY: naming.lux_average(lux).full,
                    NAME: "Light level",
                    ICON: "mdi:brightness-5",
                }
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


def plant_detail_card(config: PlantCareConfig, plant: Plant) -> VerticalStackCard:
    cards: list[Renderable] = []
    rows: list[str | dict[str, str]] = []

    moisture = plant.moisture
    if moisture is not None:
        if not moisture.is_calibrated:
            cards.append(MarkdownCard(CALIBRATING.format(display=plant.display)))
        else:
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
        # The raw reading is what the health checks watch and what gets
        # recorded during calibration — the median lags it by design.
        rows.append(
            {
                ENTITY: moisture.moisture_entity.entity_id,
                NAME: "Moisture (raw)",
                ICON: "mdi:water",
            }
        )
        # Not decoration: capacitive readings drift with soil temperature.
        # If moisture oscillates in phase with this daily, that is the pot
        # near a vent or in sun, not water moving.
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

    light_rows = _light_rows(config, plant)
    if light_rows:
        if rows:
            rows.append(divider())
        rows.extend(light_rows)

    care_rows = _care_rows(plant)
    if care_rows:
        if rows:
            rows.append(divider())
        rows.extend(care_rows)

    if rows:
        cards.append(EntitiesCard(title=plant.display, entities=rows))
    else:
        cards.append(
            MarkdownCard(f"### {plant.display}\n\nNo sensors and no care tasks.")
        )

    # A week, so one full wet/dry cycle fits on screen — the shape is the
    # point. Soil temperature shares the card so the temperature-artifact
    # check can be made without flipping between graphs.
    if moisture is not None:
        graph: list[str | dict[str, str]] = [
            {ENTITY: moisture.moisture_entity.entity_id, NAME: "Moisture (raw)"},
            {
                ENTITY: naming.moisture_smoothed(plant).full,
                NAME: "Moisture (median)",
            },
        ]
        if moisture.temperature_entity is not None:
            graph.append(
                {ENTITY: moisture.temperature_entity.entity_id, NAME: "Soil temp"}
            )
        cards.append(
            HistoryGraphCard(
                title=f"{plant.display} — one week",
                hours_to_show=168,
                entities=graph,
            )
        )

    # Two days rather than a week, because the shape being read here is the
    # daily one: where accumulation flattens is when the light stopped, and
    # a week compresses that into nothing.
    if plant.dli is not None:
        cards.append(
            HistoryGraphCard(
                title=f"{plant.display} — light",
                hours_to_show=48,
                entities=[{ENTITY: naming.dli_today(plant).full, NAME: "DLI today"}],
            )
        )

    return VerticalStackCard(cards=cards)


# ---- Fragments ----------------------------------------------------------


def fragments(config: PlantCareConfig) -> list[Fragment]:
    plants = {plant.name: plant for plant in config.plants}
    fixtures = {fixture.name: fixture for fixture in config.lights}
    people = list(config.owners.people())

    def overview(params: Params) -> VerticalStackCard:
        person = params.get("person")
        if person is None:
            return overview_card(config, naming.outstanding(), config.plants)
        return overview_card(
            config, naming.person_outstanding(person), config.plants_for(person)
        )

    plant_schema = probatio.Schema(
        {probatio.Required("plant"): probatio.In(list(plants))}
    )

    return [
        Fragment(
            "overview",
            overview,
            description=(
                "Every plant's bars, the outstanding feed, and graphs across "
                "plants; one person's plants if `person` is given"
            ),
            schema=probatio.Schema({probatio.Optional("person"): probatio.In(people)}),
        ),
        Fragment(
            "plant",
            lambda params: bars_card(config, [plants[params["plant"]]]),
            description="One plant's water, light and care tasks as bars",
            schema=plant_schema,
        ),
        Fragment(
            "plant_detail",
            lambda params: plant_detail_card(config, plants[params["plant"]]),
            description="Every entity of one plant, with its graphs",
            schema=plant_schema,
        ),
        Fragment(
            "lamp",
            lambda params: fixture_card(fixtures[params["lamp"]]),
            description="One grow light: switch, on-time, killswitch, schedule",
            schema=probatio.Schema(
                {probatio.Required("lamp"): probatio.In(list(fixtures))}
            ),
        ),
    ]


# ---- The dashboard ------------------------------------------------------


class PlantsDashboard(GeneratedDashboard):
    def __init__(self, config: PlantCareConfig) -> None:
        self._config = config

    @property
    def title(self) -> str:
        return "Plants"

    @property
    def url_path(self) -> str:
        return "plants"

    def _view(self, title: str, path: str, icon: str, card: Renderable) -> View:
        return View(title=title, path=path, icon=icon, cards=[card])

    async def render(self) -> DBT:
        config = self._config
        views = [
            self._view(
                "All",
                "all",
                "mdi:sprout",
                overview_card(config, naming.outstanding(), config.plants),
            )
        ]
        views.extend(
            self._view(
                person.replace("_", " ").title(),
                person,
                config.owners.icon(person),
                overview_card(
                    config,
                    naming.person_outstanding(person),
                    config.plants_for(person),
                ),
            )
            for person in config.owners.people()
        )
        if config.lights:
            views.append(
                self._view(
                    "Lamps",
                    "lamps",
                    "mdi:lightbulb-group",
                    VerticalStackCard(
                        cards=[fixture_card(fixture) for fixture in config.lights]
                    ),
                )
            )
        views.append(
            self._view(
                "Debug",
                "debug",
                "mdi:bug",
                VerticalStackCard(
                    cards=[plant_detail_card(config, plant) for plant in config.plants]
                ),
            )
        )
        return Dashboard(views).render()
