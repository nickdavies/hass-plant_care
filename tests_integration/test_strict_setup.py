"""Setup must fail loudly on a config it does not fully understand.

The document is machine-generated, so anything surprising means this component
and the generator have diverged. A plant care system that comes up with half its
plants missing looks like it is working, and the failure surfaces weeks later as
a plant nobody watered — so refusing to start is the kinder outcome.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.loader import DATA_CUSTOM_COMPONENTS
from homeassistant.setup import async_setup_component

from .conftest import DOMAIN, TEST_CONFIG, _ensure_custom_components_path


async def _try_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    hass.data.pop(DATA_CUSTOM_COMPONENTS, None)
    _ensure_custom_components_path()
    result = await async_setup_component(hass, DOMAIN, config)
    await hass.async_block_till_done()
    return result


def _with_first_plant(**changes: Any) -> dict[str, Any]:
    config = copy.deepcopy(TEST_CONFIG)
    config[DOMAIN]["plants"][0].update(changes)
    return config


def _with_first_moisture(**changes: Any) -> dict[str, Any]:
    config = copy.deepcopy(TEST_CONFIG)
    config[DOMAIN]["plants"][0]["moisture"].update(changes)
    return config


class TestRejectsBadConfig:
    async def test_a_valid_config_sets_up(self, hass: HomeAssistant) -> None:
        """The control: everything below differs from this by one field."""
        assert await _try_setup(hass, copy.deepcopy(TEST_CONFIG))

    async def test_an_unknown_key_is_rejected(self, hass: HomeAssistant) -> None:
        """A key this version does not understand means the generator emitted
        something newer."""
        assert not await _try_setup(hass, _with_first_plant(lightLevel="bright"))

    async def test_a_half_written_calibration_is_rejected(
        self, hass: HomeAssistant
    ) -> None:
        """Both endpoints or neither. One endpoint cannot produce a threshold
        that is not invented."""
        assert not await _try_setup(
            hass, _with_first_moisture(calibration={"fieldCapacity": 79.54})
        )

    async def test_an_inverted_calibration_is_rejected(
        self, hass: HomeAssistant
    ) -> None:
        assert not await _try_setup(
            hass,
            _with_first_moisture(calibration={"fieldCapacity": 50.0, "dryPoint": 60.0}),
        )

    async def test_a_bare_entity_name_is_rejected(self, hass: HomeAssistant) -> None:
        assert not await _try_setup(
            hass, _with_first_moisture(entities={"moisture": "not_an_entity_id"})
        )

    async def test_a_zero_interval_care_task_is_rejected(
        self, hass: HomeAssistant
    ) -> None:
        assert not await _try_setup(
            hass,
            _with_first_plant(
                care=[
                    {
                        "task": "feed",
                        "display": "Feed",
                        "icon": "mdi:nutrition",
                        "everyDays": 0,
                    }
                ]
            ),
        )

    async def test_duplicate_plant_names_are_rejected(
        self, hass: HomeAssistant
    ) -> None:
        """Every entity id is built from the name, so a duplicate collides
        silently and the second plant wins."""
        config = copy.deepcopy(TEST_CONFIG)
        config[DOMAIN]["plants"].append(copy.deepcopy(config[DOMAIN]["plants"][0]))
        assert not await _try_setup(hass, config)


class TestAbsentMeansCalibrating:
    """The one contract this component and the generator must agree on."""

    async def test_a_calibrating_plant_sets_up_and_is_monitored(
        self, hass: HomeAssistant
    ) -> None:
        """No calibration block is valid, not an error — it means the plant can
        be monitored but not judged."""
        config = copy.deepcopy(TEST_CONFIG)
        assert "calibration" not in config[DOMAIN]["plants"][1]["moisture"]
        assert await _try_setup(hass, config)
        assert hass.states.get("button.plant_monstera_pest_check_done") is not None

    @pytest.mark.parametrize("absent_field", ["species", "care", "moisture"])
    async def test_optional_blocks_may_be_absent(
        self, hass: HomeAssistant, absent_field: str
    ) -> None:
        config = copy.deepcopy(TEST_CONFIG)
        config[DOMAIN]["plants"][0].pop(absent_field, None)
        assert await _try_setup(hass, config)


class TestTheEmptyDocument:
    """`plants: []` is what hass-configs' CI writes in place of the generated
    document, so this exact shape has to set up cleanly.

    It is also the first thing the cluster sees: the ConfigMap exists before any
    plant is in it. Falling over on an empty list would turn "nothing configured
    yet" into a Home Assistant that will not start.
    """

    async def test_it_sets_up(self, hass: HomeAssistant) -> None:
        assert await _try_setup(hass, {DOMAIN: {"plants": []}})

    async def test_the_feed_exists_and_is_empty(self, hass: HomeAssistant) -> None:
        """Rather than absent — a consumer reading the feed should find nothing
        outstanding, not an entity that does not exist."""
        assert await _try_setup(hass, {DOMAIN: {"plants": []}})

        state = hass.states.get("sensor.plant_outstanding")
        assert state is not None
        assert state.state == "0"
        assert state.attributes["items"] == []

    async def test_the_dashboard_still_renders(self, hass: HomeAssistant) -> None:
        assert await _try_setup(hass, {DOMAIN: {"plants": []}})

        config = await hass.data["lovelace"].dashboards["plants"].async_load(False)
        assert config["views"]
