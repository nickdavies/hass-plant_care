"""The moisture signal layer.

Driven by explicit timestamps rather than by waiting, so the two-hour confirm
window and the one-hour median are testable in microseconds.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from custom_components.plant_care.model import (
    DEFAULT_POLICY,
    Calibrated,
    Calibrating,
    ProbeFacts,
)
from custom_components.plant_care.model.signals import (
    MoistureMonitor,
    Reading,
    RollingWindow,
)

# Timezone-aware, matching what Home Assistant hands the coordinator.
START = datetime(2026, 9, 15, 8, 0, 0, tzinfo=UTC)

# The passionfruit: refill lands at 57.13, a watering is a rise of 6.59.
PASSIONFRUIT = Calibrated(field_capacity=79.54, dry_point=53.18, fc_tolerance=8.0)
PROBE = ProbeFacts(heartbeat_minutes=10, deadband_pp=1.0)


def monitor(calibration=PASSIONFRUIT) -> MoistureMonitor:
    return MoistureMonitor(policy=DEFAULT_POLICY, probe=PROBE, calibration=calibration)


def feed(mon: MoistureMonitor, values, start=START, every=timedelta(minutes=10)):
    """Push a series of readings at a fixed cadence. Returns watering events."""
    events = []
    for index, value in enumerate(values):
        events.append(mon.observe(start + every * index, value))
    return events


class TestRollingWindow:
    def test_drops_readings_outside_the_window(self) -> None:
        window = RollingWindow(window=timedelta(hours=1), max_samples=30)
        window.add(Reading(START, 10.0))
        window.add(Reading(START + timedelta(minutes=45), 20.0))
        window.add(Reading(START + timedelta(minutes=90), 30.0))
        # The first is now 90 minutes old; the second is 45 minutes old.
        assert window.values == [20.0, 30.0]

    def test_the_window_is_exclusive_at_the_far_end(self) -> None:
        """A reading exactly one window old is out, not in.

        Pinned because it is the kind of off-by-one that would otherwise be
        decided by accident, and it changes which samples a median sees at every
        tick when the heartbeat divides evenly into the window.
        """
        window = RollingWindow(window=timedelta(hours=1), max_samples=30)
        window.add(Reading(START, 10.0))
        window.add(Reading(START + timedelta(hours=1), 20.0))
        assert window.values == [20.0]

    def test_median_rejects_a_single_bad_read(self) -> None:
        """The whole reason for a median over a mean: one spike is discarded
        rather than smeared across the window."""
        window = RollingWindow(window=timedelta(hours=1), max_samples=30)
        for value in (60.0, 60.0, 5.0, 60.0, 60.0):
            window.add(Reading(START, value))
        assert window.median() == 60.0

    def test_empty_window_reports_nothing_not_zero(self) -> None:
        window = RollingWindow(window=timedelta(hours=1), max_samples=30)
        assert window.median() is None
        assert window.minimum() is None

    def test_sample_cap_binds_only_under_a_flood(self) -> None:
        window = RollingWindow(window=timedelta(hours=1), max_samples=3)
        for index in range(10):
            window.add(Reading(START + timedelta(seconds=index), float(index)))
        assert window.values == [7.0, 8.0, 9.0]


class TestWateringDetection:
    def test_a_watering_is_a_rise_above_the_trailing_minimum(self) -> None:
        mon = monitor()
        # Sitting dry, then watered: a rise well past 6.59.
        events = feed(mon, [56.0, 55.8, 55.6, 72.0])
        assert events == [False, False, False, True]

    def test_a_splash_does_not_count(self) -> None:
        """Small enough that sensor noise could reach it must not clear a
        flag — the threshold is a quarter of the calibrated span."""
        mon = monitor()
        events = feed(mon, [56.0, 55.8, 55.6, 58.0])
        assert events == [False] * 4

    def test_a_slow_dry_down_never_looks_like_watering(self) -> None:
        """The trailing minimum tracks a falling reading down, so the rise sits
        at zero throughout."""
        mon = monitor()
        values = [70.0 - step * 0.2 for step in range(20)]
        assert not any(feed(mon, values))

    def test_detection_is_off_while_calibrating(self) -> None:
        """No calibrated span means no scale on which to call a rise large.
        Monitoring continues; judgement does not."""
        mon = monitor(Calibrating())
        assert not any(feed(mon, [56.0, 55.0, 72.0]))
        assert mon.signals.smoothed is not None


class TestNeedsWaterLatch:
    def test_does_not_flag_before_the_confirm_window(self) -> None:
        mon = monitor()
        # Below 57.13 but only for 30 minutes.
        feed(mon, [56.0, 56.0, 56.0, 56.0])
        assert not mon.needs_water

    def test_flags_once_dry_has_held_long_enough(self) -> None:
        mon = monitor()
        # 10-minute cadence for 2h+ below the threshold.
        feed(mon, [56.0] * 14)
        assert mon.needs_water

    def test_a_single_dip_does_not_start_the_clock_running_forever(self) -> None:
        """One low read inside an otherwise wet window is discarded by the
        median, so the confirm clock never starts."""
        mon = monitor()
        feed(mon, [70.0, 70.0, 40.0, 70.0, 70.0] * 3)
        assert not mon.needs_water

    def test_a_watering_clears_the_flag(self) -> None:
        mon = monitor()
        feed(mon, [56.0] * 14)
        assert mon.needs_water

        mon.observe(START + timedelta(hours=3), 75.0)
        assert not mon.needs_water

    def test_moisture_rising_on_its_own_does_not_clear_the_flag(self) -> None:
        """A cool night or a settling probe is not someone having watered.

        Only a detected watering clears it, which is why the latch is asymmetric.
        """
        mon = monitor()
        feed(mon, [56.0] * 14)
        assert mon.needs_water

        # Drifting up gently, never a big enough rise to be a watering.
        feed(
            mon,
            [56.5, 57.0, 57.5, 58.0],
            start=START + timedelta(hours=3),
        )
        assert mon.needs_water

    def test_an_unchanging_reading_still_advances_the_clock(self) -> None:
        """A pot can sit at exactly one value for hours.

        The confirm window is longer than the median window, so readings are the
        *only* thing that can advance it — a window left to age out empties and
        the clock stalls. Home Assistant fires no `state_changed` for a repeated
        value, which is why the coordinator also subscribes to `state_reported`.
        """
        mon = monitor()
        feed(mon, [55.0] * 14)
        assert mon.needs_water

    def test_no_flag_while_calibrating(self) -> None:
        """A plant with no measured endpoints can be monitored but not judged —
        there is no threshold that was not invented."""
        mon = monitor(Calibrating())
        feed(mon, [10.0] * 20)
        assert not mon.needs_water


class TestRestore:
    def test_a_flagged_plant_stays_flagged(self) -> None:
        mon = monitor()
        mon.restore(needs_water=True, last_watered=None)
        assert mon.needs_water

        # Still dry after the restart, and the window has to refill.
        feed(mon, [56.0, 56.0])
        assert mon.needs_water

    def test_a_restored_flag_is_cleared_by_a_watering(self) -> None:
        mon = monitor()
        mon.restore(needs_water=True, last_watered=None)
        feed(mon, [56.0, 56.0, 72.0])
        assert not mon.needs_water

    def test_the_confirm_clock_restarts_after_a_restore(self) -> None:
        """Deliberate: the cost is a delayed flag, never a missed one, because
        an already-flagged plant stays flagged."""
        mon = monitor()
        mon.restore(needs_water=False, last_watered=None)
        feed(mon, [56.0] * 4)  # 30 minutes
        assert not mon.needs_water
        feed(mon, [56.0] * 12, start=START + timedelta(hours=1))
        assert mon.needs_water


class TestSignals:
    def test_everything_is_none_before_the_first_reading(self) -> None:
        """After a restart the window is empty and there is nothing to say. A
        zero would read as bone dry."""
        signals = monitor().signals
        assert signals.smoothed is None
        assert signals.minimum is None
        assert signals.rise is None
        assert signals.available_water is None

    def test_available_water_uses_the_calibrated_scale(self) -> None:
        mon = monitor()
        feed(mon, [PASSIONFRUIT.field_capacity])
        assert mon.signals.available_water == pytest.approx(100.0)

    def test_available_water_is_absent_while_calibrating(self) -> None:
        mon = monitor(Calibrating())
        feed(mon, [60.0])
        assert mon.signals.available_water is None
        assert mon.signals.smoothed == 60.0

    def test_rise_never_goes_negative(self) -> None:
        mon = monitor()
        feed(mon, [70.0, 60.0])
        assert mon.signals.rise == 0.0

    def test_rise_reflects_a_watering(self) -> None:
        mon = monitor()
        feed(mon, [56.0, 56.0, 72.0])
        assert mon.signals.rise == pytest.approx(16.0)

    def test_last_watered_is_recorded(self) -> None:
        mon = monitor()
        feed(mon, [56.0, 72.0])
        assert mon.last_watered == START + timedelta(minutes=10)
