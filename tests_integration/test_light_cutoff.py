"""Cutting a lamp once the plants under it have had enough light.

The cutoff arithmetic is pinned in the unit tests. These cover what only a
running Home Assistant shows: that the switch is actually turned off, that it
stays off for the rest of the day, that a cut survives a restart, and that the
on-time check does not mistake a cut lamp for a dead one.
"""

from __future__ import annotations

import copy
from datetime import timedelta
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


async def normal(hass: HomeAssistant, minutes_a_day: float, days: int = 3) -> None:
    """Give the study lamp a history of ordinary days before today."""
    log = hass.data[DOMAIN].event_log
    today = at(0, 0).date()
    for back in range(days, 0, -1):
        await log.async_record_on_time_day(
            "study_shelf", today - timedelta(days=back), minutes_a_day, keep_days=14
        )


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
    # A normal of 400 minutes trusts a cut from 200: well before this one, so
    # these tests are about the cut and not the sanity check on it.
    await normal(hass, 400)
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


BLINDING_LUX = 200000
"""2500 µmol/m²/s through the lamp's factor: 9 mol an hour, so the monstera
reads as having had enough before ten. A probe in direct sun, or a factor ten
times too high."""


class TestTheSanityCheck:
    """A cut is only as good as the reading behind it."""

    async def run_blinded(
        self, hass: HomeAssistant, freezer: FrozenDateTimeFactory
    ) -> list[Any]:
        simulate_lamps(hass, STUDY_SWITCH)
        lux(hass, BLINDING_LUX)
        recorded = record_switch_calls(hass)
        await start(hass, freezer, at(9, 0), only_measured_config())
        # Normal 600, so nothing is cut before 300 minutes: 14:00.
        await normal(hass, 600)
        return recorded

    async def test_a_cut_far_under_normal_is_held_back(
        self, hass: HomeAssistant, freezer: FrozenDateTimeFactory
    ) -> None:
        recorded = await self.run_blinded(hass, freezer)

        await tick(hass, freezer, minutes=4 * 60)

        assert targets(recorded, STUDY_SWITCH) == ["turn_on"]
        assert controller(hass).enough_at is None
        held = controller(hass).held_at
        assert held is not None
        assert held <= at(10, 0).time()

    async def test_it_is_cut_once_the_floor_is_reached(
        self, hass: HomeAssistant, freezer: FrozenDateTimeFactory
    ) -> None:
        """Held, not overruled: the reading still says enough, and past half of
        normal there is no reason left to doubt it."""
        recorded = await self.run_blinded(hass, freezer)

        await minutes(hass, freezer, 5 * 60 + 5)

        assert targets(recorded, STUDY_SWITCH) == ["turn_on", "turn_off"]
        assert controller(hass).enough_at == at(14, 0).time()

    async def test_holding_it_back_is_said_once_to_the_system_feed(
        self, hass: HomeAssistant, freezer: FrozenDateTimeFactory
    ) -> None:
        await self.run_blinded(hass, freezer)
        await tick(hass, freezer, minutes=60)

        items = [
            item
            for item in hass.states.get(OUTSTANDING).attributes["items"]
            if item["kind"] == "light_cut_distrusted"
        ]
        assert len(items) == 1
        assert items[0]["plant"] is None
        assert items[0]["fixture"] == "study_shelf"
        assert "normal 600" in items[0]["detail"]

        state = hass.states.get(STUDY_ON_MINUTES)
        assert state.attributes["normal_minutes"] == 600
        assert state.attributes["cut_floor_minutes"] == 300
        assert state.attributes["cut_held_at"] is not None

    async def test_it_is_gone_the_next_day(
        self, hass: HomeAssistant, freezer: FrozenDateTimeFactory
    ) -> None:
        await self.run_blinded(hass, freezer)
        await tick(hass, freezer, minutes=60)
        lux(hass, 0)

        await tick(hass, freezer, minutes=15 * 60 + 15)

        assert controller(hass).held_at is None
        assert "light_cut_distrusted" not in kinds(hass)

    async def test_a_held_cut_comes_back_held(
        self, hass: HomeAssistant, freezer: FrozenDateTimeFactory
    ) -> None:
        await self.run_blinded(hass, freezer)
        await tick(hass, freezer, minutes=60)
        held = controller(hass).held_at
        data = hass.data[DOMAIN]
        for existing in data.light_controllers.values():
            existing.async_stop()

        rebuilt = LightController(
            hass, data.config.light("study_shelf"), await reloaded_log(hass)
        )
        await rebuilt.async_start()

        assert rebuilt.held_at == held
        assert rebuilt.distrusted_issue() is not None

    async def test_without_history_the_window_is_normal(
        self, hass: HomeAssistant, freezer: FrozenDateTimeFactory
    ) -> None:
        """The study window guarantees 09:00-17:00."""
        await start(hass, freezer, at(9, 0), only_measured_config())

        assert controller(hass).normal_minutes() == 480
        assert controller(hass).cut_floor_minutes() == 240


