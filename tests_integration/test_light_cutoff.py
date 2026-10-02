"""Cutting a lamp once the plants under it have had enough light.

The cutoff arithmetic is pinned in the unit tests. These cover what only a
running Home Assistant shows: that the switch is actually turned off, that it
stays off for the rest of the day, that a cut survives a restart, and that the
on-time check does not mistake a cut lamp for a dead one.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.const import STATE_ON
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.event import async_track_state_change_event

from custom_components.plant_care.light_control import LightController

from .conftest import (
    DOMAIN,
    LIGHT_CONFIG,
    LUX_1,
    LUX_2,
    MONSTERA_DLI,
    STUDY_ON_MINUTES,
    STUDY_SWITCH,
    at,
    start,
    tick,
)
from .test_lights import record_switch_calls, simulate_lamps, targets
from .test_restart import reloaded_log

OUTSTANDING = "sensor.plant_outstanding"

LAMP_LUX = 50000
"""625 µmol/m²/s through the study lamp's 0.0125: 2.25 mol an hour, so the
monstera's 8.75 cutoff falls a little before 13:00 from a 09:00 start."""


def only_measured_config() -> dict[str, Any]:
    """The study lamp over the monstera alone.

    The shared document also puts the unmeasured alii under it, which is
    exactly the case that must never be cut.
    """
    config = copy.deepcopy(LIGHT_CONFIG)
    for plant in config[DOMAIN]["plants"]:
        if plant["name"] == "ficus_alii":
            plant["lights"] = []
    return config


def lux(hass: HomeAssistant, value: float) -> None:
    hass.states.async_set(LUX_1, str(value))
    hass.states.async_set(LUX_2, str(value))


def lux_follows_the_lamp(hass: HomeAssistant) -> None:
    """A dark room: the sensors read the lamp and nothing else."""

    @callback
    def _follow(event: Event) -> None:
        new = event.data["new_state"]
        lux(hass, LAMP_LUX if new is not None and new.state == STATE_ON else 0)

    async_track_state_change_event(hass, [STUDY_SWITCH], _follow)


async def minutes(hass: HomeAssistant, freezer: FrozenDateTimeFactory, n: int) -> None:
    """One minute at a time, as production samples.

    `tick` steps fifteen, which credits each DLI reading for a quarter of an
    hour — far coarser than the headroom the cut is sized against.
    """
    for _ in range(n):
        await tick(hass, freezer)


def controller(hass: HomeAssistant) -> LightController:
    return hass.data[DOMAIN].light_controllers["study_shelf"]


def kinds(hass: HomeAssistant) -> set[str]:
    return {item["kind"] for item in hass.states.get(OUTSTANDING).attributes["items"]}


