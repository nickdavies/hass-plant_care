"""Grow light windows, presence, and the on-time outcome check.

Every boundary of the four-bound window is pinned here, because the cost of
getting one wrong is a lamp in somebody's bedroom at 2am or a plant that quietly
never gets its morning — and neither shows up in a test that only checks the
middle of the day.
"""

from __future__ import annotations

from datetime import time

import pytest

from custom_components.plant_care.model import (
    AwakeAwareWindow,
    Direction,
    FixedWindow,
    LightFixture,
    LuxFixture,
    Weekday,
    is_asleep,
    on_time_deviation,
    ppfd_factor,
)

PRESENCE = "sensor.person_presence_nick"

SPARE = FixedWindow(start=time(7, 0), end=time(19, 0))

STUDY = AwakeAwareWindow(
    if_awake_from=time(6, 0),
    no_later_than=time(9, 0),
    not_before=time(17, 0),
    until=time(19, 0),
    presence_entity=PRESENCE,
)


class TestFixedWindow:
    def test_start_is_inclusive_and_end_is_exclusive(self) -> None:
        """Pinned as its own test because the two boundaries are the only part
        of a plain window anyone ever gets wrong, and an off-by-one at the end
        is invisible for a year."""
        assert SPARE.is_on_at(time(7, 0), Weekday.MON, asleep=False)
        assert SPARE.is_on_at(time(18, 59), Weekday.MON, asleep=False)
        assert not SPARE.is_on_at(time(6, 59), Weekday.MON, asleep=False)
        assert not SPARE.is_on_at(time(19, 0), Weekday.MON, asleep=False)

    def test_sleep_is_ignored(self) -> None:
        """Nobody sleeps near this fixture, so the flag must change nothing."""
        assert SPARE.is_on_at(time(12, 0), Weekday.MON, asleep=True)

    def test_days_restrict_the_window(self) -> None:
        weekdays = FixedWindow(
            start=time(7, 0),
            end=time(19, 0),
            days=frozenset({Weekday.SAT, Weekday.SUN}),
        )
        assert weekdays.is_on_at(time(12, 0), Weekday.SAT, asleep=False)
        assert not weekdays.is_on_at(time(12, 0), Weekday.MON, asleep=False)

    def test_guaranteed_and_possible_are_the_same(self) -> None:
        """A fixed window has no discretion, so there is nothing to bound."""
        args = (time(0, 0), time(12, 0), Weekday.MON)
        assert SPARE.guaranteed_minutes(*args) == SPARE.possible_minutes(*args) == 300


class TestAwakeAwareWindow:
    """Four bounds, each answering a different failure. One test per region."""

    def test_before_if_awake_from_it_is_off_however_awake_they_are(self) -> None:
        assert not STUDY.is_on_at(time(5, 59), Weekday.MON, asleep=False)

    def test_the_early_stretch_needs_them_up(self) -> None:
        """06:00-09:00: on if they are up, so the plant banks the extra light."""
        assert STUDY.is_on_at(time(6, 0), Weekday.MON, asleep=False)
        assert not STUDY.is_on_at(time(8, 59), Weekday.MON, asleep=True)

    def test_the_guaranteed_middle_ignores_sleep(self) -> None:
        """09:00-17:00: on regardless, so a lie-in never starves the plant."""
        assert STUDY.is_on_at(time(9, 0), Weekday.MON, asleep=True)
        assert STUDY.is_on_at(time(16, 59), Weekday.MON, asleep=True)

    def test_the_tail_is_theirs_to_close(self) -> None:
        """17:00-19:00: an early night cuts it, being up keeps it."""
        assert STUDY.is_on_at(time(17, 0), Weekday.MON, asleep=False)
        assert not STUDY.is_on_at(time(17, 0), Weekday.MON, asleep=True)

    def test_until_closes_it_whatever_they_do(self) -> None:
        assert not STUDY.is_on_at(time(19, 0), Weekday.MON, asleep=False)

    def test_guaranteed_is_the_stretch_nobody_can_close(self) -> None:
        assert STUDY.guaranteed_minutes(time(0, 0), time(23, 59), Weekday.MON) == 480

    def test_possible_is_the_widest_it_can_ever_open(self) -> None:
        assert STUDY.possible_minutes(time(0, 0), time(23, 59), Weekday.MON) == 780

    def test_expectations_are_clipped_to_the_period_asked_about(self) -> None:
        """A restart narrows the comparison rather than reporting the part of
        the day nobody was watching as a shortfall."""
        assert STUDY.guaranteed_minutes(time(12, 0), time(14, 0), Weekday.MON) == 120
        assert STUDY.guaranteed_minutes(time(20, 0), time(23, 0), Weekday.MON) == 0

    def test_a_day_off_expects_nothing(self) -> None:
        weekend = AwakeAwareWindow(
            if_awake_from=time(6, 0),
            no_later_than=time(9, 0),
            not_before=time(17, 0),
            until=time(19, 0),
            presence_entity=PRESENCE,
            days=frozenset({Weekday.SAT}),
        )
        assert weekend.guaranteed_minutes(time(0, 0), time(23, 59), Weekday.MON) == 0
        assert weekend.possible_minutes(time(0, 0), time(23, 59), Weekday.MON) == 0


