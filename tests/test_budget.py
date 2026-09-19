"""Time budgets: bad time over a rolling window, against an allowance.

The arithmetic every burn-rate health check rests on, so it is pinned on its
own rather than through the checks that use it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from custom_components.plant_care.model.budget import Interval, TimeBudget

T0 = datetime(2026, 9, 8, 0, 0, tzinfo=UTC)
WEEK = timedelta(days=7)
HOUR = timedelta(hours=1)
MINUTE = timedelta(minutes=1)


def budget(allowance: timedelta = 100 * MINUTE) -> TimeBudget:
    return TimeBudget(window=WEEK, allowance=allowance)


class TestInterval:
    def test_overlap_is_clipped_to_both_ends(self) -> None:
        interval = Interval(T0 + HOUR, T0 + 3 * HOUR)
        assert interval.overlap(T0, T0 + 2 * HOUR) == HOUR
        assert interval.overlap(T0 + 2 * HOUR, T0 + 10 * HOUR) == HOUR
        assert interval.overlap(T0 + 5 * HOUR, T0 + 6 * HOUR) == timedelta(0)

    def test_it_cannot_run_backwards(self) -> None:
        with pytest.raises(ValueError):
            Interval(T0 + HOUR, T0)


class TestAccrual:
    def test_closed_stretches_add_up_inside_the_window(self) -> None:
        b = budget()
        b.add(Interval(T0 + 1 * HOUR, T0 + 2 * HOUR))
        b.add(Interval(T0 + 5 * HOUR, T0 + 5 * HOUR + 30 * MINUTE))
        assert b.accrued(T0 + 6 * HOUR, WEEK) == 90 * MINUTE

    def test_only_the_part_inside_the_period_counts(self) -> None:
        b = budget()
        b.add(Interval(T0, T0 + 2 * HOUR))
        assert b.accrued(T0 + 3 * HOUR, over=2 * HOUR) == HOUR

    def test_a_stretch_still_open_counts_up_to_now(self) -> None:
        b = budget()
        b.mark(T0, bad=True)
        assert b.accrued(T0 + 45 * MINUTE, WEEK) == 45 * MINUTE

    def test_a_stretch_the_caller_knows_about_counts_too(self) -> None:
        """Silence is not marked as it happens — nothing fires while a probe
        says nothing — so the caller passes the open stretch in."""
        b = budget()
        assert b.accrued(T0 + HOUR, WEEK, open_since=T0 + 20 * MINUTE) == 40 * MINUTE

    def test_an_open_since_in_the_future_counts_nothing(self) -> None:
        """The heartbeat has not yet been missed."""
        b = budget()
        assert b.accrued(T0, WEEK, open_since=T0 + 5 * MINUTE) == timedelta(0)

    def test_marking_bad_then_good_closes_a_stretch(self) -> None:
        b = budget()
        assert b.mark(T0, bad=True) is False
        assert b.mark(T0 + 10 * MINUTE, bad=True) is False  # still open
        assert b.mark(T0 + HOUR, bad=False) is True
        assert b.mark(T0 + 2 * HOUR, bad=False) is False  # nothing to close
        assert b.closed() == [Interval(T0, T0 + HOUR)]

    def test_stretches_older_than_the_window_are_forgotten(self) -> None:
        b = budget()
        b.add(Interval(T0, T0 + HOUR))
        b.add(Interval(T0 + 8 * timedelta(days=1), T0 + 8 * timedelta(days=1) + HOUR))
        assert b.closed() == [
            Interval(T0 + 8 * timedelta(days=1), T0 + 8 * timedelta(days=1) + HOUR)
        ]


class TestBurnRate:
    def test_one_means_the_allowance_is_spent_over_the_whole_window(self) -> None:
        b = budget(allowance=100 * MINUTE)
        b.add(Interval(T0, T0 + 100 * MINUTE))
        assert b.burn_rate(T0 + 2 * HOUR, WEEK) == pytest.approx(1.0)

    def test_it_is_relative_to_pace_over_the_period_asked_about(self) -> None:
        """100 minutes a week is about 14 a day. An hour in one day is over
        four times that pace, and exactly the same hour is well under pace
        across the week."""
        b = budget(allowance=100 * MINUTE)
        b.add(Interval(T0, T0 + HOUR))
        now = T0 + 2 * HOUR
        assert b.burn_rate(now, timedelta(days=1)) == pytest.approx(60 / (100 / 7))
        assert b.burn_rate(now, WEEK) == pytest.approx(0.6)

    def test_a_zero_allowance_never_divides_by_zero(self) -> None:
        b = budget(allowance=timedelta(0))
        b.add(Interval(T0, T0 + HOUR))
        assert b.burn_rate(T0 + 2 * HOUR, WEEK) == 0.0


class TestRestore:
    def test_closed_stretches_come_back_and_open_ones_do_not(self) -> None:
        before = budget()
        before.add(Interval(T0, T0 + HOUR))
        before.mark(T0 + 2 * HOUR, bad=True)

        after = budget()
        after.restore(before.closed())

        assert after.accrued(T0 + 3 * HOUR, WEEK) == HOUR

    def test_the_window_and_allowance_are_checked(self) -> None:
        with pytest.raises(ValueError):
            TimeBudget(window=timedelta(0), allowance=HOUR)
        with pytest.raises(ValueError):
            TimeBudget(window=WEEK, allowance=-HOUR)
