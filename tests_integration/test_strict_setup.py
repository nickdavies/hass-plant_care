"""Setup must fail loudly on a config it does not fully understand.

A plant care system that comes up with half its plants missing looks like it is
working, and the failure surfaces weeks later as a plant nobody watered — so
refusing to start is the kinder outcome. These go through
`async_setup_component`, so they also prove the checks run from
`CONFIG_SCHEMA`, which is what `check_config` sees.
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
        """A typo is an error, not a silently ignored field."""
        assert not await _try_setup(hass, _with_first_plant(lightLevel="bright"))

    async def test_an_unknown_probe_model_is_rejected(
        self, hass: HomeAssistant
    ) -> None:
        assert not await _try_setup(hass, _with_first_moisture(model="nope"))

    async def test_a_detectable_task_on_a_calibrated_plant_is_rejected(
        self, hass: HomeAssistant
    ) -> None:
        """The probe already sees a watering; a reminder beside it would be a
        second, worse source of truth."""
        assert not await _try_setup(
            hass,
            _with_first_plant(
                care=[
                    {"task": "feed", "every_days": 14},
                    {"task": "water", "every_days": 4},
                ]
            ),
        )

    async def test_a_half_written_calibration_is_rejected(
        self, hass: HomeAssistant
    ) -> None:
        """Both endpoints or neither. One endpoint cannot produce a threshold
        that is not invented."""
        assert not await _try_setup(
            hass, _with_first_moisture(calibration={"field_capacity": 79.54})
        )

    async def test_an_inverted_calibration_is_rejected(
        self, hass: HomeAssistant
    ) -> None:
        assert not await _try_setup(
            hass,
            _with_first_moisture(
                calibration={"field_capacity": 50.0, "dry_point": 60.0}
            ),
        )

    async def test_a_bare_entity_name_is_rejected(self, hass: HomeAssistant) -> None:
        assert not await _try_setup(
            hass, _with_first_moisture(entities={"moisture": "not_an_entity_id"})
        )

    async def test_a_zero_interval_care_task_is_rejected(
        self, hass: HomeAssistant
    ) -> None:
        assert not await _try_setup(
            hass, _with_first_plant(care=[{"task": "feed", "every_days": 0}])
        )

    async def test_duplicate_plant_names_are_rejected(
        self, hass: HomeAssistant
    ) -> None:
        """Every entity id is built from the name, so a duplicate collides
        silently and the second plant wins."""
        config = copy.deepcopy(TEST_CONFIG)
        config[DOMAIN]["plants"].append(copy.deepcopy(config[DOMAIN]["plants"][0]))
        assert not await _try_setup(hass, config)

    async def test_a_plant_without_an_owner_is_rejected(
        self, hass: HomeAssistant
    ) -> None:
        """No phone to page, no tab to appear on."""
        config = copy.deepcopy(TEST_CONFIG)
        config[DOMAIN]["plants"][0].pop("owner")
        assert not await _try_setup(hass, config)

    async def test_an_unknown_owner_is_rejected(self, hass: HomeAssistant) -> None:
        assert not await _try_setup(hass, _with_first_plant(owner="ghost"))

    async def test_a_group_member_nobody_can_reach_is_rejected(
        self, hass: HomeAssistant
    ) -> None:
        """Every member gets a tab and a sensor, so every member needs an
        action of their own."""
        config = copy.deepcopy(TEST_CONFIG)
        config[DOMAIN]["owners"].pop("britta")
        assert not await _try_setup(hass, config)

    async def test_system_notify_is_required(self, hass: HomeAssistant) -> None:
        config = copy.deepcopy(TEST_CONFIG)
        config[DOMAIN].pop("system_notify")
        assert not await _try_setup(hass, config)

    async def test_the_old_single_notify_key_is_rejected(
        self, hass: HomeAssistant
    ) -> None:
        """Rather than silently ignored — it used to mean something."""
        config = copy.deepcopy(TEST_CONFIG)
        config[DOMAIN]["notify"] = "notify.nick"
        assert not await _try_setup(hass, config)


class TestCalibratingIsExplicit:
    async def test_a_calibrating_plant_sets_up_and_is_monitored(
        self, hass: HomeAssistant
    ) -> None:
        """`calibration: calibrating` is valid, not an error — it means the plant
        can be monitored but not judged."""
        config = copy.deepcopy(TEST_CONFIG)
        assert config[DOMAIN]["plants"][1]["moisture"]["calibration"] == "calibrating"
        assert await _try_setup(hass, config)
        assert hass.states.get("button.plant_monstera_pest_check_done") is not None

    @pytest.mark.parametrize("absent_field", ["species", "care", "moisture"])
    async def test_optional_blocks_may_be_absent(
        self, hass: HomeAssistant, absent_field: str
    ) -> None:
        config = copy.deepcopy(TEST_CONFIG)
        config[DOMAIN]["plants"][0].pop(absent_field, None)
        assert await _try_setup(hass, config)


EMPTY_CONFIG: dict[str, Any] = {
    DOMAIN: {"plants": [], "system_notify": "notify.phones"}
}


class TestTheEmptyConfig:
    """`plants: []` has to set up cleanly.

    Falling over on an empty list would turn "nothing configured yet" into a
    Home Assistant that will not start.
    """

    async def test_it_sets_up(self, hass: HomeAssistant) -> None:
        assert await _try_setup(hass, copy.deepcopy(EMPTY_CONFIG))

    async def test_the_feed_exists_and_is_empty(self, hass: HomeAssistant) -> None:
        """Rather than absent — a consumer reading the feed should find nothing
        outstanding, not an entity that does not exist."""
        assert await _try_setup(hass, copy.deepcopy(EMPTY_CONFIG))

        state = hass.states.get("sensor.plant_outstanding")
        assert state is not None
        assert state.state == "0"
        assert state.attributes["items"] == []

    async def test_the_dashboard_still_renders_with_only_the_shared_tab(
        self, hass: HomeAssistant
    ) -> None:
        assert await _try_setup(hass, copy.deepcopy(EMPTY_CONFIG))

        config = await hass.data["lovelace"].dashboards["plants"].async_load(False)
        assert [view["path"] for view in config["views"]] == ["all"]
