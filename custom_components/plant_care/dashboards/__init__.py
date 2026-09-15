"""The generated Plants dashboard.

Built from the plant list, so adding a plant needs no dashboard edit. Leads with
the outstanding feed, so opening it answers "what needs me" before showing any
individual plant.
"""

from __future__ import annotations

from ..lovelace import (
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
from ..model import Calibrated, Plant, naming

OVERVIEW = """## Needs attention

{% set items = state_attr('sensor.plant_outstanding', 'items') or [] %}
{% if items | count == 0 %}
Nothing outstanding.
{% else %}
{% for item in items %}
- **{{ item.name }}** — {{ item.label }} ({{ item.days | round(0) }}d ago, every {{ item.every }}d)
{% endfor %}
{% endif %}"""

CALIBRATING = """### {display} — calibrating

No watering threshold yet, by design: one would be a number nobody measured.
Water by hand, then read the two endpoints off the graph below —

- **fieldCapacity**: the reading 60 minutes after watering to runoff
- **dryPoint**: the reading when the skewer test says it is time

Put both in `inputs/plants.yaml` and regenerate."""


class PlantsDashboard(GeneratedDashboard):
    def __init__(self, plants: tuple[Plant, ...]) -> None:
        self._plants = plants

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

        return VerticalStackCard(cards=cards)

    async def render(self) -> DBT:
        cards: list[Renderable] = [MarkdownCard(OVERVIEW)]
        cards.extend(self._plant_card(plant) for plant in self._plants)

        return Dashboard(
            [View(title=self.title, cards=[VerticalStackCard(cards=cards)])]
        ).render()