async def run_to_the_cut(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> list[Any]:
    simulate_lamps(hass, STUDY_SWITCH)
    lux_follows_the_lamp(hass)
    lux(hass, 0)
    recorded = record_switch_calls(hass)
    await start(hass, freezer, at(9, 0), only_measured_config())
    # To 14:00: an hour past the cut, and 11.25 mol had the lamp stayed on.
    await minutes(hass, freezer, 5 * 60)
    return recorded


class TestTheCut:
    async def test_the_lamp_goes_off_once_every_plant_under_it_has_had_enough(
        self, hass: HomeAssistant, freezer: FrozenDateTimeFactory
    ) -> None:
        recorded = await run_to_the_cut(hass, freezer)

        assert targets(recorded, STUDY_SWITCH) == ["turn_on", "turn_off"]
        cut = controller(hass).enough_at
        assert cut is not None
        assert at(12, 45).time() <= cut <= at(13, 0).time()

    async def test_the_day_lands_at_the_top_of_the_band_not_over_it(
        self, hass: HomeAssistant, freezer: FrozenDateTimeFactory
    ) -> None:
        await run_to_the_cut(hass, freezer)

        assert 8.75 <= float(hass.states.get(MONSTERA_DLI).state) <= 9.0
        assert "light_excess_today" not in kinds(hass)

    async def test_it_stays_off_for_the_rest_of_the_day(
        self, hass: HomeAssistant, freezer: FrozenDateTimeFactory
    ) -> None:
        """The lamp going out drops the light rate, never the total, so there
        is nothing to bring it back on — through the guaranteed stretch and the
        tail alike."""
        recorded = await run_to_the_cut(hass, freezer)

        # To 18:30, inside the tail: nothing else would have turned it off.
        await tick(hass, freezer, minutes=4 * 60 + 30)

        assert targets(recorded, STUDY_SWITCH) == ["turn_on", "turn_off"]

    async def test_a_cut_lamp_is_not_judged_a_dead_one(
        self, hass: HomeAssistant, freezer: FrozenDateTimeFactory
    ) -> None:
        """Off since before one with a window guaranteeing it until five: the
        expectations end at the cut, so four hours short reads as no shortfall
        at all."""
        await run_to_the_cut(hass, freezer)
        await tick(hass, freezer, minutes=3 * 60)

        assert controller(hass).deviation() is None
        state = hass.states.get(STUDY_ON_MINUTES)
        cut = controller(hass).enough_at
        assert state.attributes["enough_light_at"] == cut.isoformat("minutes")
        guaranteed = cut.hour * 60 + cut.minute - 9 * 60
        assert state.attributes["guaranteed_minutes"] == guaranteed
        assert int(state.state) == pytest.approx(guaranteed, abs=2)
        # The day's ceiling is reached: nothing more is coming today.
        assert state.attributes["day_possible_minutes"] == (
            cut.hour * 60 + cut.minute - 6 * 60
        )

    async def test_the_next_day_starts_afresh(
        self, hass: HomeAssistant, freezer: FrozenDateTimeFactory
    ) -> None:
        recorded = await run_to_the_cut(hass, freezer)
        assert controller(hass).enough_at is not None

        await tick(hass, freezer, minutes=19 * 60 + 15)

        assert controller(hass).enough_at is None
        assert targets(recorded, STUDY_SWITCH) == ["turn_on", "turn_off", "turn_on"]


class TestWhenNotToCut:
    async def test_an_unmeasured_plant_under_the_lamp_keeps_it_on(
        self, hass: HomeAssistant, freezer: FrozenDateTimeFactory
    ) -> None:
        """The alii shares the lamp and nothing measures it, so nothing can say
        it has had enough. The monstera running over is the price, and the
        DLI signal is what reports it."""
        simulate_lamps(hass, STUDY_SWITCH)
        lux_follows_the_lamp(hass)
        lux(hass, 0)
        recorded = record_switch_calls(hass)
        await start(hass, freezer, at(9, 0))

        await tick(hass, freezer, minutes=7 * 60)

        assert targets(recorded, STUDY_SWITCH) == ["turn_on"]
        assert controller(hass).enough_at is None

    async def test_a_plant_short_of_its_light_keeps_the_lamp_on(
        self, hass: HomeAssistant, freezer: FrozenDateTimeFactory
    ) -> None:
        simulate_lamps(hass, STUDY_SWITCH)
        lux(hass, 5000)
        recorded = record_switch_calls(hass)
        await start(hass, freezer, at(9, 0), only_measured_config())

        await tick(hass, freezer, minutes=7 * 60)

        assert targets(recorded, STUDY_SWITCH) == ["turn_on"]

    async def test_yesterdays_total_never_counts_for_today(
        self, hass: HomeAssistant, freezer: FrozenDateTimeFactory
    ) -> None:
        """Just past midnight a controller can tick before the plant's light
        has rolled over. Frozen here at yesterday's total for a whole morning,
        it must still read as not enough, or the lamp would be held off all
        day."""
        recorded = await run_to_the_cut(hass, freezer)
        for coordinator in hass.data[DOMAIN].dli_coordinators.values():
            coordinator.async_stop()

        await tick(hass, freezer, minutes=19 * 60 + 15)

        assert controller(hass).enough_at is None
        assert targets(recorded, STUDY_SWITCH) == ["turn_on", "turn_off", "turn_on"]


class TestAcrossARestart:
    async def test_a_cut_lamp_comes_back_cut(
        self, hass: HomeAssistant, freezer: FrozenDateTimeFactory
    ) -> None:
        """From the stamp in the store, not from re-asking the plants: rebuilt
        here with nothing to ask, it still holds."""
        await run_to_the_cut(hass, freezer)
        cut = controller(hass).enough_at
        assert cut is not None
        data = hass.data[DOMAIN]
        for existing in data.light_controllers.values():
            existing.async_stop()
        recorded = record_switch_calls(hass)

        rebuilt = LightController(
            hass, data.config.light("study_shelf"), await reloaded_log(hass)
        )
        await rebuilt.async_start()
        rebuilt.async_apply()
        await hass.async_block_till_done()

        assert rebuilt.enough_at == cut
        assert targets(recorded, STUDY_SWITCH) == []
        assert rebuilt.deviation() is None


class TestTheExcessMessage:
    async def test_daylight_past_the_band_is_not_blamed_on_the_lamp(
        self, hass: HomeAssistant, freezer: FrozenDateTimeFactory
    ) -> None:
        """The lamp did its part; the sun carried on. "Stuck on" would send
        somebody to a lamp that is off."""
        await run_to_the_cut(hass, freezer)

        lux(hass, LAMP_LUX)  # the sun, now, with the lamp off
        await tick(hass, freezer, minutes=60)

        (item,) = [
            item
            for item in hass.states.get(OUTSTANDING).attributes["items"]
            if item["kind"] == "light_excess_today"
        ]
        assert "daylight" in item["detail"]
        assert "stuck on" not in item["detail"]
