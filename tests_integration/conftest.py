"""Integration test fixtures — a real Home Assistant, via
pytest-homeassistant-custom-component.

The unit tests cover the logic; these cover the things only a running Home
Assistant can show: that the entities actually get created with the ids the
naming module promised, that a button press reaches the store and the sensors
notice, and that the config is rejected loudly when it should be.
"""

from __future__ import annotations

import pathlib
from datetime import datetime, timedelta
from typing import Any

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.loader import DATA_CUSTOM_COMPONENTS
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

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

# Probe entities the component reads but never creates. In reality these come
# from zigbee2mqtt via MQTT discovery; the generator resolved their ids.
PASSIONFRUIT_RAW = "sensor.roam_sensor_moisture_1_soil_moisture"
PASSIONFRUIT_BATTERY = "sensor.roam_sensor_moisture_1_battery"
MONSTERA_RAW = "sensor.nick_study_sensor_monstera_window_soil_moisture"


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


# --- Lights, lux and DLI ---------------------------------------------------
# A second document rather than fields bolted onto the first. The tests above
# assert exact feed contents, and a lamp that is off out of hours would add
# items to every one of them.

STUDY_SWITCH = "switch.nick_study_outlet_sansi_100w_lamp"
SPARE_SWITCH = "switch.spare_outlet_grow_lamp_1"
PRESENCE = "sensor.person_presence_nick"
LUX_1 = "sensor.esphome_study_lux_1"
LUX_2 = "sensor.esphome_study_lux_2"

STUDY_KILLSWITCH = "switch.plant_light_killswitch_study_shelf"
SPARE_KILLSWITCH = "switch.plant_light_killswitch_spare_shelf"
STUDY_ON_MINUTES = "sensor.plant_light_study_shelf_on_minutes"
STUDY_LUX = "sensor.plant_lux_study_shelf"
MONSTERA_DLI = "sensor.plant_monstera_dli_today"

LIGHT_CONFIG: dict[str, Any] = {
    DOMAIN: {
        "lights": [
            {
                "name": "study_shelf",
                "source": "nick_study.outlets.sansi_100w_lamp",
                "switch": STUDY_SWITCH,
                "room": "nick_study",
                "luxToPpfd": 0.0125,
                "window": {
                    "mode": "awakeAware",
                    "ifAwakeFrom": "06:00",
                    "noLaterThan": "09:00",
                    "notBefore": "17:00",
                    "until": "19:00",
                    "presence": PRESENCE,
                },
            },
            {
                # Nobody sleeps in here, so a plain window and no presence.
                "name": "spare_shelf",
                "source": "spare.outlets.grow_lamp_1",
                "switch": SPARE_SWITCH,
                "room": "spare",
                "window": {"mode": "fixed", "from": "07:00", "to": "19:00"},
            },
        ],
        "luxSensors": [
            {
                "name": "study_shelf",
                "entities": [LUX_1, LUX_2],
                "sunLuxToPpfd": 0.0185,
            }
        ],
        "plants": [
            {
                # Measured and lit: the full DLI path.
                "name": "monstera",
                "display": "Monstera",
                "lights": ["study_shelf"],
                "lux": "study_shelf",
                "lightSource": "mixed",
                "dli": {
                    "category": "foliage_tropical",
                    "preferred": {"low": 4.0, "high": 9.0},
                    "survival": {"low": 2.0, "high": 20.0},
                    "preferredOverridden": False,
                    "windowDays": 28,
                    "budget": 5.0,
                },
            },
            {
                # Lit but unmeasured: on-time is the only evidence there is.
                "name": "ficus_alii",
                "display": "Ficus alii",
                "lights": ["study_shelf"],
                "lightSource": "grow",
            },
            {
                "name": "spare_fern",
                "display": "Spare fern",
                "lights": ["spare_shelf"],
                "lightSource": "grow",
            },
        ],
    }
}


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
    # Every probe reports before setup. Leaving one out is not a neutral
    # simplification — a probe that has never reported is a *fault*, and the
    # plant correctly shows up in the feed as silent.
    hass.states.async_set(PASSIONFRUIT_RAW, "60.0")
    hass.states.async_set(PASSIONFRUIT_BATTERY, "85")
    hass.states.async_set(MONSTERA_RAW, "50.0")

    hass.data.pop(DATA_CUSTOM_COMPONENTS, None)
    _ensure_custom_components_path()

    assert await async_setup_component(hass, DOMAIN, TEST_CONFIG), "setup failed"
    await hass.async_block_till_done()
    return hass


STEP_MINUTES = 15
"""Biggest jump a single fired tick may stand for.

Two reasons, both about not letting the harness flatter the code.
`async_fire_time_changed` fires an interval tracker once however far the clock
moved, so a twelve-hour jump would give a controller one chance to notice twelve
hours of window edges. And the DLI integrator holds each reading for at most
`MAX_SAMPLE_GAP` — also fifteen minutes — so a coarser step would silently
under-count every integral in these tests.
"""


def at(hour: int, minute: int = 0, day: int = 15) -> datetime:
    """A Tuesday in September, in UTC — which `setup_lights` pins as local."""
    return datetime(2026, 9, day, hour, minute, tzinfo=dt_util.UTC)


async def tick(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, minutes: int = 1
) -> None:
    remaining = minutes
    while remaining > 0:
        freezer.tick(timedelta(minutes=min(remaining, STEP_MINUTES)))
        async_fire_time_changed(hass, dt_util.utcnow())
        await hass.async_block_till_done()
        remaining -= STEP_MINUTES


async def start(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    moment: datetime,
    config: dict[str, Any] | None = None,
) -> None:
    """Set the component up so that it has acted once, at exactly `moment`.

    The tick is not a test artefact. In production the first evaluation waits
    for `EVENT_HOMEASSISTANT_STARTED`, because the switch behind a grow light
    arrives from MQTT discovery some time after this component is set up; here
    Home Assistant is already "running" when the fixture builds it, so that
    event has been and gone and the minute tick is what picks the lamp up. The
    clock is rewound by that minute so tests can do round arithmetic.
    """
    freezer.move_to(moment - timedelta(minutes=1))
    await setup_lights(hass, config)
    await tick(hass, freezer)


async def setup_lights(
    hass: HomeAssistant, config: dict[str, Any] | None = None
) -> HomeAssistant:
    """Set up the light document, with the switches already in the state machine.

    Not a fixture, so a test can move the clock first: what a window does
    depends entirely on what time it is when the controller starts, and half
    these tests are about a specific hour.
    """
    # Windows are local time — a plant's morning is its own morning, not UTC's.
    # The test harness defaults to US/Pacific, which would silently shift every
    # boundary in these tests by seven hours, so it is pinned rather than
    # assumed.
    await hass.config.async_set_time_zone("UTC")

    # Only seeded when the test has not already said otherwise — "the lamp was
    # already on when Home Assistant restarted" is one of the cases under test.
    for entity_id, default in (
        (STUDY_SWITCH, "off"),
        (SPARE_SWITCH, "off"),
        (PRESENCE, "awake"),
    ):
        if hass.states.get(entity_id) is None:
            hass.states.async_set(entity_id, default)

    hass.data.pop(DATA_CUSTOM_COMPONENTS, None)
    _ensure_custom_components_path()

    assert await async_setup_component(hass, DOMAIN, config or LIGHT_CONFIG)
    await hass.async_block_till_done()
    return hass
