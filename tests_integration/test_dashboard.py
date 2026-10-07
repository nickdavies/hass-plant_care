"""The generated Plants dashboard, and the fragments it is built from.

Built from the plant list, so adding a plant needs no dashboard edit. An
overview tab, a tab each for water, light and chores, and every plant in full;
then an overview per person and a page per plant, as subviews. These tests exist mostly to catch the case where a card
references an entity the component never created — which renders as a blank
row nobody notices.
"""

from __future__ import annotations

from typing import Any

import probatio
import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.loader import DATA_CUSTOM_COMPONENTS
from homeassistant.setup import async_setup_component

from custom_components.plant_care.model.owners import DEFAULT_PERSON_ICON

from .conftest import (
    DOMAIN,
    LUX_1,
    LUX_2,
    MONSTERA_DLI,
    MONSTERA_DLI_TARGET,
    REPO_ROOT,
    SPARE_SWITCH,
    STUDY_KILLSWITCH,
    STUDY_LUX,
    STUDY_ON_MINUTES,
    STUDY_SWITCH,
    _ensure_custom_components_path,
    at,
    mock_phones,
    start,
)

BARS = "custom:plant-care-bars"
CARD_JS = "custom_components/plant_care/dashboards/bars-card.js"


def _fragments(hass: HomeAssistant) -> Any:
    """lovelace_codegen's fragment registry.

    Imported here, not at the top: lovelace_codegen is only importable once a
    fixture has put it on the path.
    """
    from custom_components.lovelace_codegen.fragments import DATA_FRAGMENTS

    return hass.data[DATA_FRAGMENTS]


TABS = ["all", "water", "light", "chores", "debug"]


async def _views(hass: HomeAssistant) -> dict[str, dict[str, Any]]:
    """The rendered tabs and subviews, by path."""
    config = await hass.data["lovelace"].dashboards["plants"].async_load(False)
    return {view["path"]: view for view in config["views"]}


def _cards(view: dict[str, Any]) -> list[dict[str, Any]]:
    """The cards of a tab, each of which is one vertical stack."""
    return view["cards"][0]["cards"]


