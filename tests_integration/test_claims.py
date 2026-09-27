"""The component's entities claimed with the config bridge, and pinned by it."""

from __future__ import annotations

import copy
import logging
from typing import Any

import pytest
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import CoreState, HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import entity_registry as er
from homeassistant.loader import DATA_CUSTOM_COMPONENTS
from homeassistant.setup import async_setup_component

from .conftest import (
    DOMAIN,
    LIGHT_CONFIG,
    MONSTERA_RAW,
    PASSIONFRUIT_ATTENTION,
    PASSIONFRUIT_BATTERY,
    PASSIONFRUIT_FEED_DONE,
    PASSIONFRUIT_FEED_DUE,
    PASSIONFRUIT_RAW,
    PRESENCE,
    SPARE_SWITCH,
    STUDY_KILLSWITCH,
    STUDY_ON_MINUTES,
    STUDY_SWITCH,
    TEST_CONFIG,
    _ensure_custom_components_path,
    mock_phones,
)


async def _boot(
    hass: HomeAssistant, *, bridge: bool = True, config: dict[str, Any] = TEST_CONFIG
) -> None:
    """A boot: the bridge (if any), then this component, then started.

    The bridge applies claims once Home Assistant has started, as it would
    after every integration and platform has set up.
    """
    hass.states.async_set(PASSIONFRUIT_RAW, "60.0")
    hass.states.async_set(PASSIONFRUIT_BATTERY, "85")
    hass.states.async_set(MONSTERA_RAW, "50.0")
    for entity_id, state in (
        (STUDY_SWITCH, "off"),
        (SPARE_SWITCH, "off"),
        (PRESENCE, "awake"),
    ):
        hass.states.async_set(entity_id, state)
    mock_phones(hass)

    hass.set_state(CoreState.starting)
    hass.data.pop(DATA_CUSTOM_COMPONENTS, None)
    _ensure_custom_components_path()
    if bridge:
        assert await async_setup_component(hass, "config_bridge", {"config_bridge": {}})
    assert await async_setup_component(hass, DOMAIN, config), "setup failed"
    await hass.async_block_till_done()
    hass.set_state(CoreState.running)
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await hass.async_block_till_done()


def claimed(hass: HomeAssistant) -> set[str]:
    # Imported here: the bridge is only importable once
    # `_ensure_custom_components_path` has run.
    from custom_components.config_bridge.lib.claims import claims_for

    return set(claims_for(hass.data, "entities").get(DOMAIN, {}))


async def test_every_entity_is_claimed(hass: HomeAssistant) -> None:
    await _boot(hass)

    registered = {
        entry.entity_id
        for entry in er.async_get(hass).entities.values()
        if entry.platform == DOMAIN
    }
    assert {PASSIONFRUIT_ATTENTION, PASSIONFRUIT_FEED_DUE, PASSIONFRUIT_FEED_DONE} <= (
        registered
    )
    assert claimed(hass) == registered


async def test_ui_changes_are_put_back(hass: HomeAssistant) -> None:
    ar.async_get(hass).async_create("Garage")
    # Registered on an earlier boot, and changed in the UI since.
    registry = er.async_get(hass)
    registry.async_get_or_create(
        "button",
        DOMAIN,
        PASSIONFRUIT_FEED_DONE,
        suggested_object_id=PASSIONFRUIT_FEED_DONE.split(".")[1],
    )
    registry.async_update_entity(
        PASSIONFRUIT_FEED_DONE, area_id="garage", name="Mine", icon="mdi:cat"
    )

    await _boot(hass)

    pinned = registry.async_get(PASSIONFRUIT_FEED_DONE)
    assert (pinned.area_id, pinned.name, pinned.icon) == (None, None, None)


async def test_without_the_bridge_nothing_is_claimed(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING):
        await _boot(hass, bridge=False)

    assert "config_bridge isn't set up" in caplog.text
    assert "config_bridge_claims" not in hass.data


def with_areas(
    config: dict[str, Any], plants: dict[str, str], lights: dict[str, str]
) -> dict[str, Any]:
    """`config` with `area:` added to the named plants and grow lights."""
    config = copy.deepcopy(config)
    for plant in config[DOMAIN]["plants"]:
        if plant["name"] in plants:
            plant["area"] = plants[plant["name"]]
    for light in config[DOMAIN].get("lights", []):
        if light["name"] in lights:
            light["area"] = lights[light["name"]]
    return config


async def test_a_plants_entities_go_in_its_area(hass: HomeAssistant) -> None:
    ar.async_get(hass).async_create("Kitchen")

    await _boot(hass, config=with_areas(TEST_CONFIG, {"passionfruit": "kitchen"}, {}))

    registry = er.async_get(hass)
    for entity_id in (
        PASSIONFRUIT_ATTENTION,
        PASSIONFRUIT_FEED_DUE,
        PASSIONFRUIT_FEED_DONE,
    ):
        assert registry.async_get(entity_id).area_id == "kitchen", entity_id


async def test_a_fixtures_entities_go_in_its_area(hass: HomeAssistant) -> None:
    ar.async_get(hass).async_create("Nick Study")

    await _boot(
        hass, config=with_areas(LIGHT_CONFIG, {}, {"study_shelf": "nick_study"})
    )

    registry = er.async_get(hass)
    assert registry.async_get(STUDY_KILLSWITCH).area_id == "nick_study"
    assert registry.async_get(STUDY_ON_MINUTES).area_id == "nick_study"
