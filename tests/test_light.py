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
    MAX_RESTART_GAP_MINUTES,
    ON_TIME_TOLERANCE_MINUTES,
    AwakeAwareWindow,
    BinarySensorMatcher,
    Direction,
    FixedWindow,
    LightFixture,
    LuxFixture,
    PresenceMatcher,
    Weekday,
    is_asleep,
    on_time_deviation,
    ppfd_factor,
)

PRESENCE = "sensor.person_presence_nick"

SPARE = FixedWindow(start=time(7, 0), end=time(19, 0))

STUDY = AwakeAwareWindow(
    on_if_awake_after=time(6, 0),
    on_after=time(9, 0),
    on_even_if_asleep_until=time(17, 0),
    on_until=time(19, 0),
    quiet_when=PresenceMatcher(PRESENCE),
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
            on_if_awake_after=time(6, 0),
            on_after=time(9, 0),
            on_even_if_asleep_until=time(17, 0),
            on_until=time(19, 0),
            quiet_when=PresenceMatcher(PRESENCE),
            days=frozenset({Weekday.SAT}),
        )
        assert weekend.guaranteed_minutes(time(0, 0), time(23, 59), Weekday.MON) == 0
        assert weekend.possible_minutes(time(0, 0), time(23, 59), Weekday.MON) == 0


class TestSchedule:
    """What the dashboard prints at the foot of a lamp's card."""

    def test_a_fixed_window_is_one_range(self) -> None:
        (only,) = SPARE.schedule()
        assert only.label == "Schedule"
        assert only.text == "07:00–19:00"

    def test_an_awake_aware_window_is_the_guaranteed_then_the_possible(
        self,
    ) -> None:
        """The same pair the on-time sensor's attributes are stated in, so the
        card and the sensor describe the window the same way."""
        guaranteed, possible = STUDY.schedule()
        assert guaranteed.label == "Schedule (guaranteed)"
        assert guaranteed.text == "09:00–17:00"
        assert possible.label == "Schedule (if awake)"
        assert possible.text == "06:00–19:00"

    def test_every_day_says_nothing_about_days(self) -> None:
        assert "Mon" not in SPARE.schedule()[0].text

    def test_a_run_of_days_collapses_to_its_ends(self) -> None:
        weekdays = FixedWindow(
            start=time(7, 0),
            end=time(19, 0),
            days=frozenset(
                {Weekday.MON, Weekday.TUE, Weekday.WED, Weekday.THU, Weekday.FRI}
            ),
        )
        assert weekdays.schedule()[0].text == "07:00–19:00 Mon–Fri"

    def test_scattered_days_are_listed_in_week_order(self) -> None:
        """Two days are listed, not collapsed: `Sat–Sun` reads as a range that
        might hide something between."""
        weekend = AwakeAwareWindow(
            on_if_awake_after=time(6, 0),
            on_after=time(9, 0),
            on_even_if_asleep_until=time(17, 0),
            on_until=time(19, 0),
            quiet_when=PresenceMatcher(PRESENCE),
            days=frozenset({Weekday.SUN, Weekday.SAT}),
        )
        assert weekend.schedule()[0].text == "09:00–17:00 Sat, Sun"
        assert weekend.schedule()[1].text == "06:00–19:00 Sat, Sun"

        odd = FixedWindow(
            start=time(7, 0),
            end=time(19, 0),
            days=frozenset({Weekday.FRI, Weekday.MON, Weekday.WED}),
        )
        assert odd.schedule()[0].text == "07:00–19:00 Mon, Wed, Fri"


class TestWindowInvariants:
    """Ordering is a property of the window, not something a later pass
    checks, so a window that does not run forward cannot be constructed."""

    def test_a_fixed_window_must_run_forward(self) -> None:
        with pytest.raises(ValueError, match="forward"):
            FixedWindow(start=time(19, 0), end=time(7, 0))

    def test_a_fixed_window_may_not_be_empty(self) -> None:
        """Opening and closing at the same moment is a midnight-crossing window
        written the only way this format can express it."""
        with pytest.raises(ValueError, match="midnight"):
            FixedWindow(start=time(7, 0), end=time(7, 0))

    def test_awake_aware_bounds_must_be_ordered(self) -> None:
        with pytest.raises(ValueError, match="on_if_awake_after .* after on_after"):
            AwakeAwareWindow(
                on_if_awake_after=time(6, 0),
                on_after=time(5, 0),
                on_even_if_asleep_until=time(17, 0),
                on_until=time(19, 0),
                quiet_when=PresenceMatcher(PRESENCE),
            )
        with pytest.raises(
            ValueError, match="on_even_if_asleep_until .* after on_until"
        ):
            AwakeAwareWindow(
                on_if_awake_after=time(6, 0),
                on_after=time(9, 0),
                on_even_if_asleep_until=time(20, 0),
                on_until=time(19, 0),
                quiet_when=PresenceMatcher(PRESENCE),
            )

    def test_equal_bounds_are_allowed_between_regions(self) -> None:
        """A region may be empty — a fixture with no early stretch is fine."""
        window = AwakeAwareWindow(
            on_if_awake_after=time(9, 0),
            on_after=time(9, 0),
            on_even_if_asleep_until=time(17, 0),
            on_until=time(17, 0),
            quiet_when=PresenceMatcher(PRESENCE),
        )
        assert window.is_on_at(time(12, 0), Weekday.MON, asleep=True)

    def test_an_awake_aware_window_may_not_cross_midnight(self) -> None:
        with pytest.raises(ValueError, match="midnight"):
            AwakeAwareWindow(
                on_if_awake_after=time(6, 0),
                on_after=time(6, 0),
                on_even_if_asleep_until=time(6, 0),
                on_until=time(6, 0),
                quiet_when=PresenceMatcher(PRESENCE),
            )


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


