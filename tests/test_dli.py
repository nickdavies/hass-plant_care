"""The daily light integral: accumulating it, and judging it as an SLO.

The tests that matter most here are the ones separating this from a threshold
alert wearing SLO clothes — that a near-miss burns proportionally less than a
blackout, and that nothing fires on a projection.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from custom_components.plant_care.model import (
    Band,
    BurnKind,
    DailyDli,
    Direction,
    DliAccumulator,
    DliObjective,
    outside_survival,
    today_certainty,
    unstable,
)
from custom_components.plant_care.model.dli import burn_rate, evaluate

TODAY = date(2026, 9, 15)

MONSTERA = DliObjective(
    category="foliage_tropical",
    preferred=Band(low=4.0, high=9.0),
    survival=Band(low=2.0, high=20.0),
    window_days=28,
    budget=20.0,
)

TIGHT = DliObjective(
    category="foliage_tropical",
    preferred=Band(low=4.0, high=9.0),
    survival=Band(low=2.0, high=20.0),
    window_days=28,
    budget=5.0,
)
"""A budget that actually exercises the burn rates, for the tests that are about
the mechanism rather than about the shipped numbers. See
`TestTheShippedBudgetIsDeliberatelyLoose` for why the default is not this."""


def days(*values: float, ending: date = TODAY) -> list[DailyDli]:
    """`values` oldest-first, the last one being yesterday."""
    return [
        DailyDli(day=ending - timedelta(days=len(values) - index), value=value)
        for index, value in enumerate(values)
    ]


class TestBand:
    def test_inside_the_band_deviates_by_nothing(self) -> None:
        """The band is what makes "in spec" a real state. With a point target
        every single day would deviate, the burn rate would never return to
        zero, and the thresholds would mean nothing."""
        assert MONSTERA.preferred.deviation(6.0) == 0.0
        assert MONSTERA.preferred.deviation(4.0) == 0.0
        assert MONSTERA.preferred.deviation(9.0) == 0.0

    def test_deviation_is_symmetric(self) -> None:
        """Too much light is a failure too — heat, photoinhibition, a disrupted
        photoperiod — not merely the absence of one."""
        assert MONSTERA.preferred.deviation(2.0) == 2.0
        assert MONSTERA.preferred.deviation(11.0) == 2.0

    def test_an_inverted_band_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="must be below"):
            Band(low=9.0, high=4.0)


class TestObjective:
    def test_a_preferred_band_outside_survival_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="outside survival"):
            DliObjective(
                category="shade",
                preferred=Band(low=2.0, high=30.0),
                survival=Band(low=3.0, high=6.0),
            )

    def test_outside_survival_needs_no_history(self) -> None:
        assert outside_survival(1.0, MONSTERA)
        assert outside_survival(25.0, MONSTERA)
        assert not outside_survival(3.0, MONSTERA)

    def test_survival_is_optional(self) -> None:
        objective = DliObjective(category="shade", preferred=Band(low=3.0, high=6.0))
        assert not outside_survival(0.0, objective)


class TestBurnRate:
    def test_on_pace_is_one(self) -> None:
        """A 20 mol budget over 28 days is 5 over 7 days. Spending exactly that
        is a rate of 1."""
        assert burn_rate(5.0, 7, MONSTERA) == pytest.approx(1.0)

    def test_a_near_miss_burns_proportionally_less_than_a_blackout(self) -> None:
        """The whole difference from a day count, and the thing worth checking.

        Under a "days outside the band" rule these two days are identical. Here
        the blackout costs four times the near miss, because it is four times as
        far outside — and only one of them fires.
        """
        assert TIGHT.preferred.deviation(3.0) == pytest.approx(1.0)
        assert TIGHT.preferred.deviation(0.0) == pytest.approx(4.0)

        assert evaluate(days(3.0), TIGHT, TODAY) == []
        assert BurnKind.FAST in [
            alert.kind for alert in evaluate(days(0.0), TIGHT, TODAY)
        ]

    def test_one_bad_day_trips_both_windows(self) -> None:
        """Expected, and why the coordinator reports only the worse of the two.

        A day bad enough for the fast window is also inside the slow one, so
        both fire on the same evidence. Correct as an SLO, but two feed items
        for one dark day is precisely the noise this system exists to avoid, so
        the presentation layer suppresses the slow one — see the coordinator.
        """
        kinds = [alert.kind for alert in evaluate(days(0.0), TIGHT, TODAY)]
        assert kinds == [BurnKind.FAST, BurnKind.SLOW]

    def test_fast_burn_carries_its_direction_and_amount(self) -> None:
        alert = next(
            alert
            for alert in evaluate(days(0.0), TIGHT, TODAY)
            if alert.kind is BurnKind.FAST
        )
        assert alert.direction is Direction.UNDER
        assert alert.deviation == pytest.approx(4.0)
        assert alert.rate == pytest.approx(22.4, abs=0.05)

    def test_slow_burn_catches_a_week_of_near_misses_a_day_count_cannot(
        self,
    ) -> None:
        """Seven days at 3.0 is 1 mol short each — never enough to fire a fast
        alert, and a threshold on any single day sees nothing at all. This is
        the winter-windowsill case, and it is the reason for the slow window."""
        kinds = [alert.kind for alert in evaluate(days(*[3.0] * 7), TIGHT, TODAY)]

        assert BurnKind.SLOW in kinds
        assert BurnKind.FAST not in kinds

    def test_direction_is_carried_because_the_fixes_are_opposite(self) -> None:
        over = evaluate(days(*[14.0] * 7), TIGHT, TODAY)
        assert over[-1].direction is Direction.OVER

        both = evaluate(days(0.0, 20.0, 0.0, 20.0, 0.0, 20.0, 0.0), TIGHT, TODAY)
        assert both[-1].direction is Direction.BOTH

    def test_a_healthy_week_produces_nothing(self) -> None:
        assert evaluate(days(*[6.0] * 7), TIGHT, TODAY) == []

    def test_today_is_never_counted(self) -> None:
        """A partially accumulated today always looks like a shortfall, so
        including it would fire an alert every single morning."""
        history = [*days(*[6.0] * 7), DailyDli(day=TODAY, value=0.1)]
        assert evaluate(history, TIGHT, TODAY) == []

    def test_no_history_produces_nothing(self) -> None:
        assert evaluate([], TIGHT, TODAY) == []


class TestTheShippedBudgetIsDeliberatelyLoose:
    """What the default numbers do, written down rather than assumed.

    Every tolerance in this system is a guess until there is a fortnight of data
    behind it, and the choice made was to start under-alerting. These tests pin
    what that actually costs, so tightening later is a decision taken with the
    numbers in front of you instead of a surprise.
    """

    def test_a_blackout_day_does_not_reach_the_fast_threshold(self) -> None:
        """Not an oversight — arithmetic. A shortfall cannot exceed the band's
        lower bound, so with a 20 mol / 28 day budget the worst possible dark
        day for this plant burns at 5.6x against a threshold of 10x.

        The low side is covered instead by the survival bound and by the
        intra-day certainty check, neither of which depends on budget scale.
        """
        assert evaluate(days(0.0), MONSTERA, TODAY) == []
        assert outside_survival(0.0, MONSTERA)

    def test_a_persistent_small_shortfall_does_not_reach_the_slow_threshold(
        self,
    ) -> None:
        """A week 1 mol under every day burns at 1.4x against a threshold of 2x.
        Tighten `budget` — not the multipliers — if this should page."""
        assert evaluate(days(*[3.0] * 7), MONSTERA, TODAY) == []

    def test_the_high_side_has_no_such_ceiling(self) -> None:
        """A lamp stuck on delivers an unbounded excess, so the fast burn fires
        on the default budget without any tuning at all."""
        assert BurnKind.FAST in [
            alert.kind for alert in evaluate(days(20.0), MONSTERA, TODAY)
        ]


class TestInstability:
    def test_a_plant_averaging_in_band_can_still_be_unstable(self) -> None:
        """Mean 8.0, inside the band, and every other day is a disaster. The
        band alone cannot see this; the mean is what hides it."""
        history = days(0.0, 16.0, 0.0, 16.0, 0.0, 16.0)
        assert unstable(history, MONSTERA, TODAY) == pytest.approx(16.0)

    def test_a_spread_inside_the_threshold_is_not_reported(self) -> None:
        assert unstable(days(0.0, 13.0, 0.0, 13.0, 0.0, 13.0), MONSTERA, TODAY) is None

    def test_a_steady_plant_is_not_unstable(self) -> None:
        assert unstable(days(*[6.0] * 7), MONSTERA, TODAY) is None

    def test_two_days_is_a_difference_not_a_spread(self) -> None:
        assert unstable(days(0.0, 20.0), MONSTERA, TODAY) is None

    def test_the_threshold_scales_with_the_band(self) -> None:
        """Derived from the band's width rather than configured, so a fussy
        plant is judged more tightly without a third number to get wrong."""
        assert MONSTERA.unstable_spread == pytest.approx(15.0)


class TestAccumulator:
    """PPFD in µmol/m²/s integrated over seconds, divided by a million."""

    START = datetime(2026, 9, 15, 8, 0, tzinfo=UTC)

    def test_a_steady_hour_integrates_to_the_textbook_figure(self) -> None:
        """1000 µmol/m²/s for an hour is 3.6 mol/m². If this drifts, every DLI
        number in the system is wrong by the same factor."""
        accumulator = DliAccumulator(self.START.date())
        accumulator.observe(self.START, 1000.0)
        for minute in range(1, 61):
            accumulator.observe(self.START + timedelta(minutes=minute), 1000.0)

        assert accumulator.total == pytest.approx(3.6)

    def test_the_first_reading_credits_nothing(self) -> None:
        """There is no interval before it to hold across."""
        accumulator = DliAccumulator(self.START.date())
        accumulator.observe(self.START, 1000.0)
        assert accumulator.total == 0.0

    def test_a_long_gap_is_capped_rather_than_extrapolated(self) -> None:
        """A sensor that drops out for six hours must not have its last daylight
        reading credited across the whole gap. Under-counting an outage is the
        safe direction: a shortfall is visible, a fabricated surplus is not.
        """
        accumulator = DliAccumulator(self.START.date())
        accumulator.observe(self.START, 1000.0)
        accumulator.observe(self.START + timedelta(hours=6), 1000.0)

        # 15 minutes at 1000, not 6 hours.
        assert accumulator.total == pytest.approx(0.9)

    def test_an_unavailable_reading_stops_the_hold(self) -> None:
        accumulator = DliAccumulator(self.START.date())
        accumulator.observe(self.START, 1000.0)
        accumulator.observe(self.START + timedelta(minutes=10), None)
        accumulator.observe(self.START + timedelta(minutes=20), None)

        # Only the first ten minutes, at the reading that actually existed.
        assert accumulator.total == pytest.approx(0.6)

    def test_a_clock_going_backwards_credits_nothing(self) -> None:
        accumulator = DliAccumulator(self.START.date())
        accumulator.observe(self.START, 1000.0)
        accumulator.observe(self.START - timedelta(minutes=5), 1000.0)
        assert accumulator.total == 0.0

    def test_midnight_closes_the_day_and_starts_the_next_at_zero(self) -> None:
        """The day's total is handed back so the caller can persist it, and the
        new day starts empty. Seeded through the constructor rather than
        integrated, so this tests the rollover and not the arithmetic."""
        accumulator = DliAccumulator(date(2026, 9, 15), total=6.2)

        finished = accumulator.observe(datetime(2026, 9, 16, 0, 1, tzinfo=UTC), 0.0)

        assert finished is not None
        assert finished.day == date(2026, 9, 15)
        assert finished.value == pytest.approx(6.2)
        assert accumulator.day == date(2026, 9, 16)
        assert accumulator.total == 0.0

    def test_the_interval_straddling_midnight_goes_to_the_day_that_ended(
        self,
    ) -> None:
        """Off by at most one capped interval, in a stretch of the night when
        the quantity being measured is zero. Splitting it would be precision
        about darkness."""
        accumulator = DliAccumulator(date(2026, 9, 15))
        accumulator.observe(datetime(2026, 9, 15, 23, 55, tzinfo=UTC), 1000.0)

        finished = accumulator.observe(datetime(2026, 9, 16, 0, 5, tzinfo=UTC), 0.0)

        assert finished is not None
        assert finished.value == pytest.approx(0.6)
        assert accumulator.total == 0.0

    def test_an_ordinary_reading_finishes_no_day(self) -> None:
        accumulator = DliAccumulator(self.START.date())
        assert accumulator.observe(self.START, 1.0) is None

    def test_a_resumed_day_keeps_what_was_already_banked(self) -> None:
        """A restart mid-afternoon must not reset the day to zero — that would
        read as a severe shortfall and burn budget for a failure that never
        happened."""
        accumulator = DliAccumulator(self.START.date(), total=4.2)
        assert accumulator.total == 4.2


class TestTodayCertainty:
    """Statements about what can no longer change. Never a projection."""

    HISTORY = days(6.0, 6.0, 6.0, 6.0)

    def test_already_past_the_upper_bound_is_certain(self) -> None:
        """The stuck-on lamp, caught exactly: the excess is a fact whatever the
        rest of the day does, so no assumption about it is needed."""
        noon = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)
        assert today_certainty(9.5, noon, self.HISTORY, MONSTERA) is Direction.OVER

    def test_a_slow_morning_is_not_a_deficit(self) -> None:
        """Light is not flat across a day. Extrapolating a morning's rate would
        call every sunrise a disaster."""
        eight_am = datetime(2026, 9, 15, 8, 0, tzinfo=UTC)
        assert today_certainty(0.2, eight_am, self.HISTORY, MONSTERA) is None

    def test_an_unrecoverable_evening_is_certain(self) -> None:
        """23:00 with 0.2 banked: even a whole best-day's worth of light in the
        remaining hour cannot reach 4.0."""
        late = datetime(2026, 9, 15, 23, 0, tzinfo=UTC)
        assert today_certainty(0.2, late, self.HISTORY, MONSTERA) is Direction.UNDER

    def test_the_deficit_test_waits_for_history(self) -> None:
        """Without some idea of what a good day looks like here, "unreachable"
        is a guess. The excess test needs none, so it still applies."""
        late = datetime(2026, 9, 15, 23, 0, tzinfo=UTC)
        assert today_certainty(0.2, late, days(6.0), MONSTERA) is None
        assert today_certainty(9.5, late, days(6.0), MONSTERA) is Direction.OVER

    def test_a_day_on_track_says_nothing(self) -> None:
        noon = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)
        assert today_certainty(3.0, noon, self.HISTORY, MONSTERA) is None
