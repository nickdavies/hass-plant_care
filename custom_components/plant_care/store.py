"""Durable record of when things last happened to a plant.

Two kinds of event share this: a care task someone marked done, and a watering
the probe detected. They arrive differently but are the same shape — "when did
this last happen" — and both are the one piece of data in the system that cannot
be recomputed. A moisture reading comes back on the next heartbeat; when you
last fed a plant exists nowhere but here.

Keys are namespaced by kind, so a care task named `water` cannot collide with
the detected `watered` event.

The in-memory half is kept free of Home Assistant so its behaviour — what an
unrecorded event reads as, how days-since is computed — is unit testable. The
`Store` is injected.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, time
from enum import Enum
from typing import Any, Protocol

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
STORAGE_KEY = "plant_care.events"

WATERED = "watered"
"""The detected-watering event. Not a care task — nothing presses a button for
it, the probe sees it."""


class EventKind(Enum):
    """What produced an event. Part of the storage key, so the namespaces can
    never collide."""

    CARE = "care"
    SYSTEM = "system"
    LIGHT = "light"
    """Keyed by *fixture* name rather than plant name — a fixture is shared, so
    hanging its events off one of the plants under it would be arbitrary."""


class StoreLike(Protocol):
    """The slice of `homeassistant.helpers.storage.Store` this needs.

    Narrow on purpose: it is the whole Home Assistant surface of this module,
    and a fake satisfying two methods makes the logic testable without one.
    """

    async def async_load(self) -> Any: ...

    async def async_save(self, data: Any) -> None: ...


def event_key(plant: str, kind: EventKind, name: str) -> str:
    return f"{plant}.{kind.value}.{name}"


SECTION_EVENTS = "events"
SECTION_FLAGS = "flags"
SECTION_DLI = "dli"
SECTION_INTERVALS = "intervals"
"""Closed stretches of a bad state, per plant per budget — the week of probe
dropouts or wet soil that a burn rate is computed over. Persisted because the
slow burn exists to see across days, and Home Assistant restarts more often
than that."""
SECTION_ANNOUNCED = "announced"
"""Feed items the notifier has already pushed, per notify action. Persisted so
a restart does not push everything still outstanding a second time."""
SECTION_ON_TIME = "on_time"
"""How long each fixture has run today, as of the last flush.

Persisted for the reason `SECTION_DLI` is. Home Assistant restarts several times
over an afternoon anyone spends editing its config, and a counter that begins
again at zero on each one reports a lamp that ran all morning as one that has
barely come on — and takes the on-time check down with it, since a comparison
that only ever sees the last twenty minutes cannot see a bulb that died at nine.
"""

FLAG_NEEDS_WATER = "needs_water"

KILLSWITCH_SINCE = "killswitch_since"
"""When a fixture's killswitch was last turned on.

