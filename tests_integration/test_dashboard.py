"""The generated Plants dashboard, and the fragments it is built from.

Built from the plant list, so adding a plant needs no dashboard edit. An
overview tab for everything and one per person, then the lamps, then every
plant in full. These tests exist mostly to catch the case where a card
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
    MONSTERA_DLI,
    MONSTERA_DLI_TARGET,
    REPO_ROOT,
    SPARE_SWITCH,
    STUDY_KILLSWITCH,
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


async def _views(hass: HomeAssistant) -> dict[str, dict[str, Any]]:
    """The rendered tabs, by path."""
    config = await hass.data["lovelace"].dashboards["plants"].async_load(False)
    return {view["path"]: view for view in config["views"]}


def _cards(view: dict[str, Any]) -> list[dict[str, Any]]:
    """The cards of a tab, each of which is one vertical stack."""
    return view["cards"][0]["cards"]


def _groups(view: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """An overview tab's bars, by plant."""
    (bars,) = [card for card in _cards(view) if card["type"] == BARS]
    return {group["title"]: group["bars"] for group in bars["groups"]}


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
    async def test_overviews_then_debug(self, integration: HomeAssistant) -> None:
        """People only: a group's members each have a tab. No lamps tab when
        there are no lamps."""
        config = (
            await integration.data["lovelace"].dashboards["plants"].async_load(False)
        )
        assert [view["path"] for view in config["views"]] == [
            "all",
            "nick",
            "britta",
            "debug",
        ]
        assert config["views"][-1]["icon"] == "mdi:bug"

    async def test_the_lamps_sit_between_the_people_and_debug(
        self, hass: HomeAssistant, freezer: FrozenDateTimeFactory
    ) -> None:
        await start(hass, freezer, at(12, 0))
        views = await _views(hass)
        assert list(views) == ["all", "nick", "britta", "lamps", "debug"]

    async def test_a_tab_wears_the_icon_its_person_chose(
        self, integration: HomeAssistant
    ) -> None:
        """`britta` chose none, so she gets the default rather than no icon."""
        views = await _views(integration)
        assert views["nick"]["icon"] == "mdi:human-male"
        assert views["britta"]["icon"] == DEFAULT_PERSON_ICON


class TestOverview:
    async def test_bars_then_the_feed_then_graphs(
        self, integration: HomeAssistant
    ) -> None:
        """The glance first, then what needs doing, then the detail."""
        views = await _views(integration)
        types = [card["type"] for card in _cards(views["all"])]
        assert types == [BARS, "markdown", "history-graph"]

    async def test_the_feed_is_the_tabs_own(self, integration: HomeAssistant) -> None:
        views = await _views(integration)
        assert "sensor.plant_outstanding'" in _cards(views["all"])[1]["content"]
        assert "sensor.plant_outstanding_nick" in _cards(views["nick"])[1]["content"]

    async def test_the_feed_reads_the_rendering_rather_than_the_items(
        self, integration: HomeAssistant
    ) -> None:
        """The household dashboards show the same list, so the Jinja that knows
        what an item looks like lives on the sensor, not in each card."""
        views = await _views(integration)
        content = _cards(views["all"])[1]["content"]
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


class TestLampsTab:
    async def test_every_lamp_whoever_owns_the_plants(
        self, hass: HomeAssistant, freezer: FrozenDateTimeFactory
    ) -> None:
        await start(hass, freezer, at(12, 0))
        views = await _views(hass)
        titles = [card["title"] for card in _cards(views["lamps"])]
        assert titles == ["Study Shelf lamp", "Spare Shelf lamp"]

    async def test_the_killswitch_sits_next_to_the_on_time_it_affects(
        self, hass: HomeAssistant, freezer: FrozenDateTimeFactory
    ) -> None:
        """Freezing a fixture is a decision about the plants under it, and the
        on-time figure is the only thing here that says what it actually did."""
        await start(hass, freezer, at(12, 0))
        views = await _views(hass)

        ids = _entity_ids(views["lamps"])
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
        by_title = {card["title"]: card for card in _cards(views["lamps"])}

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
        """Including the lamp switches, which come from zigbee2mqtt rather than
        from here."""
        await start(hass, freezer, at(12, 0))
        config = await hass.data["lovelace"].dashboards["plants"].async_load(False)

        for entity_id in _entity_ids(config):
            if entity_id in (STUDY_SWITCH, SPARE_SWITCH):
                continue
            assert hass.states.get(entity_id) is not None, (
                f"card references {entity_id}, which nothing created"
            )


class TestFragments:
    async def test_the_dashboards_cards_are_offered(
        self, integration: HomeAssistant
    ) -> None:
        described = _fragments(integration).describe()[DOMAIN]
        assert [fragment["name"] for fragment in described] == [
            "lamp",
            "overview",
            "plant",
            "plant_detail",
        ]

    async def test_a_persons_overview_is_their_tab(
        self, integration: HomeAssistant
    ) -> None:
        """The same function builds both, so an embedded copy cannot drift."""
        fragment = _fragments(integration).get(DOMAIN, "overview")
        views = await _views(integration)

        assert fragment.render({"person": "nick"}) == views["nick"]["cards"][0]
        assert fragment.render() == views["all"]["cards"][0]

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