def _groups(view: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """An overview tab's bars, by plant."""
    (bars,) = [card for card in _cards(view) if card["type"] == BARS]
    return {group["title"]: group["bars"] for group in bars["groups"]}


def _titled(view: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """A tab's entities cards, by title, however deep in its stacks."""
    found: dict[str, dict[str, Any]] = {}

    def walk(card: dict[str, Any]) -> None:
        if card["type"] == "entities" and "title" in card:
            found[card["title"]] = card
        for child in card.get("cards", []):
            walk(child)

    walk(view["cards"][0])
    return found


def _entity_ids(node: Any) -> list[str]:
    """Every entity id anywhere in a rendered card tree."""
    found: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "entity" and isinstance(value, str):
                found.append(value)
            else:
                found.extend(_entity_ids(value))
    elif isinstance(node, list):
        for item in node:
            found.extend(_entity_ids(item))
    return found


class TestRegistration:
    async def test_the_dashboard_is_registered(
        self, integration: HomeAssistant
    ) -> None:
        assert "plants" in integration.data["lovelace"].dashboards

    async def test_the_bars_card_is_served(
        self, integration: HomeAssistant, hass_client: Any
    ) -> None:
        """By this component, which owns it, so no dashboard needs a resource
        entry for it."""
        client = await hass_client()
        response = await client.get("/plant_care/bars-card.js")

        assert response.status == 200
        assert await response.text() == (REPO_ROOT / CARD_JS).read_text()


class TestTabs:
    async def test_the_tabs_then_people_and_plants_as_subviews(
        self, integration: HomeAssistant
    ) -> None:
        """People only: a group's members each have an overview."""
        views = await _views(integration)
        assert [path for path, view in views.items() if not view.get("subview")] == (
            TABS
        )
        assert [path for path, view in views.items() if view.get("subview")] == [
            "nick",
            "britta",
            "plant-passionfruit",
            "plant-monstera",
            "plant-front_step_pot",
        ]
        assert views["debug"]["icon"] == "mdi:bug"

    async def test_the_tabs_are_the_same_whatever_is_configured(
        self, hass: HomeAssistant, freezer: FrozenDateTimeFactory
    ) -> None:
        """So a link to one never breaks when a plant gains or loses a probe."""
        await start(hass, freezer, at(12, 0))
        views = await _views(hass)
        assert [path for path, view in views.items() if not view.get("subview")] == (
            TABS
        )

    async def test_an_overview_wears_the_icon_its_person_chose(
        self, integration: HomeAssistant
    ) -> None:
        """`britta` chose none, so she gets the default rather than no icon."""
        views = await _views(integration)
        assert views["nick"]["icon"] == "mdi:human-male"
        assert views["britta"]["icon"] == DEFAULT_PERSON_ICON

    async def test_a_subview_goes_back_to_wherever_it_was_opened_from(
        self, integration: HomeAssistant
    ) -> None:
        """A person's overview is opened from their own dashboard, and a
        plant's page from any overview, so neither names a fixed way back."""
        views = await _views(integration)
        assert "back_path" not in views["nick"]
        assert "back_path" not in views["plant-monstera"]


class TestOverview:
    async def test_bars_then_the_feed_then_graphs(
        self, integration: HomeAssistant
    ) -> None:
        """The glance first, then what needs doing, then the detail."""
        views = await _views(integration)
        types = [card["type"] for card in _cards(views["all"])]
        assert types == ["grid", BARS, "markdown", "history-graph"]

    async def test_the_feed_is_the_tabs_own(self, integration: HomeAssistant) -> None:
        views = await _views(integration)
        assert "sensor.plant_outstanding'" in _cards(views["all"])[2]["content"]
        assert "sensor.plant_outstanding_nick" in _cards(views["nick"])[2]["content"]

    async def test_the_feed_reads_the_rendering_rather_than_the_items(
        self, integration: HomeAssistant
    ) -> None:
        """The household dashboards show the same list, so the Jinja that knows
        what an item looks like lives on the sensor, not in each card."""
        views = await _views(integration)
        content = _cards(views["all"])[2]["content"]
        assert "'markdown'" in content
        assert "item.label" not in content

    async def test_every_plant_gets_its_bars(self, integration: HomeAssistant) -> None:
        views = await _views(integration)
        assert list(_groups(views["all"])) == [
            "Passionfruit",
            "Monstera",
            "Front step pot",
        ]

    async def test_a_tab_shows_owned_and_group_plants_only(
        self, integration: HomeAssistant
    ) -> None:
        views = await _views(integration)
        assert list(_groups(views["nick"])) == ["Passionfruit", "Monstera"]
        assert list(_groups(views["britta"])) == ["Monstera", "Front step pot"]

    async def test_a_person_with_nothing_is_told_so(self, hass: HomeAssistant) -> None:
        """Rather than an empty page that looks like a rendering failure."""
        mock_phones(hass)
        hass.data.pop(DATA_CUSTOM_COMPONENTS, None)
        _ensure_custom_components_path()
        assert await async_setup_component(
            hass,
            DOMAIN,
            {
                DOMAIN: {
                    "owners": {"nick": {"action": "notify.nick"}},
                    "system_notify": "notify.phones",
                    "plants": [],
                }
            },
        )
        views = await _views(hass)
        assert "No plants are yours yet" in str(views["nick"])


class TestPlantGrid:
    async def test_a_button_per_plant_three_across(
        self, integration: HomeAssistant
    ) -> None:
        views = await _views(integration)
        grid = _cards(views["all"])[0]
        assert grid["columns"] == 3
        assert [button["name"] for button in grid["cards"]] == [
            "Passionfruit",
            "Monstera",
            "Front step pot",
        ]

    async def test_a_button_opens_its_plants_page(
        self, integration: HomeAssistant
    ) -> None:
        """By its full path, so the same button embedded on another dashboard
        still lands here."""
        views = await _views(integration)
        button = _cards(views["all"])[0]["cards"][0]
        assert button["tap_action"] == {
            "action": "navigate",
            "navigation_path": "/plants/plant-passionfruit",
        }
        assert views["plant-passionfruit"]["subview"] is True

    async def test_a_persons_grid_is_their_plants(
        self, integration: HomeAssistant
    ) -> None:
        views = await _views(integration)
        grid = _cards(views["britta"])[0]
        assert [button["name"] for button in grid["cards"]] == [
            "Monstera",
            "Front step pot",
        ]


class TestPlantPage:
    async def test_its_bars_then_the_plant_in_full(
        self, integration: HomeAssistant
    ) -> None:
        views = await _views(integration)
        bars, detail = _cards(views["plant-passionfruit"])
        assert [group["title"] for group in bars["groups"]] == ["Passionfruit"]
        assert "sensor.roam_sensor_moisture_1_battery" in _entity_ids(detail)

    async def test_titled_with_the_plants_name(
        self, integration: HomeAssistant
    ) -> None:
        views = await _views(integration)
        assert views["plant-front_step_pot"]["title"] == "Front step pot"


class TestWaterBar:
    async def test_a_calibrated_plant_shows_available_water(
        self, integration: HomeAssistant
    ) -> None:
        """Orange up to the refill threshold, so a bar still orange is a plant
        that needs water."""
        views = await _views(integration)
        water = _groups(views["all"])["Passionfruit"][0]
        assert water["entity"] == "sensor.plant_passionfruit_available_water"
        assert water["max"] == 100
        assert [stop["from"] for stop in water["stops"]] == [0, 15.0]

    async def test_a_calibrating_plant_shows_its_probe_reading(
        self, integration: HomeAssistant
    ) -> None:
        """There is no available-water scale without both endpoints."""
        views = await _views(integration)
        water = _groups(views["all"])["Monstera"][0]
        assert water["entity"] == "sensor.plant_monstera_moisture_smoothed"

    async def test_a_plant_with_no_probe_counts_down_to_watering(
        self, integration: HomeAssistant
    ) -> None:
        """And only once: the watering task is the water bar, not a care bar
        as well."""
        views = await _views(integration)
        bars = _groups(views["all"])["Front step pot"]
        assert bars == [
            {
                "entity": "sensor.plant_front_step_pot_water_days_since",
                "name": "Water",
                "icon": "mdi:watering-can",
                "max": 4,
                "countdown": True,
            }
        ]


class TestCareBars:
    async def test_each_task_counts_down_its_own_interval(
        self, integration: HomeAssistant
    ) -> None:
        views = await _views(integration)
        feed = _groups(views["all"])["Passionfruit"][-1]
        assert feed == {
            "entity": "sensor.plant_passionfruit_feed_days_since",
            "name": "Feed",
            "icon": "mdi:nutrition",
            "max": 14,
            "countdown": True,
        }


class TestLightBars:
    async def test_a_measured_plant_fills_towards_its_band(
        self, hass: HomeAssistant, freezer: FrozenDateTimeFactory
    ) -> None:
        """Orange until the bottom of the preferred band, green up to its top."""
        await start(hass, freezer, at(12, 0))
        views = await _views(hass)

        (light,) = [
            bar
            for bar in _groups(views["all"])["Monstera"]
            if bar["entity"] == MONSTERA_DLI
        ]
        assert light["max"] == 9.0
        assert [stop["from"] for stop in light["stops"]] == [0, 4.0]

    async def test_a_lit_unmeasured_plant_shows_its_lamps_day(
        self, hass: HomeAssistant, freezer: FrozenDateTimeFactory
    ) -> None:
        """Out of the whole day's window, which moves day to day, so it is read
        off the sensor rather than written into the card."""
        await start(hass, freezer, at(12, 0))
        views = await _views(hass)

        assert _groups(views["all"])["Ficus alii"] == [
            {
                "entity": STUDY_ON_MINUTES,
                "name": "Lamp",
                "icon": "mdi:lightbulb",
                "max_attribute": "day_possible_minutes",
            }
        ]

    async def test_a_plant_nothing_lights_has_no_light_bar(
        self, integration: HomeAssistant
    ) -> None:
        views = await _views(integration)
        names = [bar["name"] for bar in _groups(views["all"])["Passionfruit"]]
        assert names == ["Water", "Feed"]


class TestComparisonGraphs:
    async def test_water_compares_only_calibrated_plants(
        self, integration: HomeAssistant
    ) -> None:
        """A raw reading means something different in every pot."""
        views = await _views(integration)
        (graph,) = [c for c in _cards(views["all"]) if c["type"] == "history-graph"]
        assert _entity_ids(graph) == ["sensor.plant_passionfruit_available_water"]

    async def test_light_compares_percentages_of_each_plants_target(
        self, hass: HomeAssistant, freezer: FrozenDateTimeFactory
    ) -> None:
        await start(hass, freezer, at(12, 0))
        views = await _views(hass)
        graphs = [c for c in _cards(views["all"]) if c["type"] == "history-graph"]
        assert [_entity_ids(graph) for graph in graphs] == [[MONSTERA_DLI_TARGET]]


class TestWaterTab:
    async def test_every_probe_raw_then_available_water(
        self, integration: HomeAssistant
    ) -> None:
        """Raw first, because it is the one graph every probe is on: a
        calibrating probe has no available water to show."""
        views = await _views(integration)
        raw, available = _cards(views["water"])[:2]
        assert _entity_ids(raw) == [
            "sensor.roam_sensor_moisture_1_soil_moisture",
            "sensor.nick_study_sensor_monstera_window_soil_moisture",
        ]
        assert _entity_ids(available) == ["sensor.plant_passionfruit_available_water"]

    async def test_then_each_probe_in_full(self, integration: HomeAssistant) -> None:
        """A plant with no probe has nothing to show here."""
        views = await _views(integration)
        assert list(_titled(views["water"])) == ["Passionfruit", "Monstera"]
        assert "Monstera — calibrating" in str(views["water"])

    async def test_says_so_when_nothing_has_a_probe(
        self, hass: HomeAssistant, freezer: FrozenDateTimeFactory
    ) -> None:
        await start(hass, freezer, at(12, 0))
        views = await _views(hass)
        assert "No plant has a moisture probe" in str(views["water"])


class TestLightTab:
    async def test_starts_with_each_plant_against_its_target(
        self, hass: HomeAssistant, freezer: FrozenDateTimeFactory
    ) -> None:
        await start(hass, freezer, at(12, 0))
        views = await _views(hass)
        assert _entity_ids(_cards(views["light"])[0]) == [MONSTERA_DLI_TARGET]

    async def test_a_measured_plant_shows_the_sensors_behind_its_light_level(
        self, hass: HomeAssistant, freezer: FrozenDateTimeFactory
    ) -> None:
        """A light level that looks wrong is most often one member shaded or
        gone quiet, so they sit under it, on the card and on the graph."""
        await start(hass, freezer, at(12, 0))
        views = await _views(hass)

        card, graph = _cards(views["light"])[1]["cards"]
        assert _entity_ids(card) == [
            MONSTERA_DLI,
            STUDY_LUX,
            LUX_1,
            LUX_2,
            STUDY_SWITCH,
        ]
        assert _entity_ids(graph) == [MONSTERA_DLI, STUDY_LUX, LUX_1, LUX_2]
        assert graph["hours_to_show"] == 48

    async def test_only_measured_plants_get_a_card_of_their_own(
        self, hass: HomeAssistant, freezer: FrozenDateTimeFactory
    ) -> None:
        """A lamp alone measures nothing; its plants show under the lamp."""
        await start(hass, freezer, at(12, 0))
        views = await _views(hass)
        assert list(_titled(views["light"])) == [
            "Monstera",
            "Study Shelf lamp",
            "Spare Shelf lamp",
        ]

    async def test_says_so_when_nothing_is_measured_or_lit(
        self, integration: HomeAssistant
    ) -> None:
        views = await _views(integration)
        assert "No plant is measured or lit" in str(views["light"])


class TestLamps:
    async def test_the_killswitch_sits_next_to_the_on_time_it_affects(
        self, hass: HomeAssistant, freezer: FrozenDateTimeFactory
    ) -> None:
        """Freezing a fixture is a decision about the plants under it, and the
        on-time figure is the only thing here that says what it actually did."""
        await start(hass, freezer, at(12, 0))
        views = await _views(hass)

        ids = _entity_ids(_titled(views["light"])["Study Shelf lamp"])
        assert STUDY_KILLSWITCH in ids
        assert STUDY_ON_MINUTES in ids

    async def test_each_fixture_card_ends_with_its_schedule(
        self, hass: HomeAssistant, freezer: FrozenDateTimeFactory
    ) -> None:
        """Next to the on-time figure, the window is what makes it readable.

        Plain text rows, not entities: the schedule is config, and the card
        prints the config's own times.
        """
        await start(hass, freezer, at(12, 0))
        views = await _views(hass)
        by_title = _titled(views["light"])

        study = by_title["Study Shelf lamp"]["entities"]
        assert study[-2:] == [
            {
                "type": "text",
                "name": "Schedule (guaranteed)",
                "text": "09:00–17:00",
                "icon": "mdi:clock-outline",
            },
            {
                "type": "text",
                "name": "Schedule (if awake)",
                "text": "06:00–19:00",
                "icon": "mdi:clock-outline",
            },
        ]
        assert study[-3]["entity"] == STUDY_KILLSWITCH

        spare = by_title["Spare Shelf lamp"]["entities"]
        assert spare[-1] == {
            "type": "text",
            "name": "Schedule",
            "text": "07:00–19:00",
            "icon": "mdi:clock-outline",
        }

    async def test_a_persons_lamps_are_those_over_their_plants(
        self, hass: HomeAssistant, freezer: FrozenDateTimeFactory
    ) -> None:
        await start(hass, freezer, at(12, 0))
        card = _fragments(hass).get(DOMAIN, "lamps").render({"person": "britta"})
        assert [lamp["title"] for lamp in card["cards"]] == ["Spare Shelf lamp"]


class TestChoresTab:
    async def test_every_task_counts_down_a_scheduled_watering_included(
        self, integration: HomeAssistant
    ) -> None:
        """On the overview a scheduled watering is the water bar; here it is a
        chore like any other."""
        views = await _views(integration)
        bars = _cards(views["chores"])[0]
        assert bars["type"] == BARS
        assert {group["title"]: _entity_ids(group) for group in bars["groups"]} == {
            "Passionfruit": ["sensor.plant_passionfruit_feed_days_since"],
            "Monstera": ["sensor.plant_monstera_pest_check_days_since"],
            "Front step pot": ["sensor.plant_front_step_pot_water_days_since"],
        }

    async def test_then_each_plants_tasks_with_their_buttons(
        self, integration: HomeAssistant
    ) -> None:
        views = await _views(integration)
        card = _titled(views["chores"])["Front step pot"]
        assert _entity_ids(card) == [
            "sensor.plant_front_step_pot_water_days_since",
            "button.plant_front_step_pot_water_done",
        ]

    async def test_says_so_when_nothing_has_a_task(
        self, hass: HomeAssistant, freezer: FrozenDateTimeFactory
    ) -> None:
        await start(hass, freezer, at(12, 0))
        views = await _views(hass)
        assert "No plant has a care task" in str(views["chores"])


class TestDebugTab:
    async def test_every_plant_in_full(self, integration: HomeAssistant) -> None:
        views = await _views(integration)
        rendered = str(views["debug"])
        for display in ("Passionfruit", "Monstera", "Front step pot"):
            assert display in rendered
        assert "sensor.roam_sensor_moisture_1_battery" in rendered

    async def test_a_calibrating_plant_says_so_on_its_card(
        self, integration: HomeAssistant
    ) -> None:
        """The reason lives on the dashboard rather than only in a config
        comment nobody reading the dashboard will see."""
        views = await _views(integration)
        assert "Monstera — calibrating" in str(views["debug"])

    async def test_a_measured_plant_shows_its_band_on_the_row(
        self, hass: HomeAssistant, freezer: FrozenDateTimeFactory
    ) -> None:
        await start(hass, freezer, at(12, 0))
        views = await _views(hass)

        assert "DLI today (want 4–9)" in str(views["debug"])


class TestReferences:
    async def test_a_calibrating_plant_is_never_shown_available_water(
        self, integration: HomeAssistant
    ) -> None:
        """There is no scale to express it on without both endpoints."""
        config = (
            await integration.data["lovelace"].dashboards["plants"].async_load(False)
        )
        assert "sensor.plant_monstera_available_water" not in str(config)

    async def test_an_unmeasured_plant_is_never_shown_dli(
        self, hass: HomeAssistant, freezer: FrozenDateTimeFactory
    ) -> None:
        """Derived from which fields are present, not from a feature flag."""
        await start(hass, freezer, at(12, 0))
        config = await hass.data["lovelace"].dashboards["plants"].async_load(False)

        assert "sensor.plant_ficus_alii_dli" not in str(config)

    async def test_every_referenced_entity_is_one_we_create_or_read(
        self, integration: HomeAssistant
    ) -> None:
        """A card pointing at an id nothing creates renders as a blank row.

        Allowed: entities this component creates, and the probe entities the
        config named.
        """
        config = (
            await integration.data["lovelace"].dashboards["plants"].async_load(False)
        )
        probe_entities = {
            "sensor.roam_sensor_moisture_1_soil_moisture",
            "sensor.roam_sensor_moisture_1_temperature",
            "sensor.roam_sensor_moisture_1_battery",
            "sensor.nick_study_sensor_monstera_window_soil_moisture",
        }

        for entity_id in _entity_ids(config):
            if entity_id in probe_entities:
                continue
            assert integration.states.get(entity_id) is not None, (
                f"card references {entity_id}, which nothing created"
            )

    async def test_every_referenced_entity_exists(
        self, hass: HomeAssistant, freezer: FrozenDateTimeFactory
    ) -> None:
        """Including the lamp switches and lux sensors, which come from
        zigbee2mqtt and ESPHome rather than from here."""
        await start(hass, freezer, at(12, 0))
        config = await hass.data["lovelace"].dashboards["plants"].async_load(False)

        for entity_id in _entity_ids(config):
            if entity_id in (STUDY_SWITCH, SPARE_SWITCH, LUX_1, LUX_2):
                continue
            assert hass.states.get(entity_id) is not None, (
                f"card references {entity_id}, which nothing created"
            )


class TestFragments:
    async def test_the_dashboards_cards_are_offered(
        self, integration: HomeAssistant
    ) -> None:
        described = _fragments(integration).describe()[DOMAIN]
        assert sorted(fragment["name"] for fragment in described) == [
            "bars",
            "chores",
            "debug",
            "feed",
            "lamp",
            "lamps",
            "light",
            "light_graph",
            "moisture_graph",
            "overview",
            "plant",
            "plant_chores",
            "plant_detail",
            "plant_grid",
            "plant_light",
            "plant_water",
            "water",
            "water_graph",
        ]

    async def test_a_persons_overview_is_their_subview(
        self, integration: HomeAssistant
    ) -> None:
        """The same function builds both, so an embedded copy cannot drift."""
        fragment = _fragments(integration).get(DOMAIN, "overview")
        views = await _views(integration)

        assert fragment.render({"person": "nick"}) == views["nick"]["cards"][0]
        assert fragment.render() == views["all"]["cards"][0]

    @pytest.mark.parametrize("tab", ["water", "chores", "debug"])
    async def test_each_tab_is_a_fragment(
        self, integration: HomeAssistant, tab: str
    ) -> None:
        fragment = _fragments(integration).get(DOMAIN, tab)
        views = await _views(integration)
        assert fragment.render() == views[tab]["cards"][0]

    async def test_the_light_tab_is_a_fragment(
        self, hass: HomeAssistant, freezer: FrozenDateTimeFactory
    ) -> None:
        await start(hass, freezer, at(12, 0))
        fragment = _fragments(hass).get(DOMAIN, "light")
        views = await _views(hass)
        assert fragment.render() == views["light"]["cards"][0]

    async def test_a_tab_fragment_takes_a_person(
        self, integration: HomeAssistant
    ) -> None:
        card = _fragments(integration).get(DOMAIN, "chores").render({"person": "nick"})
        assert [group["title"] for group in card["cards"][0]["groups"]] == [
            "Passionfruit",
            "Monstera",
        ]

    async def test_one_plants_page_is_offered_only_where_it_has_one(
        self, integration: HomeAssistant
    ) -> None:
        """The pot has no probe, so it is not a choice for its water card."""
        fragment = _fragments(integration).get(DOMAIN, "plant_water")
        assert fragment.render({"plant": "monstera"})["type"] == "vertical-stack"
        with pytest.raises(probatio.Invalid):
            fragment.render({"plant": "front_step_pot"})

    async def test_an_empty_graph_says_so(self, integration: HomeAssistant) -> None:
        """Rather than a blank card, or an error where it is embedded."""
        card = _fragments(integration).get(DOMAIN, "light_graph").render()
        assert card == {"type": "markdown", "content": "### No plant has a DLI target"}

    async def test_one_plants_bars(self, integration: HomeAssistant) -> None:
        fragment = _fragments(integration).get(DOMAIN, "plant")
        card = fragment.render({"plant": "passionfruit"})

        assert card["type"] == BARS
        assert [group["title"] for group in card["groups"]] == ["Passionfruit"]

    async def test_an_unknown_plant_is_refused(
        self, integration: HomeAssistant
    ) -> None:
        """As a `probatio.Invalid`, which the websocket answers with the reason."""
        fragment = _fragments(integration).get(DOMAIN, "plant_detail")
        with pytest.raises(probatio.Invalid):
            fragment.render({"plant": "cactus"})

    async def test_the_params_are_listed_as_choices(
        self, integration: HomeAssistant
    ) -> None:
        """What `lovelace_codegen/fragments` sends, so a dashboard repo can
        check its references."""
        described = _fragments(integration).describe()[DOMAIN]
        (overview,) = [f for f in described if f["name"] == "overview"]
        (person,) = overview["params"]
        assert person["name"] == "person"
        assert person["options"] == [("nick", "nick"), ("britta", "britta")]