class TestPresence:
    """`sensor.group_presence_*` serialises a comma-joined set."""

    def test_a_group_with_one_person_up_is_not_asleep(self) -> None:
        """The trap this exists for: comparing the whole string against one word
        would silently never match, and the light would never come on."""
        assert not is_asleep("awake,asleep")

    def test_everyone_down_is_asleep(self) -> None:
        assert is_asleep("asleep")
        assert is_asleep("asleep,asleep")

    def test_winddown_counts_as_up(self) -> None:
        """Someone reading in bed still wants the room lit."""
        assert not is_asleep("winddown")

    def test_whitespace_around_members_is_tolerated(self) -> None:
        assert not is_asleep("asleep, awake")

    def test_an_unknown_signal_counts_as_awake(self) -> None:
        """The failure directions are not symmetric: a light left on wastes
        power, a light wrongly held off starves a plant."""
        assert not is_asleep(None)
        assert not is_asleep("unavailable")


class TestOnTimeDeviation:
    def test_within_the_bounds_is_not_evidence_of_anything(self) -> None:
        assert on_time_deviation(actual=300, guaranteed=200, possible=400) is None

    def test_short_of_the_guarantee_is_under(self) -> None:
        deviation = on_time_deviation(actual=0, guaranteed=480, possible=780)
        assert deviation is not None
        assert deviation.direction is Direction.UNDER
        assert deviation.minutes == 480

    def test_past_what_the_window_allows_is_over(self) -> None:
        """The stuck-on case. Caught without knowing why, which is the point —
        a frozen automation, a failed relay and a manual toggle read the same."""
        deviation = on_time_deviation(actual=1440, guaranteed=480, possible=780)
        assert deviation is not None
        assert deviation.direction is Direction.OVER
        assert deviation.minutes == 660

    def test_the_tolerance_absorbs_a_restart_sized_gap(self) -> None:
        assert on_time_deviation(actual=440, guaranteed=480, possible=780) is None
        assert on_time_deviation(actual=434, guaranteed=480, possible=780) is not None

    def test_a_lamp_on_during_a_day_off_is_over(self) -> None:
        """Both bounds are zero, so any on-time past the tolerance is excess."""
        deviation = on_time_deviation(actual=600, guaranteed=0, possible=0)
        assert deviation is not None
        assert deviation.direction is Direction.OVER


class TestPpfdFactor:
    LAMP = 0.0125
    SUN = 0.0185

    def test_unlit_uses_the_suns_factor(self) -> None:
        assert ppfd_factor(self.SUN, []) == self.SUN

    def test_one_lamp_on_uses_that_lamps_factor(self) -> None:
        assert ppfd_factor(self.SUN, [self.LAMP]) == self.LAMP

    def test_two_lamps_are_averaged_because_they_cannot_be_told_apart(self) -> None:
        """Documented as the least-wrong option, not as correct: one lux reading
        cannot be attributed between two spectra."""
        assert ppfd_factor(self.SUN, [0.012, 0.016]) == pytest.approx(0.014)


class TestLuxAveraging:
    FIXTURE = LuxFixture(
        name="study_shelf",
        entities=("sensor.lux_1", "sensor.lux_2"),
        sun_lux_to_ppfd=0.0185,
    )

    def test_members_are_averaged(self) -> None:
        assert self.FIXTURE.average([100.0, 200.0]) == 150.0

    def test_a_dead_member_is_skipped_not_counted_as_dark(self) -> None:
        """Counting it as zero would halve the reading, which looks exactly like
        a failing lamp and would burn the budget for a plant that is fine."""
        assert self.FIXTURE.average([100.0, None]) == 100.0

    def test_the_fixture_goes_quiet_only_when_every_member_is_out(self) -> None:
        assert self.FIXTURE.average([None, None]) is None


class TestFixtureShape:
    def test_presence_comes_from_the_window_not_the_fixture(self) -> None:
        """So the presence signal, the room and the hardware cannot drift apart:
        there is one place it is stated."""
        awake_aware = LightFixture(
            name="study_shelf",
            switch_entity="switch.study_lamp",
            room="nick_study",
            window=STUDY,
        )
        fixed = LightFixture(
            name="spare_shelf",
            switch_entity="switch.spare_lamp",
            room="spare",
            window=SPARE,
        )
        assert awake_aware.presence_entity == PRESENCE
        assert awake_aware.is_sleep_sensitive
        assert fixed.presence_entity is None
        assert not fixed.is_sleep_sensitive
