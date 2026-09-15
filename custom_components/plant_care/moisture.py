"""Driving a plant's moisture monitor from live probe state.

One coordinator per plant with a probe. It subscribes to the probe entity, feeds
every reading into the pure [`MoistureMonitor`], persists a detected watering,
and tells the dependent entities to re-read.

All the judgement lives in the monitor; this is the wiring that gets readings to
it. That split is what makes the two-hour confirm window testable in
microseconds rather than by waiting.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import (
    Event,
    EventStateChangedData,
    EventStateReportedData,
    HomeAssistant,
    callback,
)
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_state_report_event,
)
from homeassistant.util import dt as dt_util

from .model import Moisture, Plant, Policy
from .model.signals import MoistureMonitor, MoistureSignals
from .store import EventLog

_LOGGER = logging.getLogger(__name__)

# Readings that carry no number. Treated as "no reading", never as a value:
# a probe that drops out must not look like a bone-dry pot.
NON_VALUES = {STATE_UNAVAILABLE, STATE_UNKNOWN, "none", ""}


class MoistureCoordinator:
    """Feeds one plant's monitor and fans out updates."""

    def __init__(
        self,
        hass: HomeAssistant,
        plant: Plant,
        moisture: Moisture,
        policy: Policy,
        event_log: EventLog,
    ) -> None:
        self._hass = hass
        self._plant = plant
        self._moisture = moisture
        self._event_log = event_log
        self._listeners: list[Callable[[], None]] = []
        self._unsubs: list[Callable[[], None]] = []
        self.monitor = MoistureMonitor(
            policy=policy, probe=moisture.probe, calibration=moisture.calibration
        )

    @property
    def plant(self) -> Plant:
        return self._plant

    @property
    def signals(self) -> MoistureSignals:
        return self.monitor.signals

    @property
    def needs_water(self) -> bool:
        return self.monitor.needs_water

    def async_add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(listener)

        def remove() -> None:
            self._listeners.remove(listener)

        return remove

    @callback
    def _notify(self) -> None:
        for listener in self._listeners:
            listener()

    async def async_start(self) -> None:
        """Begin tracking.

        The latch is restored from the store, so a plant flagged before a
        restart stays flagged. The rolling window is not restored — it would be
        stale, and it refills within one window.
        """
        self.monitor.restore(
            needs_water=self._event_log.needs_water(self._plant.name),
            last_watered=self._event_log.last_watered(self._plant.name),
        )

        entity_id = self._moisture.moisture_entity.entity_id

        # Seed from whatever the probe is already reporting, so a reload does
        # not sit blank until the next heartbeat.
        current = self._hass.states.get(entity_id)
        if current is not None:
            self._ingest(current.state, current.last_reported)

        self._unsubs.append(
            async_track_state_change_event(
                self._hass, [entity_id], self._handle_state_change
            )
        )
        # Both event types, and the second is not optional. Home Assistant fires
        # `state_changed` only when the value actually differs; a probe
        # re-reporting the same number fires `state_reported` instead. Without
        # this the window would empty and the confirm clock would stall, so a
        # pot sitting at one steady reading would never flag no matter how long
        # it sat there.
        self._unsubs.append(
            async_track_state_report_event(
                self._hass, [entity_id], self._handle_state_report
            )
        )
        self._notify()

    @callback
    def async_stop(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()

    @callback
    def _handle_state_report(self, event: Event[EventStateReportedData]) -> None:
        """The probe re-reported the same value. Still a reading."""
        self._observe_state(event.data["new_state"])

    @callback
    def _handle_state_change(self, event: Event[EventStateChangedData]) -> None:
        self._observe_state(event.data["new_state"])

    @callback
    def _observe_state(self, new_state) -> None:
        if new_state is None:
            return

        was_flagged = self.monitor.needs_water
        # `last_reported`, not `last_updated`: the latter only advances when the
        # value actually changes, so a probe repeating the same number would
        # hand every reading the timestamp of the first one and the confirm
        # clock would never move. This is the same distinction the hand-written
        # health checks drew — one answers "is the radio alive", the other "is
        # the reading moving".
        watered = self._ingest(new_state.state, new_state.last_reported)

        # Persisting is async; this callback is not. Neither write is needed to
        # compute anything — the monitor already holds both — so the background
        # write only affects what survives a restart.
        if watered:
            self._hass.async_create_task(self._async_record_watering())
        if self.monitor.needs_water != was_flagged:
            self._hass.async_create_task(
                self._event_log.async_set_needs_water(
                    self._plant.name, self.monitor.needs_water
                )
            )

        self._notify()

    def _ingest(self, raw: str, when) -> bool:
        if raw in NON_VALUES:
            return False
        try:
            value = float(raw)
        except (TypeError, ValueError):
            _LOGGER.debug(
                "plant_care: %s ignoring non-numeric probe reading %r",
                self._plant.name,
                raw,
            )
            return False

        return self.monitor.observe(dt_util.as_utc(when), value)

    async def _async_record_watering(self) -> None:
        when = self.monitor.last_watered or dt_util.utcnow()
        await self._event_log.async_record_watering(self._plant.name, when)
        _LOGGER.debug("plant_care: %s watering detected at %s", self._plant.name, when)
        self._notify()
