"""Driving one grow light fixture, and watching what actually happened.

Two jobs, in one object because they share a subscription to the same switch:

1. Re-evaluate the window every minute and whenever its sleepers change, and drive
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

from .model import (
    MAX_RESTART_GAP_MINUTES,
    LightFixture,
    OnTimeDeviation,
    Weekday,
    on_time_deviation,
)
from .model.health import HealthIssue, IssueKind
from .store import EventLog, OnTimeRecord

_LOGGER = logging.getLogger(__name__)

SWITCH_DOMAIN = "switch"

# Window boundaries are minute-resolution, so a minute of latency is the most
# this adds. Cheap too: a tick compares three times and usually does nothing.
TICK_INTERVAL = timedelta(minutes=1)

FLUSH_INTERVAL = timedelta(minutes=5)
"""How often today's on-time is written down.

Not on every sample: the smart plug behind a grow light reports power every few
seconds, and each report is a state change, so writing on each one would be a
storage write every few seconds per fixture. Not only at midnight either —
that is the failure being fixed. Transitions are written as they happen
regardless, since they are rare and they are what makes the stored on/off flag
worth trusting.
"""

MAX_RESTART_GAP = timedelta(minutes=MAX_RESTART_GAP_MINUTES)

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

        # On-time accounting, kept as two spans over one counter.
        #
        # `_on_minutes` is the day, restored across a restart so the dashboard
        # reports the day rather than the time since the last deploy.
        #
        # The comparison against the window has to run over a stretch something
        # was actually watching, or a lamp would be judged against hours nobody
        # recorded. That stretch runs from `_tracking_since`, and
        # `_counted_from` holds the minutes banked before it opened so they can
        # be taken back off. Ordinarily the two spans are the same and
        # `_counted_from` is zero; they part only after an outage too long to
        # account for.
        self._day: date = dt_util.now().date()
        self._tracking_since = time(0, 0)
        self._on_minutes = 0.0
        self._counted_from = 0.0
        self._sampled_at: datetime | None = None
        self._was_on = False
        self._flushed_at = dt_util.now()

    @property
    def fixture(self) -> LightFixture:
        return self._fixture

    @property
    def killed(self) -> bool:
        return self._killed

    @property
    def on_minutes(self) -> int:
        """Minutes the lamp has been on today. What the sensor reports."""
        return int(self._on_minutes)

    @property
    def tracked_minutes(self) -> int:
        """The part of that falling inside the span expectations cover.

        The same number as `on_minutes` unless an outage was too long to
        credit — in which case judging a whole day's on-time against a window
        that only opened at teatime would report every lamp as stuck on.
        """
        return int(self._on_minutes - self._counted_from)

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
        self._restore(dt_util.now())

        self._unsubs.append(
            async_track_time_interval(self._hass, self._handle_tick, TICK_INTERVAL)
        )
        # Whatever the window's matcher reads is the other thing that can flip
        # the answer, and waiting up to a minute to cut a lamp somebody just
        # went to sleep beside is exactly the annoyance the window exists to
        # avoid.
        watched = list(self._fixture.watched_entities)
        if watched:
            self._unsubs.append(
                async_track_state_change_event(
                    self._hass, watched, self._handle_sleepers
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
    def _handle_sleepers(self, _event: Event[EventStateChangedData]) -> None:
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
        states: dict[str, str | None] = {}
        for entity_id in self._fixture.watched_entities:
            state = self._hass.states.get(entity_id)
            states[entity_id] = state.state if state is not None else None
        asleep = self._fixture.is_quiet(states)

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
    def _restore(self, now: datetime) -> None:
        """Pick today's count up where the last run left it, where that is honest.

        A restart is a break in the record, not in the lamp: nothing but this
        controller commands the switch, so across a short one the lamp held
        whatever state it was last seen in, and the break can be filled in from
        the stored flag. From the *stored* one rather than the state the switch
        comes back in, because the switch arrives from MQTT discovery some time
        after this runs — `_handle_started` exists for that reason — so there is
        nothing there to read yet.

        Beyond `MAX_RESTART_GAP` that reasoning runs out. Nothing here knows
        what the lamp did during an outage, so the comparison restarts from now
        and only ever judges a stretch that was watched. The day's total is
        still carried: it is an undercount by however long the outage was, but
        the alternative is reporting a lamp that ran all morning as one that
        never came on, which is the failure this whole record exists to stop.
        """
        self._day = now.date()
        self._tracking_since = now.time()
        self._on_minutes = 0.0
        self._counted_from = 0.0
        self._sampled_at = now
        self._was_on = self._switch_is_on()
        self._flushed_at = now

        stored = self._event_log.on_time(self._fixture.name)
        if stored is None or stored.day != now.date():
            return

        gap = now - stored.seen
        self._on_minutes = stored.minutes
        if timedelta(0) <= gap <= MAX_RESTART_GAP:
            if stored.on:
                self._on_minutes += gap.total_seconds() / 60.0
            self._tracking_since = stored.since
            self._counted_from = stored.counted_from
        else:
            # Includes a gap running backwards, which means the clock moved
            # under us and the arithmetic is not to be trusted either way.
            self._counted_from = self._on_minutes

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
            self._counted_from = 0.0
            self._sampled_at = now
            self._was_on = self._switch_is_on()
            self._persist(now)
            return

        if self._sampled_at is not None and self._was_on:
            elapsed = (now - self._sampled_at).total_seconds() / 60.0
            if elapsed > 0:
                self._on_minutes += elapsed

        self._sampled_at = now
        before, self._was_on = self._was_on, self._switch_is_on()

        if before != self._was_on or now - self._flushed_at >= FLUSH_INTERVAL:
            self._persist(now)

    @callback
    def _persist(self, now: datetime) -> None:
        self._flushed_at = now
        self._hass.async_create_task(
            self._event_log.async_record_on_time(
                self._fixture.name,
                OnTimeRecord(
                    day=self._day,
                    minutes=self._on_minutes,
                    since=self._tracking_since,
                    counted_from=self._counted_from,
                    seen=now,
                    on=self._was_on,
                ),
            )
        )

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
        return on_time_deviation(self.tracked_minutes, guaranteed, possible)

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
                f"The killswitch for '{self._fixture.name}' has been on for "
                f"{hours:.0f} hours, so nothing is scheduling it. "
                "Turn it off, or accept that the plants under it are on manual."
            ),
            value=round(hours, 1),
        )
