"""The generated Plants dashboard, and the fragments it is built from.

Built from the plant list, so adding a plant needs no dashboard edit. Five
tabs:

- **All**, the overview: a button per plant, each plant's bars, the
  outstanding feed, then graphs putting the plants side by side.
- **Water**, every probe: raw readings, available water, then each in full.
- **Light**, every measured plant and lamp: light against each plant's target,
  then each plant's DLI, light level and the sensors behind it, then the lamps.
- **Chores**, every care task: countdowns, then the buttons marking them done.
- **Debug**, every plant in full.

Then subviews, out of the tab bar: one overview per person, which their own
dashboard links to, and one page per plant, which its button opens.

Each card is built by one function in the module for its tab, and every one
worth embedding elsewhere is registered with lovelace_codegen as a fragment, so
a hand-written dashboard can show it (`custom:codegen-fragment`, source
`plant_care`) and cannot drift from this one.
"""

from __future__ import annotations

from collections.abc import Callable

import probatio
from custom_components.lovelace_codegen import (
    DBT,
    Dashboard,
    Fragment,
    GeneratedDashboard,
    HistoryGraphCard,
    Renderable,
    View,
)

from ..model import PlantCareConfig
from .chores import chores_card, plant_chores_card, tended
from .common import DASHBOARD, Scope, feed_card, nothing, plant_path, scope
from .detail import debug_card, plant_detail_card, plant_page_card
from .light import fixture_card, lamps_card, light_card, measured, plant_light_card
from .overview import (
    PLANT_ICON,
    available_water_graph,
    bars_card,
    light_target_graph,
    overview_card,
    plant_grid,
)
from .water import plant_water_card, probed, raw_moisture_graph, water_card

# ---- Fragments ----------------------------------------------------------


def fragments(config: PlantCareConfig) -> list[Fragment]:
    plants = {plant.name: plant for plant in config.plants}
    fixtures = {fixture.name: fixture for fixture in config.lights}
    people = list(config.owners.people())

    def scoped(
        name: str, build: Callable[[Scope], Renderable], description: str
    ) -> Fragment:
        """Everyone's plants, or one person's if `person` is given."""
        return Fragment(
            name,
            lambda params: build(scope(config, params.get("person"))),
            description=f"{description}; one person's if `person` is given",
            schema=probatio.Schema({probatio.Optional("person"): probatio.In(people)}),
        )

    def graph(
        name: str,
        build: Callable[[Scope], HistoryGraphCard | None],
        empty: str,
        description: str,
    ) -> Fragment:
        """A graph across plants, said in words when no plant is on it."""
        return scoped(name, lambda s: build(s) or nothing(empty), description)

    def per_plant(
        name: str,
        build: Callable[[str], Renderable],
        eligible: list[str],
        description: str,
    ) -> Fragment:
        """One plant's card, offered only for the plants that have one, so the
        listed choices are the ones that render."""
        return Fragment(
            name,
            lambda params: build(params["plant"]),
            description=description,
            schema=probatio.Schema({probatio.Required("plant"): probatio.In(eligible)}),
        )

    names = list(plants)
    return [
        # The overview, whole and in parts.
        scoped(
            "overview",
            lambda s: overview_card(config, s),
            "The plant buttons, bars, outstanding feed and graphs across plants",
        ),
        scoped(
            "plant_grid",
            lambda s: plant_grid(s.plants),
            "A button per plant, three across, each opening its page",
        ),
        scoped(
            "bars",
            lambda s: bars_card(config, s.plants),
            "Every plant's water, light and care tasks as bars",
        ),
        scoped("feed", lambda s: feed_card(s.feed), "Everything outstanding"),
        graph(
            "water_graph",
            lambda s: available_water_graph(s.plants),
            "No plant is calibrated",
            "Available water across calibrated plants, one week",
        ),
        graph(
            "light_graph",
            lambda s: light_target_graph(s.plants),
            "No plant has a DLI target",
            "Light as a % of each plant's target, two days",
        ),
        # The tabs, whole and in parts.
        scoped("water", water_card, "The Water tab"),
        graph(
            "moisture_graph",
            lambda s: raw_moisture_graph(s.plants),
            "No plant has a moisture probe",
            "Every probe's raw reading, one week",
        ),
        scoped("light", lambda s: light_card(config, s), "The Light tab"),
        scoped("lamps", lambda s: lamps_card(s.fixtures), "Every grow light"),
        scoped("chores", chores_card, "The Chores tab"),
        scoped(
            "debug",
            lambda s: debug_card(config, s),
            "Every plant in full: the Debug tab",
        ),
        # One plant.
        per_plant(
            "plant",
            lambda name: bars_card(config, [plants[name]]),
            names,
            "One plant's water, light and care tasks as bars",
        ),
        per_plant(
            "plant_detail",
            lambda name: plant_detail_card(config, plants[name]),
            names,
            "Every entity of one plant, with its graphs",
        ),
        per_plant(
            "plant_water",
            lambda name: plant_water_card(plants[name]),
            [plant.name for plant in probed(config.plants)],
            "One plant's probe in full, with its week",
        ),
        per_plant(
            "plant_light",
            lambda name: plant_light_card(config, plants[name]),
            [plant.name for plant in measured(config.plants)],
            "One plant's DLI, light level and lux sensors, with their graph",
        ),
        per_plant(
            "plant_chores",
            lambda name: plant_chores_card(plants[name]),
            [plant.name for plant in tended(config.plants)],
            "One plant's care tasks, with their mark-done buttons",
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
        return DASHBOARD

    async def render(self) -> DBT:
        config = self._config
        everyone = scope(config)

        def tab(title: str, path: str, icon: str, card: Renderable) -> View:
            return View(title=title, path=path, icon=icon, cards=[card])

        def subview(title: str, path: str, icon: str, card: Renderable) -> View:
            """Out of the tab bar. Its back arrow goes back in history, to
            wherever it was opened from: a tab here, or another dashboard."""
            return View(title=title, path=path, icon=icon, cards=[card], subview=True)

        views = [
            tab("All", "all", PLANT_ICON, overview_card(config, everyone)),
            tab("Water", "water", "mdi:water", water_card(everyone)),
            tab(
                "Light",
                "light",
                "mdi:white-balance-sunny",
                light_card(config, everyone),
            ),
            tab("Chores", "chores", "mdi:clipboard-check", chores_card(everyone)),
            tab("Debug", "debug", "mdi:bug", debug_card(config, everyone)),
        ]
        views.extend(
            subview(
                person.replace("_", " ").title(),
                person,
                config.owners.icon(person),
                overview_card(config, scope(config, person)),
            )
            for person in config.owners.people()
        )
        views.extend(
            subview(
                plant.display,
                plant_path(plant),
                PLANT_ICON,
                plant_page_card(config, plant),
            )
            for plant in config.plants
        )
        return Dashboard(views).render()