class TestNormal:
    async def test_a_whole_day_is_banked_at_midnight(
        self, hass: HomeAssistant, freezer: FrozenDateTimeFactory
    ) -> None:
        simulate_lamps(hass, STUDY_SWITCH)
        await start(hass, freezer, at(0, 0))

        await tick(hass, freezer, minutes=24 * 60 + 15)

        days = hass.data[DOMAIN].event_log.on_time_days("study_shelf")
        # 06:00-19:00 with nobody asleep.
        assert days[at(0, 0).date()] == pytest.approx(13 * 60, abs=2)

    async def test_a_day_that_started_part_way_through_is_not(
        self, hass: HomeAssistant, freezer: FrozenDateTimeFactory
    ) -> None:
        """Counted from a 09:00 start-up it is three hours short, and banked it
        would drag normal down with it."""
        simulate_lamps(hass, STUDY_SWITCH)
        await start(hass, freezer, at(9, 0))

        await tick(hass, freezer, minutes=15 * 60 + 15)

        assert hass.data[DOMAIN].event_log.on_time_days("study_shelf") == {}

    async def test_a_day_that_ended_while_down_is_banked_on_the_way_up(
        self, hass: HomeAssistant, freezer: FrozenDateTimeFactory
    ) -> None:
        """Down from 20:00 to the next morning: the window allowed nothing
        after the last record, so it is the whole day."""
        simulate_lamps(hass, STUDY_SWITCH)
        await start(hass, freezer, at(0, 0))
        await tick(hass, freezer, minutes=20 * 60)
        data = hass.data[DOMAIN]
        for existing in data.light_controllers.values():
            existing.async_stop()

        freezer.move_to(at(7, 0, day=16))
        log = await reloaded_log(hass)
        rebuilt = LightController(hass, data.config.light("study_shelf"), log)
        await rebuilt.async_start()
        await hass.async_block_till_done()

        days = log.on_time_days("study_shelf")
        assert days[at(0, 0).date()] == pytest.approx(13 * 60, abs=2)

    async def test_a_day_cut_off_by_going_down_mid_window_is_not(
        self, hass: HomeAssistant, freezer: FrozenDateTimeFactory
    ) -> None:
        simulate_lamps(hass, STUDY_SWITCH)
        await start(hass, freezer, at(0, 0))
        await tick(hass, freezer, minutes=12 * 60)
        data = hass.data[DOMAIN]
        for existing in data.light_controllers.values():
            existing.async_stop()

        freezer.move_to(at(7, 0, day=16))
        log = await reloaded_log(hass)
        rebuilt = LightController(hass, data.config.light("study_shelf"), log)
        await rebuilt.async_start()
        await hass.async_block_till_done()

        assert log.on_time_days("study_shelf") == {}
