"""Health checks end to end.

The durations are covered exhaustively in the unit tests, where time is
injected. These cover what only a running Home Assistant shows: that battery is
read from its own entity, that faults reach the feed, and that a plant with no
probe is not quietly reported as broken.
"""

from __future__ import annotations

from datetime import timedelta

from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant

from .conftest import (
    MONSTERA_RAW,
    PASSIONFRUIT_BATTERY,
    PASSIONFRUIT_FEED_DONE,
    PASSIONFRUIT_RAW,
    press,
    probe_reports,
)

OUTSTANDING = "sensor.plant_outstanding"
PASSIONFRUIT_ATTENTION = "sensor.plant_passionfruit_attention"


def items_for(hass: HomeAssistant, plant: str) -> list[dict]:
    return [
        item
        for item in hass.states.get(OUTSTANDING).attributes["items"]
        if item["plant"] == plant
    ]


def kinds_for(hass: HomeAssistant, plant: str) -> set[str]:
    return {item["kind"] for item in items_for(hass, plant)}


class TestHealthyIsQuiet:
    async def test_a_reporting_plant_raises_nothing(
        self, integration: HomeAssistant
    ) -> None:
        assert kinds_for(integration, "passionfruit") == set()

    async def test_a_plant_with_no_probe_raises_nothing(
        self, integration: HomeAssistant
    ) -> None:
        """No probe is not a fault — an outdoor pot is a first-class case, and
        reporting it as a broken sensor would be noise forever."""
        assert kinds_for(integration, "front_step_pot") == set()


class TestBattery:
    """Read from its own entity, so it needs a running Home Assistant to test."""

    async def test_a_healthy_battery_is_quiet(self, integration: HomeAssistant) -> None:
        assert "battery_low" not in kinds_for(integration, "passionfruit")

    async def test_a_low_battery_reaches_the_feed(
        self, integration: HomeAssistant
    ) -> None:
        integration.states.async_set(PASSIONFRUIT_BATTERY, "9")
        await probe_reports(integration, PASSIONFRUIT_RAW, 60.1)

        assert "battery_low" in kinds_for(integration, "passionfruit")

    async def test_an_unavailable_battery_is_not_a_low_battery(
        self, integration: HomeAssistant
    ) -> None:
        """Unknown is not zero. Treating a dropout as a flat battery would cry
        wolf every time zigbee2mqtt restarted."""
        integration.states.async_set(PASSIONFRUIT_BATTERY, "unavailable")
        await probe_reports(integration, PASSIONFRUIT_RAW, 60.1)

        assert "battery_low" not in kinds_for(integration, "passionfruit")

    async def test_the_remedy_is_in_the_message(
        self, integration: HomeAssistant
    ) -> None:
        integration.states.async_set(PASSIONFRUIT_BATTERY, "9")
        await probe_reports(integration, PASSIONFRUIT_RAW, 60.1)

        (item,) = [
            i
            for i in items_for(integration, "passionfruit")
            if i["kind"] == "battery_low"
        ]
        assert "Replace it" in item["detail"]
        assert item["value"] == 9.0


class TestSilentProbe:
    async def test_silence_past_the_window_reaches_the_feed(
        self, integration: HomeAssistant, freezer: FrozenDateTimeFactory
    ) -> None:
        assert "probe_silent" not in kinds_for(integration, "monstera")

        # The passionfruit keeps reporting; the monstera stops. Only one should
        # be reported.
        for _ in range(20):
            freezer.tick(timedelta(minutes=10))
            await probe_reports(integration, PASSIONFRUIT_RAW, 60.0)

        assert "probe_silent" in kinds_for(integration, "monstera")
        assert "probe_silent" not in kinds_for(integration, "passionfruit")

    async def test_it_clears_when_the_probe_returns(
        self, integration: HomeAssistant, freezer: FrozenDateTimeFactory
    ) -> None:
        for _ in range(20):
            freezer.tick(timedelta(minutes=10))
            await probe_reports(integration, PASSIONFRUIT_RAW, 60.0)
        assert "probe_silent" in kinds_for(integration, "monstera")

        await probe_reports(integration, MONSTERA_RAW, 51.0)

        assert "probe_silent" not in kinds_for(integration, "monstera")


class TestWateringShortfall:
    async def test_a_watering_that_falls_short_reaches_the_feed(
        self, integration: HomeAssistant, freezer: FrozenDateTimeFactory
    ) -> None:
        # A watering: a rise well past a quarter of the calibrated span.
        await probe_reports(integration, PASSIONFRUIT_RAW, 75.0)
        assert "watering_shortfall" not in kinds_for(integration, "passionfruit")

        # An hour later it has settled well below the 71.54% it should reach.
        freezer.tick(timedelta(minutes=61))
        await probe_reports(integration, PASSIONFRUIT_RAW, 62.0)

        assert "watering_shortfall" in kinds_for(integration, "passionfruit")

    async def test_a_watering_that_settles_high_is_quiet(
        self, integration: HomeAssistant, freezer: FrozenDateTimeFactory
    ) -> None:
        await probe_reports(integration, PASSIONFRUIT_RAW, 79.0)
        freezer.tick(timedelta(minutes=61))
        await probe_reports(integration, PASSIONFRUIT_RAW, 76.0)

        assert "watering_shortfall" not in kinds_for(integration, "passionfruit")


class TestFeedOrdering:
    async def test_faults_come_before_care_items(
        self, integration: HomeAssistant, freezer: FrozenDateTimeFactory
    ) -> None:
        """A silent probe means nothing else about the plant can be believed, so
        it should not be buried under overdue feedings."""
        integration.states.async_set(PASSIONFRUIT_BATTERY, "5")

        # Mark feeding done, then let it go overdue. A task never marked done is
        # deliberately not overdue, so it has to be done once first.
        await press(integration, PASSIONFRUIT_FEED_DONE)
        freezer.tick(timedelta(days=20))
        await probe_reports(integration, PASSIONFRUIT_RAW, 60.5)

        kinds = [item["kind"] for item in items_for(integration, "passionfruit")]
        assert "care" in kinds
        assert kinds.index("battery_low") < kinds.index("care")
