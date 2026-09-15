"""Driving one grow light fixture, and watching what actually happened.

Two jobs, in one object because they share a subscription to the same switch:

1. Re-evaluate the window every minute and whenever presence changes, and drive
   the switch when it disagrees.
2. Count how long the switch was *actually* on, so the outcome can be compared
   against what the window allows. That is the half that catches a bulb that
   died behind a live outlet, an outlet that fell off zigbee2mqtt, and a
   killswitch somebody forgot — none of which the first half can see.

The decision itself lives in `model.light`, where it is a pure function of a
time, a weekday and an asleep flag, so every boundary of the four-bound window
is testable without a running Home Assistant, a clock, or a lamp.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date, datetime, time, timedelta

from homeassistant.const import SERVICE_TURN_OFF, SERVICE_TURN_ON, STATE_ON
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.exceptions import ServiceNotFound
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers.start import async_at_started
from homeassistant.util import dt as dt_util

from .model import LightFixture, OnTimeDeviation, Weekday, is_asleep, on_time_deviation
from .model.health import HealthIssue, IssueKind
from .store import EventLog

_LOGGER = logging.getLogger(__name__)

SWITCH_DOMAIN = "switch"

# Window boundaries are minute-resolution, so a minute of latency is the most
# this adds. Cheap too: a tick compares three times and usually does nothing.
TICK_INTERVAL = timedelta(minutes=1)

FROZEN_AFTER = timedelta(hours=48)
"""How long a killswitch may stay on before it becomes an item of its own.

