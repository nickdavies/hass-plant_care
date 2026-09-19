"""Pushing new feed items to a notify action.

The feed is a sensor, and a sensor is only read by whoever happens to be
looking. A silent probe found on the dashboard three days later has already
cost the plant, so anything that *appears* in the feed is pushed once, the
moment it appears.

Once, not on every recompute. The feed is rebuilt on every probe reading and
every half hour, and days-since ticks up on each rebuild, so an item is
identified by what it is about — plant, kind, task or fixture — never by its
text. A key that leaves the feed is forgotten, so the same fault coming back is
announced again. The announced set is persisted, because Home Assistant
restarts far more often than a plant is watered and every restart would
otherwise re-send everything still outstanding.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import CoreState, HomeAssistant, callback
from homeassistant.exceptions import ServiceNotFound
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.event import async_track_time_interval

from .const import RECOMPUTE_INTERVAL, SIGNAL_CARE_UPDATED
from .feed import all_items

if TYPE_CHECKING:
    from . import PlantCareData

_LOGGER = logging.getLogger(__name__)

TITLE = "Plants"


def item_key(item: dict[str, Any]) -> str:
    """What an item is about, stable across recomputes.

    `plant` is `None` for a system fault, which stringifies fine — it still
    cannot collide with a real plant, since plant names are slugs.
    """
    subject = item.get("task") or item.get("fixture") or ""
    return f"{item.get('plant')}.{item['kind']}.{subject}"


def item_line(item: dict[str, Any]) -> str:
    """One line of the message. The label alone for a care item, the remedy
    alongside it for a fault, because the fault's detail is what says what to
    do about the number."""
    who = item.get("name") or TITLE
    line = f"{who}: {item['label']}"
    if item["kind"] == "care":
        line += f" ({item['days']:.0f} days, every {item['every']})"
    elif item.get("detail"):
        line += f" — {item['detail']}"
    return line


class FeedNotifier:
    """Watches the same sources the outstanding sensor does and pushes the
    difference."""

    def __init__(self, hass: HomeAssistant, data: PlantCareData, action: str) -> None:
        self._hass = hass
        self._data = data
        self._domain, self._service = action.split(".", 1)
        self._announced: set[str] = set()
        self._unsubs: list[Callable[[], None]] = []

    async def async_start(self) -> None:
        self._announced = self._data.event_log.announced()

        self._unsubs.append(
            async_dispatcher_connect(self._hass, SIGNAL_CARE_UPDATED, self._evaluate)
        )
        self._unsubs.append(
            async_track_time_interval(
                self._hass, self._handle_interval, RECOMPUTE_INTERVAL
            )
        )
        for coordinator in self._data.coordinators.values():
            self._unsubs.append(coordinator.async_add_listener(self._evaluate))
        for dli in self._data.dli_coordinators.values():
            self._unsubs.append(dli.async_add_listener(self._evaluate))
        for controller in self._data.light_controllers.values():
            self._unsubs.append(controller.async_add_listener(self._evaluate))

        # The notify platform loads alongside this one, so a push during setup
        # would find no service. What is outstanding at boot is announced once
        # Home Assistant is up, which is also when a phone can be reached.
        if self._hass.state is CoreState.running:
            self._evaluate()
        else:
            self._hass.bus.async_listen_once(
                EVENT_HOMEASSISTANT_STARTED, self._handle_started
            )

    @callback
    def async_stop(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()

    @callback
    def _handle_started(self, _event) -> None:
        self._evaluate()

    @callback
    def _handle_interval(self, _now) -> None:
        self._evaluate()

    @callback
    def _evaluate(self) -> None:
        """Diff the feed against what has been announced, in the callback.

        The set is updated here, synchronously, before anything is awaited:
        several sources fire in one burst, and two evaluations that both saw a
        new item before either had recorded it would push it twice.
        """
        items = all_items(self._data)
        current = {item_key(item): item for item in items}
        new = [item for key, item in current.items() if key not in self._announced]
        changed = set(current) != self._announced
        self._announced = set(current)
        if changed:
            self._hass.async_create_task(self._async_push(new))

    async def _async_push(self, new: list[dict[str, Any]]) -> None:
        if new:
            try:
                await self._hass.services.async_call(
                    self._domain,
                    self._service,
                    {
                        "title": TITLE,
                        "message": "\n".join(item_line(item) for item in new),
                    },
                    blocking=True,
                )
            except ServiceNotFound:
                # Forget these so the next recompute tries again, rather than
                # marking them announced when nobody was told.
                self._announced.difference_update(item_key(item) for item in new)
                _LOGGER.warning(
                    "plant_care: notify action %s.%s does not exist; %d feed item(s) "
                    "not pushed",
                    self._domain,
                    self._service,
                    len(new),
                )
        await self._data.event_log.async_set_announced(self._announced)
