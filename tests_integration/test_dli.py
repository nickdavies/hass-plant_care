"""Measuring light end to end.

The integration arithmetic and the burn rates are pinned in the unit tests.
These cover the parts only a running Home Assistant shows: that the lux fixture
is actually read and averaged, that the conversion factor follows the lamp, that
a finished day reaches the store, and that the alerts land in the feed.
"""

from __future__ import annotations

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.const import STATE_ON
from homeassistant.core import HomeAssistant

from .conftest import (
    DOMAIN,
    LUX_1,
    LUX_2,
    MONSTERA_DLI,
    STUDY_LUX,
    STUDY_SWITCH,
    at,
    start,
    tick,
)

OUTSTANDING = "sensor.plant_outstanding"

SUN_FACTOR = 0.0185
LAMP_FACTOR = 0.0125


def lux(hass: HomeAssistant, first: float | str, second: float | str) -> None:
    hass.states.async_set(LUX_1, str(first))
    hass.states.async_set(LUX_2, str(second))


def dli(hass: HomeAssistant) -> float:
    return float(hass.states.get(MONSTERA_DLI).state)


def mol_per_hour(lux_reading: float, factor: float) -> float:
    return lux_reading * factor * 3600 / 1_000_000


def kinds(hass: HomeAssistant, plant: str) -> set[str]:
    return {
        item["kind"]
        for item in hass.states.get(OUTSTANDING).attributes["items"]
        if item["plant"] == plant
    }


class TestLuxFixture:
    async def test_members_are_averaged_into_one_sensor(
        self, hass: HomeAssistant, freezer: FrozenDateTimeFactory
    ) -> None:
        lux(hass, 10000, 20000)
        await start(hass, freezer, at(12, 0))

        assert float(hass.states.get(STUDY_LUX).state) == pytest.approx(15000.0)

    async def test_one_dead_probe_does_not_take_the_fixture_down(
        self, hass: HomeAssistant, freezer: FrozenDateTimeFactory
    ) -> None:
        """A probe shaded by a single leaf is why these are averaged at all; a
        probe that has dropped out must not read as darkness."""
        lux(hass, 10000, "unavailable")
        await start(hass, freezer, at(12, 0))

        assert float(hass.states.get(STUDY_LUX).state) == pytest.approx(10000.0)

    async def test_the_fixture_goes_quiet_only_when_every_member_is_out(
        self, hass: HomeAssistant, freezer: FrozenDateTimeFactory
    ) -> None:
        lux(hass, "unavailable", "unknown")
        await start(hass, freezer, at(12, 0))

        assert hass.states.get(STUDY_LUX).state in ("unknown", "unavailable")


class TestAccumulation:
    async def test_an_hour_of_daylight_lands_where_the_arithmetic_says(
        self, hass: HomeAssistant, freezer: FrozenDateTimeFactory
    ) -> None:
        """10000 lx at the sun's 0.0185 is 185 µmol/m²/s, and an hour of that is
        0.666 mol/m². If this drifts, every DLI number in the system is wrong by
        the same factor.

        Measured as a difference across the hour rather than as a total, so the
        minute `start` spends bringing the component up is not part of it.
        """
        lux(hass, 10000, 10000)
        await start(hass, freezer, at(10, 0))
        before = dli(hass)

        await tick(hass, freezer, minutes=60)

        assert dli(hass) - before == pytest.approx(
            mol_per_hour(10000, SUN_FACTOR), abs=0.001
        )

    async def test_the_lamps_factor_takes_over_while_the_lamp_is_on(
        self, hass: HomeAssistant, freezer: FrozenDateTimeFactory
    ) -> None:
        """lux→PPFD depends on the spectrum producing the light, so the same
        reading is worth less under this lamp than under the sun. Using one
        scalar all day would over-count every lit hour."""
        lux(hass, 10000, 10000)
        hass.states.async_set(STUDY_SWITCH, STATE_ON)
        await start(hass, freezer, at(10, 0))
        before = dli(hass)

        await tick(hass, freezer, minutes=60)

        assert dli(hass) - before == pytest.approx(
            mol_per_hour(10000, LAMP_FACTOR), abs=0.001
        )

    async def test_darkness_accumulates_nothing(
        self, hass: HomeAssistant, freezer: FrozenDateTimeFactory
    ) -> None:
        lux(hass, 0, 0)
        await start(hass, freezer, at(1, 0))

        await tick(hass, freezer, minutes=120)

        assert float(hass.states.get(MONSTERA_DLI).state) == 0.0

    async def test_the_sensor_carries_the_objective_it_is_judged_against(
        self, hass: HomeAssistant, freezer: FrozenDateTimeFactory
    ) -> None:
        lux(hass, 10000, 10000)
        await start(hass, freezer, at(12, 0))

        attributes = hass.states.get(MONSTERA_DLI).attributes
        assert attributes["category"] == "foliage_tropical"
        assert attributes["preferred_low"] == 4.0
        assert attributes["preferred_high"] == 9.0
        assert attributes["survival_low"] == 2.0
        # Surfaced so a band that differs from its category stays explicable
        # months later rather than looking like a mistake.
        assert attributes["preferred_overridden"] is False


