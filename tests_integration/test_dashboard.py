"""The generated Plants dashboard.

Built from the plant list, so adding a plant needs no dashboard edit. These
tests exist mostly to catch the case where a card references an entity the
component never created — which renders as a blank row nobody notices.
"""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant


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
        assert "sensor.plant_outstanding" in cards[0]["content"]

    async def test_every_plant_gets_a_card(self, integration: HomeAssistant) -> None:
        config = (
            await integration.data["lovelace"].dashboards["plants"].async_load(False)
        )
        rendered = str(config)
        for display in ("Passionfruit", "Monstera", "Front step pot"):
            assert display in rendered

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
        config named — which the generator already resolved against the device
        inventory.
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
