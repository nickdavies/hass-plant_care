"""The generated Plants dashboard.

Built from the plant list, so adding a plant needs no dashboard edit. One tab
for everything and one per person. These tests exist mostly to catch the case
where a card references an entity the component never created — which renders
as a blank row nobody notices.
"""

from __future__ import annotations

from typing import Any

from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.loader import DATA_CUSTOM_COMPONENTS
from homeassistant.setup import async_setup_component

from custom_components.plant_care.model.owners import DEFAULT_PERSON_ICON

from .conftest import (
    DOMAIN,
    SPARE_SWITCH,
    STUDY_KILLSWITCH,
    STUDY_ON_MINUTES,
    STUDY_SWITCH,
    _ensure_custom_components_path,
    at,
    mock_phones,
    start,
)


async def _views(hass: HomeAssistant) -> dict[str, dict[str, Any]]:
    """The rendered tabs, by path."""
    config = await hass.data["lovelace"].dashboards["plants"].async_load(False)
    return {view["path"]: view for view in config["views"]}


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

    async def test_it_renders(self, integration: HomeAssistant) -> None:
        config = (
            await integration.data["lovelace"].dashboards["plants"].async_load(False)
        )
        assert config["views"]


class TestContent:
    async def test_the_overview_leads(self, integration: HomeAssistant) -> None:
        """Opening the dashboard should answer "what needs me" before showing
        any individual plant."""
        config = (
            await integration.data["lovelace"].dashboards["plants"].async_load(False)
        )
        cards = config["views"][0]["cards"][0]["cards"]
        assert cards[0]["type"] == "markdown"
        assert "Needs attention" in cards[0]["content"]
        assert "sensor.plant_outstanding'" in cards[0]["content"]

    async def test_the_overview_reads_the_rendering_rather_than_the_items(
        self, integration: HomeAssistant
    ) -> None:
        """The household dashboards show the same list, so the Jinja that knows
        what an item looks like lives on the sensor, not in each card."""
        config = (
            await integration.data["lovelace"].dashboards["plants"].async_load(False)
        )
        content = config["views"][0]["cards"][0]["cards"][0]["content"]
        assert "'markdown'" in content
        assert "item.label" not in content

    async def test_every_plant_gets_a_card(self, integration: HomeAssistant) -> None:
        views = await _views(integration)
        rendered = str(views["all"])
        for display in ("Passionfruit", "Monstera", "Front step pot"):
            assert display in rendered


class TestPersonTabs:
    async def test_one_tab_per_person_after_the_shared_one(
        self, integration: HomeAssistant
    ) -> None:
        """People only: a group's members each have a tab."""
        config = (
            await integration.data["lovelace"].dashboards["plants"].async_load(False)
        )
        assert [view["path"] for view in config["views"]] == ["all", "nick", "britta"]
        assert [view["title"] for view in config["views"]] == ["All", "Nick", "Britta"]

    async def test_a_tab_wears_the_icon_its_person_chose(
        self, integration: HomeAssistant
    ) -> None:
        """`britta` chose none, so she gets the default rather than no icon."""
        views = await _views(integration)
        assert views["nick"]["icon"] == "mdi:human-male"
        assert views["britta"]["icon"] == DEFAULT_PERSON_ICON

    async def test_a_tab_shows_owned_and_group_plants_only(
        self, integration: HomeAssistant
    ) -> None:
        views = await _views(integration)

        nick = str(views["nick"])
        assert "Passionfruit" in nick
        assert "Monstera" in nick
        assert "Front step pot" not in nick

        britta = str(views["britta"])
        assert "Front step pot" in britta
        assert "Monstera" in britta
        assert "Passionfruit" not in britta

    async def test_a_tab_leads_with_that_persons_feed(
        self, integration: HomeAssistant
    ) -> None:
        views = await _views(integration)
        cards = views["nick"]["cards"][0]["cards"]
        assert cards[0]["type"] == "markdown"
        assert "sensor.plant_outstanding_nick" in cards[0]["content"]

    async def test_a_tab_only_shows_the_lamps_over_its_plants(
        self, hass: HomeAssistant, freezer: FrozenDateTimeFactory
    ) -> None:
        await start(hass, freezer, at(12, 0))
        views = await _views(hass)

        assert "Study Shelf lamp" in str(views["nick"])
        assert "Spare Shelf lamp" not in str(views["nick"])
        assert "Spare Shelf lamp" in str(views["britta"])
        assert "Study Shelf lamp" not in str(views["britta"])

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


class TestViewShape:
    async def test_a_calibrating_plant_says_so_on_its_card(
        self, integration: HomeAssistant
    ) -> None:
        """The reason lives on the dashboard rather than only in a config
        comment nobody reading the dashboard will see."""
        config = (
            await integration.data["lovelace"].dashboards["plants"].async_load(False)
        )
        assert "Monstera — calibrating" in str(config)

    async def test_a_calibrating_plant_shows_no_available_water_row(
        self, integration: HomeAssistant
    ) -> None:
        """There is no scale to express it on without both endpoints."""
        config = (
            await integration.data["lovelace"].dashboards["plants"].async_load(False)
        )
        assert "sensor.plant_monstera_available_water" not in str(config)

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
            assert entity_id.split(".")[1].startswith("plant_"), (
                f"card references {entity_id}, which is neither a probe entity "
                "from the config nor something this component creates"
            )


class TestLightContent:
    async def test_each_fixture_gets_a_card(
        self, hass: HomeAssistant, freezer: FrozenDateTimeFactory
    ) -> None:
        await start(hass, freezer, at(12, 0))
        config = await hass.data["lovelace"].dashboards["plants"].async_load(False)
        rendered = str(config)

        assert "Study Shelf lamp" in rendered
        assert "Spare Shelf lamp" in rendered

    async def test_the_killswitch_sits_next_to_the_on_time_it_affects(
        self, hass: HomeAssistant, freezer: FrozenDateTimeFactory
    ) -> None:
        """Freezing a fixture is a decision about the plants under it, and the
        on-time figure is the only thing here that says what it actually did."""
        await start(hass, freezer, at(12, 0))
        config = await hass.data["lovelace"].dashboards["plants"].async_load(False)

        ids = _entity_ids(config)
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
        cards = views["all"]["cards"][0]["cards"]
        by_title = {card.get("title"): card for card in cards}

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

    async def test_a_measured_plant_shows_its_band_on_the_row(
        self, hass: HomeAssistant, freezer: FrozenDateTimeFactory
    ) -> None:
        await start(hass, freezer, at(12, 0))
        config = await hass.data["lovelace"].dashboards["plants"].async_load(False)

        assert "DLI today (want 4–9)" in str(config)

    async def test_an_unmeasured_plant_gets_no_dli_row(
        self, hass: HomeAssistant, freezer: FrozenDateTimeFactory
    ) -> None:
        """Derived from which fields are present, not from a feature flag."""
        await start(hass, freezer, at(12, 0))
        config = await hass.data["lovelace"].dashboards["plants"].async_load(False)

        assert "sensor.plant_ficus_alii_dli_today" not in str(config)

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
