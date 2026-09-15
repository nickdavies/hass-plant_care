"""Integration test fixtures — a real Home Assistant, via
pytest-homeassistant-custom-component.

The unit tests cover the logic; these cover the things only a running Home
Assistant can show: that the entities actually get created with the ids the
naming module promised, that a button press reaches the store and the sensors
notice, and that the config is rejected loudly when it should be.
"""

from __future__ import annotations

import pathlib
from typing import Any

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.loader import DATA_CUSTOM_COMPONENTS
from homeassistant.setup import async_setup_component

DOMAIN = "plant_care"

# Shaped exactly like the generator's output. Kept verbatim rather than
# minimised, because the contract being tested is with that document — a
# simplified fixture would stop catching a divergence between the two.
TEST_CONFIG: dict[str, Any] = {
    DOMAIN: {
        "plants": [
            {
                "name": "passionfruit",
                "display": "Passionfruit",
                "species": "passiflora edulis",
                "moisture": {
                    "source": "roam.sensors.moisture_1",
                    "entities": {
                        "moisture": "sensor.roam_sensor_moisture_1_soil_moisture",
                        "temperature": "sensor.roam_sensor_moisture_1_temperature",
                        "battery": "sensor.roam_sensor_moisture_1_battery",
                    },
                    "probe": {"heartbeatMinutes": 10, "deadbandPp": 1.0},
                    "calibration": {
                        "fieldCapacity": 79.54,
                        "dryPoint": 53.18,
                        "fcTolerance": 8.0,
                    },
                },
                "care": [
                    {
                        "task": "feed",
                        "display": "Feed",
                        "icon": "mdi:nutrition",
                        "everyDays": 14,
                    }
                ],
            },
            {
                # Calibrating: monitored, but no threshold exists.
                "name": "monstera",
                "display": "Monstera",
                "moisture": {
                    "source": "nick_study.sensors.monstera_window",
                    "entities": {
                        "moisture": "sensor.nick_study_sensor_monstera_window_soil_moisture"
                    },
                    "probe": {"heartbeatMinutes": 10, "deadbandPp": 1.0},
                },
                "care": [
                    {
                        "task": "pest_check",
                        "display": "Pest check",
                        "icon": "mdi:bug-outline",
                        "everyDays": 7,
                    }
                ],
            },
            {
                # No sensors at all — an outdoor pot. A first-class case.
                "name": "front_step_pot",
                "display": "Front step pot",
                "care": [
                    {
                        "task": "water",
                        "display": "Water",
                        "icon": "mdi:watering-can",
                        "everyDays": 4,
                    }
                ],
            },
        ]
    }
}

# --- Entity ids under test -------------------------------------------------
# Spelled out rather than built with `naming`, on purpose: if these are derived
# the same way the component derives them, a change to the naming scheme would
# move both and the test would keep passing while every dashboard broke.

PASSIONFRUIT_FEED_DAYS = "sensor.plant_passionfruit_feed_days_since"
PASSIONFRUIT_FEED_DUE = "binary_sensor.plant_passionfruit_feed_due"
PASSIONFRUIT_FEED_DONE = "button.plant_passionfruit_feed_done"
PASSIONFRUIT_ATTENTION = "sensor.plant_passionfruit_attention"

MONSTERA_PEST_DUE = "binary_sensor.plant_monstera_pest_check_due"
MONSTERA_PEST_DONE = "button.plant_monstera_pest_check_done"

POT_WATER_DONE = "button.plant_front_step_pot_water_done"

OUTSTANDING = "sensor.plant_outstanding"

# The probe entity the component reads but never creates.
PASSIONFRUIT_RAW = "sensor.roam_sensor_moisture_1_soil_moisture"


async def press(hass: HomeAssistant, entity_id: str) -> None:
    await hass.services.async_call(
        "button", "press", {"entity_id": entity_id}, blocking=True
    )
    await hass.async_block_till_done()


async def probe_reports(hass: HomeAssistant, entity_id: str, value: float) -> None:
    """Stand in for zigbee2mqtt publishing a reading.

    Set through the state machine rather than by calling the coordinator, so the
    subscription itself is part of what is being tested.
    """
    hass.states.async_set(entity_id, str(value))
    await hass.async_block_till_done()


def _ensure_custom_components_path() -> None:
    """Put this project's custom_components on the namespace path.

    pytest-homeassistant-custom-component points the `custom_components`
    namespace package at its own testing_config directory, so without this the
    component under test is invisible.
    """
    import custom_components

    project_cc = str(pathlib.Path(__file__).parent.parent / "custom_components")
    if project_cc not in custom_components.__path__:
        custom_components.__path__.insert(0, project_cc)


@pytest.fixture
async def integration(hass: HomeAssistant) -> HomeAssistant:
    """Set up the component with the fixture config."""
    # The probe entities belong to zigbee2mqtt in reality; here they just need
    # to exist so nothing reads an entity that was never created.
    hass.states.async_set(PASSIONFRUIT_RAW, "60.0")

    hass.data.pop(DATA_CUSTOM_COMPONENTS, None)
    _ensure_custom_components_path()

    assert await async_setup_component(hass, DOMAIN, TEST_CONFIG), "setup failed"
    await hass.async_block_till_done()
    return hass
