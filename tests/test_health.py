"""Faults that would otherwise read as healthy soil.

Every duration here — six hours silent, a week of dropouts, twelve hours
stuck, a day wet, an hour to settle — is driven by injected timestamps, so none
of these tests wait.
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
from custom_components.plant_care.model.health import IssueKind
from custom_components.plant_care.model.signals import MoistureMonitor

START = datetime(2026, 9, 15, 8, 0, 0, tzinfo=UTC)

PASSIONFRUIT = Calibrated(field_capacity=79.54, dry_point=53.18, fc_tolerance=8.0)
PROBE = ProbeFacts(heartbeat_minutes=10, deadband_pp=1.0)

# The shortfall bound for the passionfruit: 79.54 - 8.0
SHORTFALL_TARGET = 71.54


def monitor(calibration=PASSIONFRUIT) -> MoistureMonitor:
    return MoistureMonitor(policy=DEFAULT_POLICY, probe=PROBE, calibration=calibration)


def kinds(mon: MoistureMonitor, now: datetime) -> set[IssueKind]:
    return {issue.kind for issue in mon.health(now)}


def feed(mon: MoistureMonitor, values, start=START, every=timedelta(minutes=10)):
    for index, value in enumerate(values):
        mon.observe(start + every * index, value)
    return start + every * (len(values) - 1)


class TestProbeSilent:
    def test_a_probe_that_never_reports_is_eventually_silent(self) -> None:
        """Not "fine until proven otherwise" — a plant nothing is watching is a
        plant that can die unnoticed."""
        mon = monitor()
        mon.restore(needs_water=False, last_watered=None, now=START)
        assert IssueKind.PROBE_SILENT in kinds(mon, START + timedelta(hours=7))

    def test_a_cold_start_is_given_time_to_populate(self) -> None:
        """On a Home Assistant restart, MQTT discovery has not created the probe
        entities yet. Without a grace period every plant would report a silent
        probe for the first seconds of every restart — noise that teaches you to
        ignore the check."""
        mon = monitor()
        mon.restore(needs_water=False, last_watered=None, now=START)
        assert IssueKind.PROBE_SILENT not in kinds(mon, START + timedelta(minutes=5))

    def test_with_no_clock_at_all_nothing_is_claimed(self) -> None:
        """Never started and never heard from: there is no duration to measure,
        and inventing a fault would be guessing."""
        assert IssueKind.PROBE_SILENT not in kinds(monitor(), START)

    def test_a_reporting_probe_is_not_silent(self) -> None:
        mon = monitor()
        feed(mon, [60.0])
        assert IssueKind.PROBE_SILENT not in kinds(mon, START + timedelta(minutes=10))

    def test_silence_past_the_floor_is_reported(self) -> None:
        """Six hours, whatever the heartbeat. A two-hour threshold produced
        alerts that had healed by the time anyone looked."""
        mon = monitor()
        feed(mon, [60.0])
        assert IssueKind.PROBE_SILENT not in kinds(mon, START + timedelta(hours=5))
        assert IssueKind.PROBE_SILENT in kinds(mon, START + timedelta(hours=7))

    def test_a_two_hour_dropout_is_not_a_fault(self) -> None:
        """The false positive the burn-rate shape exists to remove: a dropout
        that heals is neither silent nor, on its own, flaky."""
        mon = monitor()
        feed(mon, [60.0] * 6)
        gap_end = START + timedelta(hours=3)
        assert kinds(mon, gap_end - timedelta(minutes=1)) == set()
        feed(mon, [60.0] * 6, start=gap_end)
        assert kinds(mon, gap_end + timedelta(minutes=50)) == set()

    def test_a_silent_probe_suppresses_the_other_reading_checks(self) -> None:
        """Otherwise a probe that stops mid-sentence reports as both silent and
        stuck — two alerts for one fault, and the second is misleading."""
        mon = monitor()
        feed(mon, [60.0] * 5)
        issues = kinds(mon, START + timedelta(hours=20))
        assert issues == {IssueKind.PROBE_SILENT}

    def test_it_carries_how_long(self) -> None:
        mon = monitor()
        feed(mon, [60.0])
        (issue,) = [
            i
            for i in mon.health(START + timedelta(hours=8))
            if i.kind is IssueKind.PROBE_SILENT
        ]
        assert issue.value == 8.0


class TestProbeFlaky:
    """Never down long enough to be silent; down often enough to matter."""

    def _dropouts(self, mon: MoistureMonitor, minutes: int, days: int) -> datetime:
        """Reporting every ten minutes, all day, except one `minutes`-long gap a
        day for `days` days. The value wobbles so nothing reads as stuck, and
        stays between the refill threshold and field capacity."""
        for day in range(days):
            for slot in range(144):
                offset = slot * 10
                if 360 <= offset < 360 + minutes:
                    continue  # the gap: from six hours in, for `minutes`
                mon.observe(
                    START + timedelta(days=day, minutes=offset),
                    65.0 + 0.1 * (slot % 2),
                )
        return START + timedelta(days=days - 1, minutes=1430)

    def test_a_dropout_every_day_spends_the_week(self) -> None:
        """Half an hour a day is 210 minutes over the week, twice the pace of
        the ~100 a 99% SLO allows. No single episode comes near the silent
        floor."""
        mon = monitor()
        last = self._dropouts(mon, minutes=30, days=7)
        assert kinds(mon, last) == {IssueKind.PROBE_FLAKY}

    def test_the_odd_dropout_does_not(self) -> None:
        mon = monitor()
        last = self._dropouts(mon, minutes=30, days=2)
        assert IssueKind.PROBE_FLAKY not in kinds(mon, last)

    def test_the_stretch_since_the_last_report_counts(self) -> None:
        """Dropouts banked plus a gap still open: the open one tips it over,
        before it is anywhere near long enough to be silent."""
        mon = monitor()
        last = self._dropouts(mon, minutes=50, days=3)  # 150 minutes banked
        assert IssueKind.PROBE_FLAKY not in kinds(mon, last)
        # 70 minutes on: one heartbeat expected, sixty minutes past it.
        assert IssueKind.PROBE_FLAKY in kinds(mon, last + timedelta(minutes=70))

    def test_a_probe_that_is_silent_now_is_not_also_flaky(self) -> None:
        mon = monitor()
        last = self._dropouts(mon, minutes=30, days=7)
        assert kinds(mon, last + timedelta(hours=7)) == {IssueKind.PROBE_SILENT}

    def test_restored_dropouts_survive_a_restart(self) -> None:
        """The slow burn exists to see across days; Home Assistant restarts
        more often than that."""
        before = monitor()
        last = self._dropouts(before, minutes=30, days=7)

        after = monitor()
        after.restore(
            needs_water=False,
            last_watered=None,
            now=last,
            silence=before.intervals()["silence"],
        )
        last = feed(after, [65.0, 65.1, 65.0], start=last + timedelta(minutes=10))
        assert IssueKind.PROBE_FLAKY in kinds(after, last)

    def test_the_message_carries_the_arithmetic(self) -> None:
        mon = monitor()
        last = self._dropouts(mon, minutes=30, days=7)
        (issue,) = [i for i in mon.health(last) if i.kind is IssueKind.PROBE_FLAKY]
        assert "210 minutes" in issue.detail
        assert "allowance of 101" in issue.detail
        assert issue.value == pytest.approx(210 / 100.8, abs=0.01)

    def test_a_heartbeat_a_few_seconds_late_is_not_a_dropout(self) -> None:
        """The false positive found in the field: a probe whose ten-minute
        cycle runs at 10:02 was charged two seconds per heartbeat, over half
        an hour a week, for reporting perfectly."""
        mon = monitor()
        feed(mon, [65.0, 65.1] * 504, every=timedelta(minutes=10, seconds=2))
        assert mon.intervals()["silence"] == []

    def test_a_missed_heartbeat_counts_from_when_it_was_due(self) -> None:
        mon = monitor()
        feed(mon, [65.0])
        mon.observe(START + timedelta(minutes=20, seconds=1), 65.1)
        (gap,) = mon.intervals()["silence"]
        assert gap.start == START + timedelta(minutes=10)
        assert gap.end == START + timedelta(minutes=20, seconds=1)

    def test_a_report_still_only_late_is_not_an_open_dropout(self) -> None:
        """Between one heartbeat and one and a half, the stretch since the
        last report is not yet silence — the same allowance the closed gaps
        get, so the clock cannot page on a report that then arrives. 200
        minutes banked is just under the 201.6 that pages; fourteen minutes
        on, nothing has been added (the old accounting added four and paged),
        and seventeen minutes on, the due heartbeat's seven minutes have."""
        mon = monitor()
        last = self._dropouts(mon, minutes=50, days=4)  # 200 minutes banked
        assert IssueKind.PROBE_FLAKY not in kinds(mon, last + timedelta(minutes=14))
        assert IssueKind.PROBE_FLAKY not in kinds(mon, last + timedelta(minutes=15))
        assert IssueKind.PROBE_FLAKY in kinds(mon, last + timedelta(minutes=17))

    def test_the_message_says_how_the_budget_was_spent(self) -> None:
        """Seven half-hour dropouts and a week of single missed reports read
        the same as a total. The shape is what says whether to look at the
        probe or at the policy."""
        mon = monitor()
        last = self._dropouts(mon, minutes=30, days=7)
        (issue,) = [i for i in mon.health(last) if i.kind is IssueKind.PROBE_FLAKY]
        assert "across 7 stretches, the longest 30 minutes" in issue.detail


