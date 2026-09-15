"""Durable record of when each care task was last done.

A `Store` rather than entity restore state, because this is the one piece of
data in the system that cannot be recomputed from anything else. A moisture
reading comes back on the next heartbeat; "when did I last feed this" exists
nowhere but here, and losing it silently resets every task's clock.

The in-memory half is kept free of Home Assistant so its behaviour — what an
unrecorded task reads as, how days-since is computed — is unit testable. The
`Store` is injected.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import datetime
from typing import Any, Protocol

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
STORAGE_KEY = "plant_care.care_log"


class StoreLike(Protocol):
    """The slice of `homeassistant.helpers.storage.Store` this needs.

    Narrow on purpose: it is the whole Home Assistant surface of this module,
    and a fake satisfying three methods makes the logic testable without one.
    """

    async def async_load(self) -> Any: ...

    async def async_save(self, data: Any) -> None: ...


def log_key(plant: str, task: str) -> str:
    """One flat key per (plant, task).

    Flat rather than nested so a renamed plant orphans its entries visibly
    instead of merging into another plant's history.
    """
    return f"{plant}.{task}"


class CareLog:
    """When each care task was last marked done."""

    def __init__(self, store: StoreLike) -> None:
        self._store = store
        self._done: dict[str, datetime] = {}

    async def async_load(self) -> None:
        raw: Mapping[str, Any] | None = await self._store.async_load()
        if not raw:
            return

        for key, value in raw.items():
            try:
                self._done[key] = datetime.fromisoformat(value)
            except (TypeError, ValueError):
                # One unreadable entry must not take the rest of the log with
                # it: losing every plant's history because one timestamp was
                # corrupt would be a far worse failure than losing one.
                _LOGGER.warning(
                    "plant_care: discarding unreadable last-done value for %s: %r",
                    key,
                    value,
                )

    async def async_mark_done(self, plant: str, task: str, when: datetime) -> None:
        self._done[log_key(plant, task)] = when
        await self._store.async_save(
            {key: value.isoformat() for key, value in self._done.items()}
        )

    def last_done(self, plant: str, task: str) -> datetime | None:
        """`None` means never done — not "done at the epoch", and not today.

        The hand-written version of this system stored last-done in an
        `input_datetime`, which Home Assistant defaults to *today at midnight*
        when it has never been set. Testing the timestamp alone therefore
        reported a fraction of a day since a watering that never happened, and
        needed an exact-midnight sentinel to work around. A real `None` removes
        the whole class of problem.
        """
        return self._done.get(log_key(plant, task))

    def days_since(self, plant: str, task: str, now: datetime) -> float | None:
        last = self.last_done(plant, task)
        if last is None:
            return None
        return round((now - last).total_seconds() / 86400.0, 1)