class TestPersistence:
    async def test_todays_progress_is_written_down_so_a_restart_resumes(
        self, hass: HomeAssistant, freezer: FrozenDateTimeFactory
    ) -> None:
        """A day that restarted to zero would read as a severe shortfall and
        burn budget for a failure that never happened."""
        lux(hass, 10000, 10000)
        await start(hass, freezer, at(10, 0))
        await tick(hass, freezer, minutes=60)

        stored = hass.data[DOMAIN].event_log.dli_history("monstera")
        assert stored[at(10, 0).date()] == pytest.approx(dli(hass), abs=0.001)

    async def test_midnight_closes_the_day_into_the_history(
        self, hass: HomeAssistant, freezer: FrozenDateTimeFactory
    ) -> None:
        lux(hass, 10000, 10000)
        await start(hass, freezer, at(23, 0))
        banked = dli(hass)

        await tick(hass, freezer, minutes=120)

        history = hass.data[DOMAIN].event_log.dli_history("monstera")
        # An hour of the 15th, closed out; the 16th started again at zero and
        # has an hour of its own.
        assert history[at(15).date()] == pytest.approx(
            banked + mol_per_hour(10000, SUN_FACTOR), abs=0.01
        )
        assert at(16).date() in history
        assert dli(hass) == pytest.approx(mol_per_hour(10000, SUN_FACTOR), abs=0.01)

        rendered = hass.states.get(MONSTERA_DLI).attributes["history"]
        assert rendered["2026-09-15"] == pytest.approx(history[at(15).date()])


class TestAlerts:
    async def test_a_silent_lux_fixture_is_a_fault_in_its_own_right(
        self, hass: HomeAssistant, freezer: FrozenDateTimeFactory
    ) -> None:
        """Without this the symptom still appears — a day with no readings
        accumulates nothing and burns hard — but it reads as "this plant is in
        the dark", which sends you to the lamp instead of to the sensor."""
        await start(hass, freezer, at(6, 0))

        assert "lux_silent" not in kinds(hass, "monstera")

        await tick(hass, freezer, minutes=7 * 60)

        assert "lux_silent" in kinds(hass, "monstera")

    async def test_a_silent_fixture_suppresses_every_other_light_claim(
        self, hass: HomeAssistant, freezer: FrozenDateTimeFactory
    ) -> None:
        """Nothing measured this plant's light, so nothing derived from the
        measurement is worth saying."""
        await start(hass, freezer, at(6, 0))
        await tick(hass, freezer, minutes=7 * 60)

        assert kinds(hass, "monstera") == {"lux_silent"}

    async def test_an_excess_already_banked_is_reported_before_the_day_is_out(
        self, hass: HomeAssistant, freezer: FrozenDateTimeFactory
    ) -> None:
        """The stuck-on lamp, caught on certainty rather than projection: the
        upper bound has already been passed, so no assumption about the rest of
        the day is needed.

        50000 lx is 925 µmol/m²/s, which clears the 9 mol band in under three
        hours.
        """
        lux(hass, 50000, 50000)
        await start(hass, freezer, at(9, 0))

        await tick(hass, freezer, minutes=180)

        assert "light_excess_today" in kinds(hass, "monstera")

    async def test_a_dim_morning_is_not_yet_a_deficit(
        self, hass: HomeAssistant, freezer: FrozenDateTimeFactory
    ) -> None:
        """Light is not flat across a day, so extrapolating a morning's rate
        would call every sunrise a disaster."""
        lux(hass, 100, 100)
        await start(hass, freezer, at(7, 0))

        await tick(hass, freezer, minutes=120)

        assert "light_deficit_today" not in kinds(hass, "monstera")
