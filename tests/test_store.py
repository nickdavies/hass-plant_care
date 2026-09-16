"""The durable record.

Everything here is state that cannot be recomputed. A moisture reading comes
back on the next heartbeat; when you last fed a plant, when a killswitch was
flipped, and how much light a plant got last Tuesday exist nowhere else.

So the tests that matter are the round trips: what is written must come back the
same after a restart, and one corrupt entry must never take the rest with it.
The `Store` is injected precisely so this can be checked in microseconds rather
than by restarting Home Assistant.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from datetime import UTC, date, datetime, timedelta
from typing import Any, TypeVar

import pytest

from custom_components.plant_care.store import (
    KILLSWITCH_SINCE,
    EventKind,
    EventLog,
    event_key,
)

NOW = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)

T = TypeVar("T")


def run(coro: Coroutine[Any, Any, T]) -> T:
    """Drive one of `EventLog`'s coroutines to completion.

    The tests here are plain synchronous functions on purpose. This suite
    installs nothing but pytest and voluptuous — that is what keeps
    `test_no_ha_imports` meaningful — and an async test would need a plugin.
    `EventLog` is async only because `Store` is, so there is nothing to
    schedule around.
    """
    return asyncio.run(coro)


class FakeStore:
    """The two methods `EventLog` actually uses, backed by a dict.

    Keeping a handle on `data` is what makes a restart expressible: build a
    second `EventLog` over the same store and load it.
    """

    def __init__(self, data: Any = None) -> None:
        self.data = data
        self.saves = 0

    async def async_load(self) -> Any:
        return self.data

    async def async_save(self, data: Any) -> None:
        self.data = data
        self.saves += 1


def restarted(store: FakeStore) -> EventLog:
    """What the next run sees: a fresh log over the same bytes."""
    log = EventLog(store)
    run(log.async_load())
    return log


class TestCareEvents:
    def test_a_recorded_event_survives_a_restart(self) -> None:
        store = FakeStore()
        log = EventLog(store)
        run(log.async_mark_care_done("monstera", "feed", NOW))

        assert restarted(store).last("monstera", EventKind.CARE, "feed") == NOW

    def test_never_done_is_none_not_today(self) -> None:
        """The hand-written version stored this in an `input_datetime`, which
        Home Assistant defaults to *today at midnight* when unset — so it
        reported a fraction of a day since something that never happened, and
        needed an exact-midnight sentinel to work around. A real `None` removes
        the class of problem."""
        log = EventLog(FakeStore())
        assert log.last("monstera", EventKind.CARE, "feed") is None
        assert log.care_days_since("monstera", "feed", NOW) is None

    def test_days_since_is_measured_not_counted(self) -> None:
        log = EventLog(FakeStore())
        run(log.async_mark_care_done("monstera", "feed", NOW - timedelta(hours=36)))
        assert log.care_days_since("monstera", "feed", NOW) == 1.5

    def test_a_care_task_named_water_cannot_collide_with_the_detected_one(
        self,
    ) -> None:
        """A sensorless plant is allowed a `water` care task, and every plant
        with a probe has a detected `watered` event. Sharing a namespace would
        have one silently answer for the other."""
        log = EventLog(FakeStore())
        run(log.async_mark_care_done("front_step_pot", "water", NOW))

        assert log.last_watered("front_step_pot") is None
        assert log.care_days_since("front_step_pot", "water", NOW) == 0.0
        assert event_key("p", EventKind.CARE, "water") != event_key(
            "p", EventKind.SYSTEM, "watered"
        )


class TestFlags:
    def test_the_needs_water_latch_survives_a_restart(self) -> None:
        """A plant flagged before a restart stays flagged. It is a latch, not a
        measurement — the pot is still dry after a reboot."""
        store = FakeStore()
        log = EventLog(store)
        run(log.async_set_needs_water("passionfruit", True))

        assert restarted(store).needs_water("passionfruit")

    def test_an_unflagged_plant_is_not_flagged(self) -> None:
        log = EventLog(FakeStore())
        assert not log.needs_water("passionfruit")


class TestKillswitchStamp:
    def test_the_stamp_survives_a_restart_unchanged(self) -> None:
        """The whole reason this is not read off the entity's `last_changed`.

        `RestoreState` brings the switch's *value* back but stamps it with the
        restore time, so a two-day-old killswitch would read as brand new on
        every restart and the 48-hour backstop that exists to catch exactly that
        forgetfulness would never fire.
        """
        store = FakeStore()
        log = EventLog(store)
        flipped = NOW - timedelta(days=2)
        run(log.async_set_killswitch_since("study_shelf", flipped))

        assert restarted(store).killswitch_since("study_shelf") == flipped

    def test_clearing_it_removes_it_rather_than_writing_a_sentinel(self) -> None:
        """`None` means "not frozen"; the timestamps cannot express that as a
        value, so the entry has to go."""
        store = FakeStore()
        log = EventLog(store)
        run(log.async_set_killswitch_since("study_shelf", NOW))
        run(log.async_set_killswitch_since("study_shelf", None))

        assert restarted(store).killswitch_since("study_shelf") is None

    def test_fixtures_are_independent(self) -> None:
        log = EventLog(FakeStore())
        run(log.async_set_killswitch_since("study_shelf", NOW))

        assert log.killswitch_since("spare_shelf") is None

    def test_clearing_something_never_set_writes_nothing(self) -> None:
        store = FakeStore()
        log = EventLog(store)
        run(log.async_clear("study_shelf", EventKind.LIGHT, KILLSWITCH_SINCE))

        assert store.saves == 0


class TestDliHistory:
    def test_days_survive_a_restart_as_dates_not_strings(self) -> None:
        """The burn-rate engine windows on `date` arithmetic, so a string here
        would fail comparison rather than compare wrongly — but only once a
        month had accumulated."""
        store = FakeStore()
        log = EventLog(store)
        run(log.async_record_dli("monstera", date(2026, 9, 14), 6.4, keep_days=29))

        history = restarted(store).dli_history("monstera")
        assert history == {date(2026, 9, 14): 6.4}

    def test_rewriting_today_replaces_rather_than_appends(self) -> None:
        """Today's partial total is written every few minutes; each write is the
        running total, not an increment."""
        log = EventLog(FakeStore())
        for value in (1.0, 2.0, 3.5):
            run(log.async_record_dli("monstera", date(2026, 9, 15), value, 29))

        assert log.dli_history("monstera") == {date(2026, 9, 15): 3.5}

    def test_the_window_is_trimmed_from_the_oldest_end(self) -> None:
        """Otherwise a year of daily values accumulates in a file that is read
        in full at every start-up."""
        log = EventLog(FakeStore())
        for day in range(1, 11):
            run(log.async_record_dli("monstera", date(2026, 9, day), float(day), 3))

        assert sorted(log.dli_history("monstera")) == [
            date(2026, 9, 8),
            date(2026, 9, 9),
            date(2026, 9, 10),
        ]

    def test_plants_do_not_share_a_history(self) -> None:
        log = EventLog(FakeStore())
        run(log.async_record_dli("monstera", date(2026, 9, 14), 6.4, 29))

        assert log.dli_history("ficus_alii") == {}

    def test_the_returned_history_is_a_copy(self) -> None:
        """A caller mutating it must not silently rewrite the record."""
        log = EventLog(FakeStore())
        run(log.async_record_dli("monstera", date(2026, 9, 14), 6.4, 29))

        log.dli_history("monstera")[date(2026, 9, 14)] = 99.0
        assert log.dli_history("monstera")[date(2026, 9, 14)] == 6.4


class TestCorruption:
    """One bad entry must not take the rest of the log with it.

    Losing every plant's history because a single value was unreadable is a far
    worse failure than losing one, and it is the kind that only shows up months
    later when the file has something odd in it.
    """

    def test_an_unreadable_timestamp_discards_only_itself(self) -> None:
        store = FakeStore(
            {
                "events": {
                    event_key("monstera", EventKind.CARE, "feed"): "not a date",
                    event_key("passionfruit", EventKind.CARE, "feed"): NOW.isoformat(),
                }
            }
        )
        log = restarted(store)

        assert log.last("monstera", EventKind.CARE, "feed") is None
        assert log.last("passionfruit", EventKind.CARE, "feed") == NOW

    def test_an_unreadable_dli_value_discards_only_itself(self) -> None:
        store = FakeStore(
            {"dli": {"monstera": {"2026-09-14": "dark", "2026-09-15": 6.4}}}
        )
        assert restarted(store).dli_history("monstera") == {date(2026, 9, 15): 6.4}

    def test_an_unreadable_dli_date_discards_only_itself(self) -> None:
        store = FakeStore({"dli": {"monstera": {"tuesday": 3.0, "2026-09-15": 6.4}}})
        assert restarted(store).dli_history("monstera") == {date(2026, 9, 15): 6.4}

    def test_a_non_boolean_flag_is_ignored(self) -> None:
        store = FakeStore({"flags": {"monstera.needs_water": "yes"}})
        assert not restarted(store).needs_water("monstera")

    def test_an_empty_store_is_not_an_error(self) -> None:
        """First run, and the case after the storage file is deleted."""
        for empty in (None, {}):
            log = restarted(FakeStore(empty))
            assert log.last_watered("monstera") is None

    def test_missing_sections_are_not_an_error(self) -> None:
        """A file written by an older version has no `dli` section at all."""
        store = FakeStore({"events": {}})
        log = restarted(store)

        assert log.dli_history("monstera") == {}
        assert log.killswitch_since("study_shelf") is None


class TestEverythingTogether:
    def test_a_full_restart_brings_back_every_kind_of_state(self) -> None:
        """The one that would have caught a section dropped from `_async_save`.

        Each of these is written through a different method, and the save writes
        all three sections every time — so forgetting one would only show up
        after a restart, which is exactly when nobody is looking.
        """
        store = FakeStore()
        log = EventLog(store)

        run(log.async_mark_care_done("monstera", "feed", NOW))
        run(log.async_record_watering("passionfruit", NOW))
        run(log.async_set_needs_water("passionfruit", True))
        run(log.async_set_killswitch_since("study_shelf", NOW))
        run(log.async_record_dli("monstera", date(2026, 9, 14), 6.4, 29))

        after = restarted(store)

        assert after.care_days_since("monstera", "feed", NOW) == 0.0
        assert after.last_watered("passionfruit") == NOW
        assert after.needs_water("passionfruit")
        assert after.killswitch_since("study_shelf") == NOW
        assert after.dli_history("monstera") == {date(2026, 9, 14): 6.4}

    def test_the_stored_shape_is_json_safe(self) -> None:
        """`Store` serialises to JSON, so a `date` or `datetime` key reaching it
        would raise at save time — in a background task, where it would be a log
        line nobody reads rather than a failure anyone notices."""
        import json

        store = FakeStore()
        log = EventLog(store)
        run(log.async_mark_care_done("monstera", "feed", NOW))
        run(log.async_set_killswitch_since("study_shelf", NOW))
        run(log.async_record_dli("monstera", date(2026, 9, 14), 6.4, 29))

        json.dumps(store.data)  # raises if anything in there is not JSON

    def test_reloading_does_not_double_count(self) -> None:
        store = FakeStore()
        log = EventLog(store)
        run(log.async_record_dli("monstera", date(2026, 9, 14), 6.4, 29))

        run(log.async_load())  # as if loaded twice

        assert log.dli_history("monstera") == {date(2026, 9, 14): 6.4}


@pytest.mark.parametrize(
    ("kind", "name"),
    [
        (EventKind.CARE, "feed"),
        (EventKind.SYSTEM, "watered"),
        (EventKind.LIGHT, KILLSWITCH_SINCE),
    ],
)
def test_every_namespace_round_trips(kind: EventKind, name: str) -> None:
    store = FakeStore()
    log = EventLog(store)
    run(log.async_record("subject", kind, name, NOW))

    assert restarted(store).last("subject", kind, name) == NOW
