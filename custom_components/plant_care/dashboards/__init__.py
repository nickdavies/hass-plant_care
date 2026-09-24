"""The generated Plants dashboard.

Built from the plant list, so adding a plant needs no dashboard edit. One tab
for everything, then one per person. Every tab leads with its outstanding feed,
so opening it answers "what needs me" before showing any individual plant.
"""

from __future__ import annotations

from collections.abc import Sequence
from string import Template

from custom_components.lovelace_codegen import (
    DBT,
    ENTITY,
    ICON,
    NAME,
    Dashboard,
    EntitiesCard,
    GeneratedDashboard,
    HistoryGraphCard,
    MarkdownCard,
    Renderable,
    VerticalStackCard,
    View,
    divider,
)

from ..model import Calibrated, Entity, LightFixture, Plant, PlantCareConfig, naming

# The list arrives already rendered, in `model/markdown.py`. This card is not
# the only one showing it — the household dashboards show the same feed — and a
# copy of the Jinja on each is one that will not be updated when an item grows a
# field. The count in the heading is the sensor's own state.
# A `Template` rather than `str.format`, because the body is Jinja.
OVERVIEW = Template("""## Needs attention ({{ states('$entity') }})

{{ state_attr('$entity', 'markdown') }}""")


def overview(entity: Entity) -> str:
    return OVERVIEW.substitute(entity=entity.full)


NOTHING_YET = "### No plants are yours yet"


def text_row(name: str, text: str, icon: str) -> dict[str, str]:
    """A row inside an EntitiesCard showing a fixed string rather than an entity.

    Lives here rather than in `lovelace_codegen` because this is the only
    dashboard that needs it. Move it there the day the second one does.
    """
    return {"type": "text", NAME: name, "text": text, ICON: icon}


CALIBRATING = """### {display} — calibrating

No watering threshold yet, by design: one would be a number nobody measured.
Water by hand, then read the two endpoints off the graph below —

- **fieldCapacity**: the reading 60 minutes after watering to runoff
- **dryPoint**: the reading when the skewer test says it is time

Put both in `inputs/plants.yaml` and regenerate."""


class PlantsDashboard(GeneratedDashboard):
    def __init__(self, config: PlantCareConfig) -> None:
        self._config = config
        self._plants = config.plants

    @property
    def title(self) -> str:
        return "Plants"

    @property
    def url_path(self) -> str:
        return "plants"

    def _care_rows(self, plant: Plant) -> list[dict[str, str]]:
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

    def _light_rows(self, plant: Plant) -> list[str | dict[str, str]]:
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
            lux = self._config.lux(plant.lux)
            if lux is not None:
                rows.append(
                    {
                        ENTITY: naming.lux_average(lux).full,
                        NAME: "Light level",
                        ICON: "mdi:brightness-5",
                    }
                )

        for fixture in self._config.fixtures_for(plant):
            rows.append(
                {
                    ENTITY: fixture.switch_entity,
                    NAME: f"{fixture.name.replace('_', ' ').title()} lamp",
                    ICON: "mdi:lightbulb",
                }
            )

        return rows

    def _fixture_card(self, fixture: LightFixture) -> Renderable:
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
        title = f"{fixture.name.replace('_', ' ').title()} lamp"
        return EntitiesCard(title=title, entities=rows)

    def _plant_card(self, plant: Plant) -> Renderable:
        cards: list[Renderable] = []
        rows: list[str | dict[str, str]] = []

        moisture = plant.moisture
        if moisture is not None:
            calibrated = isinstance(moisture.calibration, Calibrated)

            if not calibrated:
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

        light_rows = self._light_rows(plant)
        if light_rows:
            if rows:
                rows.append(divider())
            rows.extend(light_rows)

        care_rows = self._care_rows(plant)
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
                    entities=[
                        {ENTITY: naming.dli_today(plant).full, NAME: "DLI today"}
                    ],
                )
            )

        return VerticalStackCard(cards=cards)

    def _view(
        self,
        title: str,
        path: str,
        icon: str,
        feed: Entity,
        plants: Sequence[Plant],
        fixtures: Sequence[LightFixture],
    ) -> View:
        cards: list[Renderable] = [MarkdownCard(overview(feed))]
        if plants:
            cards.extend(self._plant_card(plant) for plant in plants)
        else:
            cards.append(MarkdownCard(NOTHING_YET))
        cards.extend(self._fixture_card(fixture) for fixture in fixtures)
        return View(
            title=title, path=path, icon=icon, cards=[VerticalStackCard(cards=cards)]
        )

    async def render(self) -> DBT:
        views = [
            self._view(
                "All",
                "all",
                "mdi:sprout",
                naming.outstanding(),
                self._plants,
                self._config.lights,
            )
        ]
        views.extend(
            self._view(
                person.replace("_", " ").title(),
                person,
                self._config.owners.icon(person),
                naming.person_outstanding(person),
                self._config.plants_for(person),
                self._config.fixtures_for_person(person),
            )
            for person in self._config.owners.people()
        )
        return Dashboard(views).render()