Stored rather than read off the entity's `last_changed`, and that is not
fussiness. `RestoreState` brings the switch's *value* back after a restart but
its `last_changed` becomes the restore time, so a killswitch somebody flipped
two days ago would read as brand new every time Home Assistant restarted — and
the 48h catchall that exists to catch exactly that forgetfulness would never
fire. Same class of trap as an `input_datetime` defaulting to today at midnight.
"""


@dataclass(frozen=True)
class OnTimeRecord:
    """One fixture's on-time accounting, as it stood at the last flush.

    Two spans, not one, and that is the whole reason this is a record rather
    than a number. `minutes` is the day, which is what the dashboard asks for.
    The comparison against the window has to run over a stretch that was
    actually watched, so it runs from `since`, with the minutes banked before
    it held in `counted_from` and subtracted back out.

    On an ordinary day the two are the same thing and `counted_from` is zero.
    They only part company after an outage too long to account for.
    """

    day: date
    minutes: float
    """On-time for the whole day."""
    since: time
    """Local time-of-day the current comparison span opened — midnight
    ordinarily, the moment Home Assistant came back after a long outage."""
    counted_from: float
    """Minutes already banked when that span opened."""
    seen: datetime
    """When this was written. The gap between it and the next start-up is what
    a restart cost, and what decides whether the break can be credited."""
    on: bool
    """Whether the lamp was on at `seen`.

    Stored rather than read off the switch at start-up because the switch
    arrives from MQTT discovery some time *after* the controller does, so at
    the moment the decision is made there is nothing there to read.
    """


class EventLog:
    """When each event last happened, per plant — plus latched flags.

    Flags are here rather than restored through an entity because a plant that
    is still calibrating has no needs-water entity to restore from, and tying
    the monitor's lifecycle to whether a particular entity happens to exist is
    the kind of coupling that breaks quietly when a plant's config changes.
    """

    def __init__(self, store: StoreLike) -> None:
        self._store = store
        self._events: dict[str, datetime] = {}
        self._flags: dict[str, bool] = {}
        self._dli: dict[str, dict[date, float]] = {}
        self._intervals: dict[str, list[tuple[datetime, datetime]]] = {}
        self._announced: dict[str, set[str]] = {}
        self._on_time: dict[str, OnTimeRecord] = {}

    async def async_load(self) -> None:
        raw: Mapping[str, Any] | None = await self._store.async_load()
        if not raw:
            return

        for key, value in raw.get(SECTION_EVENTS, {}).items():
            try:
                self._events[key] = datetime.fromisoformat(value)
            except (TypeError, ValueError):
                # One unreadable entry must not take the rest of the log with
                # it: losing every plant's history because a single timestamp
                # was corrupt is a far worse failure than losing one.
                _LOGGER.warning(
                    "plant_care: discarding unreadable timestamp for %s: %r", key, value
                )

        for key, value in raw.get(SECTION_FLAGS, {}).items():
            if isinstance(value, bool):
                self._flags[key] = value

        for plant, days in raw.get(SECTION_DLI, {}).items():
            bucket = self._dli.setdefault(plant, {})
            for day, value in days.items():
                try:
                    bucket[date.fromisoformat(day)] = float(value)
                except (TypeError, ValueError):
                    _LOGGER.warning(
                        "plant_care: discarding unreadable dli entry %s/%s: %r",
                        plant,
                        day,
                        value,
                    )

        for key, pairs in raw.get(SECTION_INTERVALS, {}).items():
            restored: list[tuple[datetime, datetime]] = []
            for pair in pairs:
                try:
                    start, end = pair
                    restored.append(
                        (datetime.fromisoformat(start), datetime.fromisoformat(end))
                    )
                except (TypeError, ValueError):
                    _LOGGER.warning(
                        "plant_care: discarding unreadable interval for %s: %r",
                        key,
                        pair,
                    )
            self._intervals[key] = restored

        for fixture, record in raw.get(SECTION_ON_TIME, {}).items():
            try:
                self._on_time[fixture] = OnTimeRecord(
                    day=date.fromisoformat(record["day"]),
                    minutes=float(record["minutes"]),
                    since=time.fromisoformat(record["since"]),
                    counted_from=float(record["counted_from"]),
                    seen=datetime.fromisoformat(record["seen"]),
                    on=bool(record["on"]),
                )
            except (KeyError, TypeError, ValueError):
                # Dropping it costs one fixture the morning it has already
                # counted; a half-read record would misreport that fixture
                # until midnight, which is worse and much harder to notice.
                _LOGGER.warning(
                    "plant_care: discarding unreadable on-time record for %s: %r",
                    fixture,
                    record,
                )

        announced = raw.get(SECTION_ANNOUNCED, {})
        if isinstance(announced, Mapping):
            for action, keys in announced.items():
                if isinstance(action, str) and isinstance(keys, list):
                    self._announced[action] = {k for k in keys if isinstance(k, str)}
        elif announced:
            # The flat list from before per-owner routing: nothing says which
            # phone it went to, so one repeat beats a guess.
            _LOGGER.warning(
                "plant_care: discarding the announced set written before per-owner "
                "routing; anything still outstanding will be pushed once more"
            )

    async def _async_save(self) -> None:
        await self._store.async_save(
            {
                SECTION_EVENTS: {
                    key: value.isoformat() for key, value in self._events.items()
                },
                SECTION_FLAGS: dict(self._flags),
                SECTION_DLI: {
                    plant: {day.isoformat(): value for day, value in days.items()}
                    for plant, days in self._dli.items()
                },
                SECTION_INTERVALS: {
                    key: [[start.isoformat(), end.isoformat()] for start, end in pairs]
                    for key, pairs in self._intervals.items()
                },
                SECTION_ANNOUNCED: {
                    action: sorted(keys)
                    for action, keys in self._announced.items()
                    if keys
                },
                SECTION_ON_TIME: {
                    fixture: {
                        "day": record.day.isoformat(),
                        "minutes": round(record.minutes, 3),
                        "since": record.since.isoformat(),
                        "counted_from": round(record.counted_from, 3),
                        "seen": record.seen.isoformat(),
                        "on": record.on,
                    }
                    for fixture, record in self._on_time.items()
                },
            }
        )

    async def async_record(
        self, plant: str, kind: EventKind, name: str, when: datetime
    ) -> None:
        self._events[event_key(plant, kind, name)] = when
        await self._async_save()

    async def async_clear(self, plant: str, kind: EventKind, name: str) -> None:
        """Forget an event. Distinct from recording `None`, which the timestamps
        cannot express."""
        if self._events.pop(event_key(plant, kind, name), None) is not None:
            await self._async_save()

    async def async_set_flag(self, plant: str, flag: str, value: bool) -> None:
        self._flags[f"{plant}.{flag}"] = value
        await self._async_save()

    def flag(self, plant: str, flag: str, default: bool = False) -> bool:
        return self._flags.get(f"{plant}.{flag}", default)

    def last(self, plant: str, kind: EventKind, name: str) -> datetime | None:
        """`None` means never — not "at the epoch", and not today.

        The hand-written version of this stored last-done in an
        `input_datetime`, which Home Assistant defaults to *today at midnight*
        when never set. Testing the timestamp alone therefore reported a
        fraction of a day since something that never happened, and needed an
        exact-midnight sentinel to work around. A real `None` removes the class
        of problem.
        """
        return self._events.get(event_key(plant, kind, name))

    def days_since(
        self, plant: str, kind: EventKind, name: str, now: datetime
    ) -> float | None:
        last = self.last(plant, kind, name)
        if last is None:
            return None
        return round((now - last).total_seconds() / 86400.0, 1)

    # ---- Convenience for the two callers -------------------------------

    async def async_mark_care_done(self, plant: str, task: str, when: datetime) -> None:
        await self.async_record(plant, EventKind.CARE, task, when)

    def care_days_since(self, plant: str, task: str, now: datetime) -> float | None:
        return self.days_since(plant, EventKind.CARE, task, now)

    async def async_record_watering(self, plant: str, when: datetime) -> None:
        await self.async_record(plant, EventKind.SYSTEM, WATERED, when)

    def last_watered(self, plant: str) -> datetime | None:
        return self.last(plant, EventKind.SYSTEM, WATERED)

    def days_since_watered(self, plant: str, now: datetime) -> float | None:
        return self.days_since(plant, EventKind.SYSTEM, WATERED, now)

    async def async_set_needs_water(self, plant: str, value: bool) -> None:
        await self.async_set_flag(plant, FLAG_NEEDS_WATER, value)

    def needs_water(self, plant: str) -> bool:
        return self.flag(plant, FLAG_NEEDS_WATER)

    # ---- Lights --------------------------------------------------------

    async def async_set_killswitch_since(
        self, fixture: str, when: datetime | None
    ) -> None:
        if when is None:
            await self.async_clear(fixture, EventKind.LIGHT, KILLSWITCH_SINCE)
        else:
            await self.async_record(fixture, EventKind.LIGHT, KILLSWITCH_SINCE, when)

    def killswitch_since(self, fixture: str) -> datetime | None:
        return self.last(fixture, EventKind.LIGHT, KILLSWITCH_SINCE)

    async def async_record_on_time(self, fixture: str, record: OnTimeRecord) -> None:
        """Write down where a fixture's day has got to.

        One record per fixture, replaced in place: nothing reads yesterday's,
        and the day it belongs to is on the record, so a stale one is spotted
        rather than inherited.
        """
        self._on_time[fixture] = record
        await self._async_save()

    def on_time(self, fixture: str) -> OnTimeRecord | None:
        """`None` means no run has written one for this fixture yet."""
        return self._on_time.get(fixture)

    # ---- Daily light integral ------------------------------------------

    async def async_record_dli(
        self, plant: str, day: date, value: float, keep_days: int
    ) -> None:
        """Write one day's accumulation, trimming anything past the window.

        Today's partial total is written here too, under today's date, so a
        restart mid-afternoon resumes rather than starting the day at zero. A
        day that restarted to zero would look like a severe shortfall and burn
        budget for a failure that never happened.
        """
        bucket = self._dli.setdefault(plant, {})
        bucket[day] = round(value, 3)

        for stale in sorted(bucket)[: max(0, len(bucket) - keep_days)]:
            del bucket[stale]

        await self._async_save()

    def dli_history(self, plant: str) -> dict[date, float]:
        return dict(self._dli.get(plant, {}))

    # ---- Budget intervals ----------------------------------------------

    async def async_record_intervals(
        self, plant: str, budget: str, pairs: list[tuple[datetime, datetime]]
    ) -> None:
        """Replace one budget's closed stretches. The caller already keeps the
        list trimmed to its window, so this is a write, not a merge."""
        self._intervals[f"{plant}.{budget}"] = list(pairs)
        await self._async_save()

    def intervals(self, plant: str, budget: str) -> list[tuple[datetime, datetime]]:
        return list(self._intervals.get(f"{plant}.{budget}", []))

    # ---- The notifier's memory -----------------------------------------

    async def async_set_announced(self, announced: Mapping[str, Iterable[str]]) -> None:
        """Replace what has been pushed, per notify action."""
        self._announced = {action: set(keys) for action, keys in announced.items()}
        await self._async_save()

    def announced(self) -> dict[str, set[str]]:
        return {action: set(keys) for action, keys in self._announced.items()}