class TestMatchers:
    """Whose sleep a window answers to. Both kinds fail towards light."""

    OUTPUT = "binary_sensor.presence_output_everyone_any_asleep"

    def test_a_presence_matcher_reads_the_presence_set(self) -> None:
        matcher = PresenceMatcher(PRESENCE)
        assert matcher.entity_ids == (PRESENCE,)
        assert matcher.is_quiet({PRESENCE: "asleep"})
        assert not matcher.is_quiet({PRESENCE: "awake,asleep"})

    def test_a_presence_matcher_with_no_state_is_not_quiet(self) -> None:
        assert not PresenceMatcher(PRESENCE).is_quiet({PRESENCE: None})
        assert not PresenceMatcher(PRESENCE).is_quiet({})

    def test_a_binary_sensor_matcher_is_quiet_only_when_on(self) -> None:
        """The case a presence sensor cannot express: one guest asleep while
        everyone else is up. The rule that says so lives in the sensor."""
        matcher = BinarySensorMatcher(self.OUTPUT)
        assert matcher.entity_ids == (self.OUTPUT,)
        assert matcher.is_quiet({self.OUTPUT: "on"})
        assert not matcher.is_quiet({self.OUTPUT: "off"})

    def test_a_broken_binary_sensor_leaves_the_lamp_free(self) -> None:
        """A rule that cannot be evaluated must not starve a plant for as long
        as it stays broken."""
        matcher = BinarySensorMatcher(self.OUTPUT)
        for state in ("unknown", "unavailable", None):
            assert not matcher.is_quiet({self.OUTPUT: state})
        assert not matcher.is_quiet({})

    def test_a_binary_sensor_matcher_refuses_other_domains(self) -> None:
        with pytest.raises(ValueError, match="not a binary_sensor"):
            BinarySensorMatcher(PRESENCE)

    def test_a_quiet_matcher_cannot_close_the_guaranteed_middle(self) -> None:
        """The matcher decides `asleep`; the window decides what that closes.
        A guest asleep till eleven costs the plant its early light, never its
        guaranteed stretch."""
        window = AwakeAwareWindow(
            on_if_awake_after=time(6, 0),
            on_after=time(9, 0),
            on_even_if_asleep_until=time(17, 0),
            on_until=time(19, 0),
            quiet_when=BinarySensorMatcher(self.OUTPUT),
        )
        fixture = LightFixture(
            name="study_shelf", switch_entity="switch.study_lamp", window=window
        )
        asleep = fixture.is_quiet({self.OUTPUT: "on"})
        assert asleep
        assert not window.is_on_at(time(7, 0), Weekday.MON, asleep=asleep)
        assert window.is_on_at(time(10, 0), Weekday.MON, asleep=asleep)
        assert not window.is_on_at(time(18, 0), Weekday.MON, asleep=asleep)


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

    def test_a_credited_restart_gap_cannot_be_what_trips_the_check(self) -> None:
        """The two constants, pinned against each other.

        On-time across a restart is filled in from the state the lamp was last
        seen in, so a credited gap is an estimate and its error is at most the
        whole gap. Raise the gap towards the tolerance and the estimate becomes
        able to move a healthy lamp past the bound on its own, which would turn
        a deploy into an alert. A third leaves room for several in a day.
        """
        assert MAX_RESTART_GAP_MINUTES * 3 <= ON_TIME_TOLERANCE_MINUTES


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
        """So a fixture cannot claim sleep-sensitivity without naming whose
        sleep, and a fixed window has nowhere to put a presence entity."""
        awake_aware = LightFixture(
            name="study_shelf", switch_entity="switch.study_lamp", window=STUDY
        )
        fixed = LightFixture(
            name="spare_shelf", switch_entity="switch.spare_lamp", window=SPARE
        )
        assert awake_aware.watched_entities == (PRESENCE,)
        assert awake_aware.is_sleep_sensitive
        assert fixed.watched_entities == ()
        assert not fixed.is_sleep_sensitive
        assert not fixed.is_quiet({PRESENCE: "asleep"})
