"""The moisture layer end to end.

The confirm window and the median are covered exhaustively in the unit tests,
where time is injected. These cover what only a running Home Assistant can show:
that probe state changes actually reach the monitor, that the entities exist
where the naming module says, and that a detected watering is persisted.
"""

from __future__ import annotations

from datetime import timedelta

from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant

from .conftest import PASSIONFRUIT_RAW, probe_reports

SMOOTHED = "sensor.plant_passionfruit_moisture_smoothed"
RISE = "sensor.plant_passionfruit_moisture_rise"
AVAILABLE = "sensor.plant_passionfruit_available_water"
DAYS_WATERED = "sensor.plant_passionfruit_days_since_watered"
NEEDS_WATER = "binary_sensor.plant_passionfruit_needs_water"
OUTSTANDING = "sensor.plant_outstanding"

MONSTERA_SMOOTHED = "sensor.plant_monstera_moisture_smoothed"
MONSTERA_AVAILABLE = "sensor.plant_monstera_available_water"
MONSTERA_NEEDS_WATER = "binary_sensor.plant_monstera_needs_water"


class TestWhichEntitiesExist:
    """Derived from whether the plant is calibrated — no feature flags."""

    async def test_a_calibrated_plant_gets_the_full_set(
        self, integration: HomeAssistant
    ) -> None:
        for entity_id in (SMOOTHED, RISE, AVAILABLE, DAYS_WATERED, NEEDS_WATER):
            assert integration.states.get(entity_id) is not None, f"{entity_id} missing"

    async def test_a_calibrating_plant_is_monitored_but_not_judged(
        self, integration: HomeAssistant
    ) -> None:
        """Signals yes, thresholds no — any threshold would be invented."""
        assert integration.states.get(MONSTERA_SMOOTHED) is not None
        assert integration.states.get(MONSTERA_AVAILABLE) is None
        assert integration.states.get(MONSTERA_NEEDS_WATER) is None

    async def test_a_sensorless_plant_gets_no_moisture_entities(
        self, integration: HomeAssistant
    ) -> None:
        assert (
            integration.states.get("sensor.plant_front_step_pot_moisture_smoothed")
            is None
        )


class TestReadingsFlowThrough:
    async def test_the_probe_seeds_from_existing_state(
        self, integration: HomeAssistant
    ) -> None:
        """A reload should not sit blank until the next heartbeat — the
        coordinator reads whatever the probe is already reporting."""
        assert integration.states.get(SMOOTHED).state == "60.0"

    async def test_a_new_reading_moves_the_median(
        self, integration: HomeAssistant
    ) -> None:
        await probe_reports(integration, PASSIONFRUIT_RAW, 70.0)
        # Median of 60 and 70.
        assert integration.states.get(SMOOTHED).state == "65.0"

    async def test_available_water_uses_the_calibrated_scale(
        self, integration: HomeAssistant
    ) -> None:
        """0% at the measured dry point, 100% at field capacity."""
        await probe_reports(integration, PASSIONFRUIT_RAW, 79.54)
        # Median of 60 and 79.54 is 69.77, which is (69.77 - 53.18) / 26.36
        # of the way up the calibrated span.
        assert float(integration.states.get(AVAILABLE).state) == 62.9

    async def test_a_dropout_is_not_a_reading(self, integration: HomeAssistant) -> None:
        """A probe going unavailable must never look like a bone-dry pot."""
        before = integration.states.get(SMOOTHED).state
        integration.states.async_set(PASSIONFRUIT_RAW, "unavailable")
        await integration.async_block_till_done()
        assert integration.states.get(SMOOTHED).state == before

    async def test_a_non_numeric_reading_is_ignored(
        self, integration: HomeAssistant
    ) -> None:
        before = integration.states.get(SMOOTHED).state
        integration.states.async_set(PASSIONFRUIT_RAW, "wet-ish")
        await integration.async_block_till_done()
        assert integration.states.get(SMOOTHED).state == before


class TestWateringDetection:
    """Instantaneous — a rise above the trailing minimum, no waiting needed."""

    async def test_a_watering_is_detected_and_recorded(
        self, integration: HomeAssistant
    ) -> None:
        assert integration.states.get(DAYS_WATERED).state == "unknown"

        await probe_reports(integration, PASSIONFRUIT_RAW, 78.0)

        assert integration.states.get(DAYS_WATERED).state == "0.0"

    async def test_a_splash_is_not_a_watering(self, integration: HomeAssistant) -> None:
        """Below a quarter of the calibrated span, so noise cannot reach it."""
        await probe_reports(integration, PASSIONFRUIT_RAW, 62.0)
        assert integration.states.get(DAYS_WATERED).state == "unknown"

    async def test_the_rise_sensor_reflects_it(
        self, integration: HomeAssistant
    ) -> None:
        await probe_reports(integration, PASSIONFRUIT_RAW, 78.0)
        assert float(integration.states.get(RISE).state) == 18.0

    async def test_a_dry_down_never_looks_like_a_watering(
        self, integration: HomeAssistant
    ) -> None:
        for value in (59.0, 58.0, 57.0, 56.0):
            await probe_reports(integration, PASSIONFRUIT_RAW, value)
        assert float(integration.states.get(RISE).state) == 0.0
        assert integration.states.get(DAYS_WATERED).state == "unknown"


class TestNeedsWaterReachesTheFeed:
    async def test_dry_for_long_enough_flags_and_reaches_outstanding(
        self, integration: HomeAssistant, freezer: FrozenDateTimeFactory
    ) -> None:
        """The full chain: readings → monitor latch → binary sensor → feed."""
        assert integration.states.get(NEEDS_WATER).state == "off"

        # Below the 57.13 threshold, held past the two-hour confirm window.
        # Deliberately the *same* value every time: Home Assistant fires no
        # `state_changed` for a repeated value, so this only works because the
        # coordinator also subscribes to `state_reported`. It is the realistic
        # case — a pot sitting still reports the same number for hours.
        for _ in range(16):
            freezer.tick(timedelta(minutes=10))
            await probe_reports(integration, PASSIONFRUIT_RAW, 55.0)

        assert integration.states.get(NEEDS_WATER).state == "on"

        outstanding = integration.states.get(OUTSTANDING)
        kinds = [item["kind"] for item in outstanding.attributes["items"]]
        assert "needs_water" in kinds

    async def test_a_watering_clears_it(
        self, integration: HomeAssistant, freezer: FrozenDateTimeFactory
    ) -> None:
        for _ in range(16):
            freezer.tick(timedelta(minutes=10))
            await probe_reports(integration, PASSIONFRUIT_RAW, 55.0)
        assert integration.states.get(NEEDS_WATER).state == "on"

        freezer.tick(timedelta(minutes=10))
        await probe_reports(integration, PASSIONFRUIT_RAW, 78.0)

        assert integration.states.get(NEEDS_WATER).state == "off"

        # Only this plant's thirst should have cleared. The other plants' probes
        # are not being fed during this test, so they legitimately go silent —
        # asserting the whole feed is empty would be asserting the health checks
        # do not work.
        items = integration.states.get(OUTSTANDING).attributes["items"]
        assert not [
            item
            for item in items
            if item["plant"] == "passionfruit" and item["kind"] == "needs_water"
        ]

    async def test_the_threshold_is_published_for_inspection(
        self, integration: HomeAssistant
    ) -> None:
        """So "why is this flagged" is answerable from the entity itself."""
        attrs = integration.states.get(NEEDS_WATER).attributes
        assert attrs["refill_threshold"] == 57.13
        assert attrs["field_capacity"] == 79.54
        assert attrs["dry_point"] == 53.18
