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
from collections.abc import Mapping
from datetime import date, datetime
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