A killswitch is a deliberate, temporary intervention and the failure mode is
forgetting it, so it needs a backstop that does not wait for a plant to suffer
first. Two days is long enough that a weekend of deliberate manual control never
trips it.
"""


class LightController:
    """Keeps one fixture's switch matching its window, and records the outcome."""

    def __init__(
        self, hass: HomeAssistant, fixture: LightFixture, event_log: EventLog
    ) -> None:
        self._hass = hass
        self._fixture = fixture
        self._event_log = event_log
        self._unsubs: list[Callable[[], None]] = []
        self._listeners: list[Callable[[], None]] = []
        self._killed = False

        # On-time accounting. `_tracking_since` is when today's count started,
        # which is midnight on an ordinary day and start-up time on the day Home
        # Assistant restarted. Expectations are computed from that same instant,
        # so a restart narrows the comparison window rather than reporting the
        # part of the day nobody was watching as a shortfall. Nothing needs
        # persisting for that to hold, which is why nothing is.
        self._day: date | None = None
        self._tracking_since = time(0, 0)
        self._on_minutes = 0.0
        self._sampled_at: datetime | None = None
        self._was_on = False

    @property
    def fixture(self) -> LightFixture:
        return self._fixture

    @property
    def killed(self) -> bool:
        return self._killed

    @property
    def on_minutes(self) -> int:
        return int(self._on_minutes)

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
        """Begin controlling and counting.

        The killswitch is restored from the store, not from the switch entity.
        Entity platforms are set up after this, so reading the entity would
        mean driving the lamp once before finding out it was frozen — and the
        store is the killswitch's real home anyway, for the `last_changed`
        reason spelled out in `store.KILLSWITCH_SINCE`.
        """
        self._killed = self._event_log.killswitch_since(self._fixture.name) is not None

        now = dt_util.now()
        self._day = now.date()
        self._tracking_since = now.time()
        self._sampled_at = now
        self._was_on = self._switch_is_on()

        self._unsubs.append(
            async_track_time_interval(self._hass, self._handle_tick, TICK_INTERVAL)
        )
        # Presence changes are the other thing that can flip the answer, and
        # waiting up to a minute to cut a lamp in a room somebody just went to
        # sleep in is exactly the annoyance the window exists to avoid.
        presence = self._fixture.presence_entity
        if presence is not None:
            self._unsubs.append(
                async_track_state_change_event(
                    self._hass, [presence], self._handle_presence
                )
            )
        # The switch, so on-time is credited at the transition rather than
        # rounded to the next tick.
        self._unsubs.append(
            async_track_state_change_event(
                self._hass, [self._fixture.switch_entity], self._handle_switch
            )
        )

        # The first apply waits for Home Assistant to have finished starting.
        # The switch behind a grow light comes from MQTT discovery, and this
        # component is set up well before that — commanding a switch domain that
        # does not exist yet would do nothing and look like it had worked.
        self._unsubs.append(async_at_started(self._hass, self._handle_started))

    @callback
    def _handle_started(self, _hass: HomeAssistant) -> None:
        self.async_apply()

    @callback
    def async_stop(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()

    async def async_set_killed(self, killed: bool) -> None:
        """Freeze or resume automation for this fixture.

        A killswitch means "stop automating this", not "turn it off": whatever
        state the lamp is in when it is flipped is where it stays, so whoever
        flipped it is in control. One boolean rather than a kill plus a force,
        because two can contradict each other and one cannot.

        That makes a frozen fixture dangerous in *both* directions — stuck off
        starves the plants under it, stuck on gives them a 24-hour photoperiod
        and the heat with it. Which is why what gets reported is the on-time
        outcome and the 48-hour backstop, never the boolean itself.
        """
        if killed == self._killed:
            return

        self._killed = killed
        await self._event_log.async_set_killswitch_since(
            self._fixture.name, dt_util.utcnow() if killed else None
        )
        if not killed:
            self.async_apply()
        self._notify()

    @callback
    def _handle_tick(self, _now: datetime) -> None:
        self._sample()
        self.async_apply()
        # Unconditionally, not just when a command goes out: on-time and the
        # deviation derived from it move every minute whether or not the lamp
        # needed touching, and a lamp doing nothing wrong for twelve hours is
        # exactly when the numbers have drifted furthest from what was last
        # written.
        self._notify()

    @callback
    def _handle_presence(self, _event: Event[EventStateChangedData]) -> None:
        self.async_apply()

    @callback
    def _handle_switch(self, _event: Event[EventStateChangedData]) -> None:
        self._sample()
        self._notify()

    # ---- What the window says ------------------------------------------

    def _switch_is_on(self) -> bool:
        state = self._hass.states.get(self._fixture.switch_entity)
        return state is not None and state.state == STATE_ON

    def should_be_on(self, now: datetime | None = None) -> bool:
        """What the window says, ignoring the killswitch."""
        now = now or dt_util.now()
        asleep = False
        presence = self._fixture.presence_entity
        if presence is not None:
            state = self._hass.states.get(presence)
            asleep = is_asleep(state.state if state is not None else None)

        return self._fixture.window.is_on_at(
            now.time(), Weekday.from_python(now.weekday()), asleep=asleep
        )

    @callback
    def async_apply(self) -> None:
        if self._killed:
            return

        wanted = self.should_be_on()
        switch = self._fixture.switch_entity
        current = self._hass.states.get(switch)

        # Only act on a genuine mismatch. Calling turn_on every minute would
        # fill the logbook and wake the device for nothing. A switch that is
        # missing entirely is still driven, so a restart mid-window lands on the
        # right side of an edge that has already passed.
        if current is not None and (current.state == STATE_ON) == wanted:
            return

        _LOGGER.debug("plant_care: %s -> %s", switch, "on" if wanted else "off")
        self._hass.async_create_task(self._async_command(switch, wanted))
        self._notify()

    async def _async_command(self, switch: str, wanted: bool) -> None:
        try:
            await self._hass.services.async_call(
                SWITCH_DOMAIN,
                SERVICE_TURN_ON if wanted else SERVICE_TURN_OFF,
                {"entity_id": switch},
                blocking=False,
            )
        except ServiceNotFound:
            # The switch integration is not up yet. Nothing to do about it here
            # and nothing lost: the next tick re-evaluates from scratch and will
            # issue the same command once there is something to receive it.
            _LOGGER.debug(
                "plant_care: switch domain not ready for %s, retrying next tick",
                switch,
            )

    # ---- What actually happened ----------------------------------------

    @callback
    def _sample(self, now: datetime | None = None) -> None:
        """Credit the interval since the last sample, then remember the state.

        Credited from the state at the *start* of the interval, which is what
        makes a transition-triggered sample exact: the lamp was on for all of
        the time up to the moment it switched off, and none after.
        """
        now = now or dt_util.now()

        if self._day != now.date():
            self._day = now.date()
            self._tracking_since = time(0, 0)
            self._on_minutes = 0.0
            self._sampled_at = now
            self._was_on = self._switch_is_on()
            return

        if self._sampled_at is not None and self._was_on:
            elapsed = (now - self._sampled_at).total_seconds() / 60.0
            if elapsed > 0:
                self._on_minutes += elapsed

        self._sampled_at = now
        self._was_on = self._switch_is_on()

    def expectation(self, now: datetime | None = None) -> tuple[int, int]:
        """Minutes the window guarantees, and the most it could ever allow."""
        now = now or dt_util.now()
        day = Weekday.from_python(now.weekday())
        window = self._fixture.window
        return (
            window.guaranteed_minutes(self._tracking_since, now.time(), day),
            window.possible_minutes(self._tracking_since, now.time(), day),
        )

    def deviation(self, now: datetime | None = None) -> OnTimeDeviation | None:
        guaranteed, possible = self.expectation(now)
        return on_time_deviation(self.on_minutes, guaranteed, possible)

    def frozen_issue(self, now: datetime | None = None) -> HealthIssue | None:
        """A killswitch left on long enough to have been forgotten."""
        since = self._event_log.killswitch_since(self._fixture.name)
        if since is None:
            return None

        held = (now or dt_util.utcnow()) - since
        if held < FROZEN_AFTER:
            return None

        hours = held.total_seconds() / 3600.0
        return HealthIssue(
            kind=IssueKind.AUTOMATION_FROZEN,
            label=f"{self._fixture.name} automation frozen",
            detail=(
                f"The killswitch for '{self._fixture.name}' in {self._fixture.room} "
                f"has been on for {hours:.0f} hours, so nothing is scheduling it. "
                "Turn it off, or accept that the plants under it are on manual."
            ),
            value=round(hours, 1),
        )