class TestProbeStuck:
    """Reporting fine, but the value has not moved. A pot in use always drifts."""

    def test_a_moving_reading_is_not_stuck(self) -> None:
        mon = monitor()
        last = feed(mon, [70.0 - step * 0.2 for step in range(80)])
        assert IssueKind.PROBE_STUCK not in kinds(mon, last)

    def test_an_unchanging_reading_is_stuck(self) -> None:
        mon = monitor()
        # Reporting every ten minutes for thirteen hours, same number.
        last = feed(mon, [60.0] * 78)
        assert IssueKind.PROBE_STUCK in kinds(mon, last)

    def test_it_takes_the_full_window(self) -> None:
        mon = monitor()
        last = feed(mon, [60.0] * 30)  # under five hours
        assert IssueKind.PROBE_STUCK not in kinds(mon, last)

    def test_a_single_change_resets_it(self) -> None:
        mon = monitor()
        feed(mon, [60.0] * 78)
        last = feed(mon, [60.5], start=START + timedelta(hours=13))
        assert IssueKind.PROBE_STUCK not in kinds(mon, last)


class TestWaterlogged:
    """Time above field capacity, at two speeds."""

    def test_a_whole_day_above_field_capacity_is_not_draining(self) -> None:
        mon = monitor()
        last = feed(mon, [85.0] * 160)  # above 79.54 for over a day
        (issue,) = [i for i in mon.health(last) if i.kind is IssueKind.WATERLOGGED]
        assert issue.label == "Not draining"

    def test_a_brief_spike_is_not(self) -> None:
        """A watering puts the pot above field capacity for a while by design.
        Only sitting there is a fault."""
        mon = monitor()
        last = feed(mon, [85.0] * 12)  # two hours
        assert IssueKind.WATERLOGGED not in kinds(mon, last)

    def test_draining_back_down_clears_the_fast_page(self) -> None:
        mon = monitor()
        feed(mon, [85.0] * 100)
        last = feed(mon, [70.0] * 10, start=START + timedelta(hours=17))
        assert IssueKind.WATERLOGGED not in kinds(mon, last)

    def test_wet_more_of_the_week_than_tolerated_is_overwatered(self) -> None:
        """Twelve hours wet, twelve dry, every day. It drains — no single day
        trips the fast page — and it is above field capacity half the time,
        past the 40% this plant tolerates. The old check could not see this."""
        mon = monitor()
        when = START
        for _ in range(7):
            when = feed(mon, [85.0] * 72, start=when) + timedelta(minutes=10)
            when = feed(mon, [70.0] * 72, start=when) + timedelta(minutes=10)
        (issue,) = [i for i in mon.health(when) if i.kind is IssueKind.WATERLOGGED]
        assert issue.label == "Overwatered"
        assert "50% of the last 7 days" in issue.detail
        assert "budget of 40%" in issue.detail

    def test_a_tolerant_plant_is_not(self) -> None:
        """The same week against a per-plant budget that allows it."""
        marshy = Calibrated(
            field_capacity=79.54, dry_point=53.18, waterlogged_budget_pct=60.0
        )
        mon = monitor(marshy)
        when = START
        for _ in range(7):
            when = feed(mon, [85.0] * 72, start=when) + timedelta(minutes=10)
            when = feed(mon, [70.0] * 72, start=when) + timedelta(minutes=10)
        assert IssueKind.WATERLOGGED not in kinds(mon, when)

    def test_not_reported_while_calibrating(self) -> None:
        """Field capacity is what "too wet" is measured against, and a
        calibrating plant has none."""
        mon = monitor(Calibrating())
        last = feed(mon, [95.0] * 160)
        assert IssueKind.WATERLOGGED not in kinds(mon, last)

    def test_closed_wet_stretches_survive_a_restart(self) -> None:
        before = monitor()
        when = START
        for _ in range(7):
            when = feed(before, [85.0] * 72, start=when) + timedelta(minutes=10)
            when = feed(before, [70.0] * 72, start=when) + timedelta(minutes=10)

        after = monitor()
        after.restore(
            needs_water=False,
            last_watered=None,
            now=when,
            wet=before.intervals()["wet"],
        )
        last = feed(after, [70.0] * 3, start=when)
        assert IssueKind.WATERLOGGED in kinds(after, last)


class TestWateringShortfall:
    """Did the water actually reach the roots, or run down the side?"""

    def _water_then_settle(self, settled: float) -> MoistureMonitor:
        mon = monitor()
        feed(mon, [56.0, 56.0])
        # A watering: a rise well past a quarter of the span.
        mon.observe(START + timedelta(minutes=20), 75.0)
        # An hour later, where it settled.
        mon.observe(START + timedelta(minutes=80), settled)
        return mon

    def test_a_watering_that_settles_near_field_capacity_is_fine(self) -> None:
        mon = self._water_then_settle(SHORTFALL_TARGET + 1.0)
        assert IssueKind.WATERING_SHORTFALL not in kinds(
            mon, START + timedelta(minutes=80)
        )

    def test_a_watering_that_settles_low_is_reported(self) -> None:
        mon = self._water_then_settle(SHORTFALL_TARGET - 5.0)
        assert IssueKind.WATERING_SHORTFALL in kinds(mon, START + timedelta(minutes=80))

    def test_it_is_not_judged_before_the_settle_window(self) -> None:
        """Soil is still draining; judging early would alarm on every watering."""
        mon = monitor()
        feed(mon, [56.0, 56.0])
        mon.observe(START + timedelta(minutes=20), 75.0)
        mon.observe(START + timedelta(minutes=40), 60.0)  # only 20 minutes in
        assert IssueKind.WATERING_SHORTFALL not in kinds(
            mon, START + timedelta(minutes=40)
        )

    def test_a_later_good_watering_clears_it(self) -> None:
        mon = self._water_then_settle(SHORTFALL_TARGET - 5.0)
        assert IssueKind.WATERING_SHORTFALL in kinds(mon, START + timedelta(minutes=80))

        mon.observe(START + timedelta(hours=2), 78.0)
        assert IssueKind.WATERING_SHORTFALL not in kinds(
            mon, START + timedelta(hours=2)
        )

    def test_the_message_carries_both_numbers(self) -> None:
        """A human has to decide what to do about it, so "settled at X, should
        have reached Y" is the useful form."""
        mon = self._water_then_settle(60.0)
        (issue,) = [
            i
            for i in mon.health(START + timedelta(minutes=80))
            if i.kind is IssueKind.WATERING_SHORTFALL
        ]
        assert "60.0%" in issue.detail
        assert "71.5%" in issue.detail
        assert issue.value == 60.0

    def test_nothing_is_judged_while_calibrating(self) -> None:
        """No field capacity means no bound to fall short of."""
        mon = monitor(Calibrating())
        feed(mon, [56.0, 56.0])
        mon.observe(START + timedelta(minutes=20), 75.0)
        mon.observe(START + timedelta(minutes=80), 60.0)
        assert IssueKind.WATERING_SHORTFALL not in kinds(
            mon, START + timedelta(minutes=80)
        )

    def test_complete_channelling_is_invisible_here(self) -> None:
        """The hole worth knowing about.

        Water that bypasses the rootball entirely produces no rise, so no
        watering is detected and this check never runs. It is caught instead by
        the needs-water latch never clearing — which is exactly why the latch is
        cleared only by a detected watering.
        """
        mon = monitor()
        feed(mon, [56.0] * 14)
        assert mon.needs_water

        # "Watering" that the probe barely registers.
        mon.observe(START + timedelta(hours=3), 57.0)

        assert IssueKind.WATERING_SHORTFALL not in kinds(
            mon, START + timedelta(hours=3)
        )
        assert mon.needs_water, "the latch is the backstop for this case"


class TestRestore:
    def test_a_watering_mid_settle_window_survives_a_restart(self) -> None:
        """A redeploy right after watering is exactly when you are stood over
        the pot, so it is the worst time to silently skip the check."""
        watered = START
        mon = monitor()
        mon.restore(
            needs_water=False,
            last_watered=watered,
            now=watered + timedelta(minutes=10),
        )
        mon.observe(watered + timedelta(minutes=70), 60.0)
        assert IssueKind.WATERING_SHORTFALL in kinds(
            mon, watered + timedelta(minutes=70)
        )

    def test_an_old_watering_is_not_re_judged(self) -> None:
        """Its settle window closed long ago; re-running it now would judge a
        pot that has since dried on purpose."""
        watered = START
        mon = monitor()
        mon.restore(
            needs_water=False, last_watered=watered, now=watered + timedelta(hours=6)
        )
        mon.observe(watered + timedelta(hours=6), 60.0)
        assert IssueKind.WATERING_SHORTFALL not in kinds(
            mon, watered + timedelta(hours=6)
        )
