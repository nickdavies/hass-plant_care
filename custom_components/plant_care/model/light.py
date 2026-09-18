"""Grow light fixtures and lux sensor groups.

A fixture is hardware, not a property of a plant: one lamp serves several plants
and one plant can sit under several lamps. So fixtures are standalone objects
that plants reference by name.

The window is the interesting part. It is a sum type, because a fixture in a
room somebody sleeps in obeys different rules from one in a spare room, and the
two sets of fields must not be mixable.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import time
from enum import Enum

from .dli import Direction


class Lit(Enum):
    """Where a plant's light comes from.

    Derived by the generator from which fields the plant has — a lux fixture, a
    grow light, both — never set by hand, so it cannot disagree with them.
    """

    SUN = "sun"
    """Measured, unlit: one lux→PPFD factor applies all day."""

    GROW = "grow"
    """Lit but unmeasured: scheduled hours are the only evidence available."""

    MIXED = "mixed"
    """Both, so the factor has to switch with the lamp."""


class Weekday(Enum):
    MON = "mon"
    TUE = "tue"
    WED = "wed"
    THU = "thu"
    FRI = "fri"
    SAT = "sat"
    SUN = "sun"

    @classmethod
    def from_python(cls, weekday: int) -> Weekday:
        """From `datetime.weekday()`, where Monday is 0."""
        return list(cls)[weekday]


def _minutes(value: time) -> int:
    return value.hour * 60 + value.minute


def _overlap(start: time, end: time, since: time, until: time) -> int:
    """Minutes of `[start, end)` falling inside `[since, until)`."""
    low = max(_minutes(start), _minutes(since))
    high = min(_minutes(end), _minutes(until))
    return max(0, high - low)


@dataclass(frozen=True)
class FixedWindow:
    """A plain daily window. Nobody sleeps near this fixture."""

    start: time
    end: time
    days: frozenset[Weekday] | None = None
    """`None` means every day."""

    def runs_on(self, day: Weekday) -> bool:
        return self.days is None or day in self.days

    def is_on_at(self, moment: time, day: Weekday, *, asleep: bool) -> bool:
        """`asleep` is ignored: nobody's sleep governs this fixture.

        Taking the argument anyway keeps the two window types substitutable, so
        the caller never has to ask which kind it is holding.
        """
        del asleep
        return self.runs_on(day) and self.start <= moment < self.end

    def guaranteed_minutes(self, since: time, until: time, day: Weekday) -> int:
        if not self.runs_on(day):
            return 0
        return _overlap(self.start, self.end, since, until)

    def possible_minutes(self, since: time, until: time, day: Weekday) -> int:
        return self.guaranteed_minutes(since, until, day)


@dataclass(frozen=True)
class AwakeAwareWindow:
    """A fixture in a room somebody sleeps in.

    Four bounds, each answering a different failure:

    - `on_if_awake_after`: come on early when they are already up, so the plant
      banks the extra light.
    - `on_after`: come on anyway by here, so a lie-in never starves it.
    - `on_even_if_asleep_until`: stay on until here whatever they do, so an
      early night does not cut the photoperiod short.
    - `on_until`: off by here regardless.

    Between `on_even_if_asleep_until` and `on_until`, their sleep closes it.
    Before `on_after`, their being awake opens it early.
    """

    on_if_awake_after: time
    on_after: time
    on_even_if_asleep_until: time
    on_until: time
    presence_entity: str
    days: frozenset[Weekday] | None = None

    def runs_on(self, day: Weekday) -> bool:
        return self.days is None or day in self.days

    def is_on_at(self, moment: time, day: Weekday, *, asleep: bool) -> bool:
        if not self.runs_on(day):
            return False
        if moment >= self.on_until or moment < self.on_if_awake_after:
            return False

        # The early window: only while they are up.
        if moment < self.on_after:
            return not asleep

        # The guaranteed middle: on regardless.
        if moment < self.on_even_if_asleep_until:
            return True

        # The tail: theirs to close by going to bed.
        return not asleep

    def guaranteed_minutes(self, since: time, until: time, day: Weekday) -> int:
        """The stretch no amount of sleeping can close."""
        if not self.runs_on(day):
            return 0
        return _overlap(self.on_after, self.on_even_if_asleep_until, since, until)

    def possible_minutes(self, since: time, until: time, day: Weekday) -> int:
        """The widest the window can ever open, if they are up for all of it."""
        if not self.runs_on(day):
            return 0
        return _overlap(self.on_if_awake_after, self.on_until, since, until)


LightWindow = FixedWindow | AwakeAwareWindow

# Both window types answer `guaranteed_minutes` and `possible_minutes`, which is
# what lets the on-time check work without knowing which kind it holds — and,
# more importantly, without a record of who was awake when.
#
# Comparing against a *pair* of bounds rather than one expected number is the
# whole trick. An awake-aware window's real on-time depends on presence history
# nobody stores, so any single expectation would be a guess. Two bounds instead
# make both alerts statements of fact: under the guaranteed minutes means the
# lamp missed time nothing could have excused, over the possible minutes means it
# ran outside its own window. Everything between is simply not evidence.


@dataclass(frozen=True)
class LightFixture:
    """One grow light."""

    name: str
    switch_entity: str
    room: str
    window: LightWindow
    lux_to_ppfd: float | None = None
    """This lamp's spectrum. Absent unless a lux fixture covers a plant under
    it — the generator requires it at exactly that point."""

    @property
    def is_sleep_sensitive(self) -> bool:
        return isinstance(self.window, AwakeAwareWindow)

    @property
    def presence_entity(self) -> str | None:
        if isinstance(self.window, AwakeAwareWindow):
            return self.window.presence_entity
        return None


@dataclass(frozen=True)
class LuxFixture:
    """A group of lux sensors, averaged.

    Averaged because a single probe shaded by one leaf reports a value that is
    true for that spot and wrong for the plant.
    """

    name: str
    entities: tuple[str, ...]
    sun_lux_to_ppfd: float

    @staticmethod
    def average(readings: Sequence[float | None]) -> float | None:
        """Mean of the members that are reporting.

        A member with nothing to say is skipped rather than counted as zero, and
        the fixture only goes quiet when every member is out. One dead probe
        must not read as darkness — that would look exactly like a failed lamp
        and would burn the light budget for a plant that is perfectly fine.
        """
        live = [value for value in readings if value is not None]
        if not live:
            return None
        return sum(live) / len(live)


def ppfd_factor(sun_factor: float, lit_by: Sequence[float]) -> float:
    """Which lux→PPFD scalar applies to a reading taken right now.

    The conversion belongs to the emitter, not the sensor: lux is weighted by
    human photopic response and PPFD counts photons between 400 and 700nm, so
    the ratio depends entirely on the spectrum producing the light. A lamp's
    factor therefore lives on the fixture, and this switches to it while that
    lamp is on.

    Residual error, stated rather than hidden: while a lamp is on the real light
    is sun *plus* lamp with different spectra, and one blended factor is an
    approximation of a mixture. Two lamps of different types over one sensor
    cannot be told apart at all — averaging their factors is the least wrong
    thing available. This is why the objective is a band with an error budget
    and not a target: the budget is sized to absorb this, and tuning the budget
    is the right response to noise here, not tuning the factors.
    """
    if not lit_by:
        return sun_factor
    return sum(lit_by) / len(lit_by)


ON_TIME_TOLERANCE_MINUTES = 45
"""Slack before an on-time gap counts as a fault.

A guess, and deliberately loose. The controller re-evaluates every minute, so
its own error is about a minute; the rest absorbs a zigbee2mqtt restart, a Flux
redeploy, or a slow outlet. A bulb that is genuinely dead accrues hours, so the
alert that matters is not the one this number is close to.
"""


@dataclass(frozen=True)
class OnTimeDeviation:
    """A fixture that did not run for as long as its window says it should
    have — or ran when the window says it could not have."""

    direction: Direction
    minutes: int
    """How far outside the bounds, which is what makes it worth reporting."""
    actual: int
    guaranteed: int
    possible: int


def on_time_deviation(
    actual: int,
    guaranteed: int,
    possible: int,
    tolerance: int = ON_TIME_TOLERANCE_MINUTES,
) -> OnTimeDeviation | None:
    """Compare observed on-time against what the window allows.

    Catches consequences, not causes: a dead relay, a bulb someone unplugged, an
    outlet that fell off zigbee2mqtt, a manual toggle nobody undid and a frozen
    automation all produce the same reading here. Checking the killswitch
    instead would see none of them.
    """
    if actual < guaranteed - tolerance:
        return OnTimeDeviation(
            direction=Direction.UNDER,
            minutes=guaranteed - actual,
            actual=actual,
            guaranteed=guaranteed,
            possible=possible,
        )
    if actual > possible + tolerance:
        return OnTimeDeviation(
            direction=Direction.OVER,
            minutes=actual - possible,
            actual=actual,
            guaranteed=guaranteed,
            possible=possible,
        )
    return None


UP_STATES = frozenset({"awake", "winddown"})
DOWN_STATES = frozenset({"asleep"})


def is_asleep(presence_state: str | None) -> bool:
    """Whether the presence signal says nobody who matters is up.

    `sensor.group_presence_*` serialises a **comma-joined set** — `"awake,asleep"`
    when one person is up and another is not — so this tests membership rather
    than equality. Getting that wrong would compare a set against a single word
    and silently never match.

    Asleep has to be stated, not inferred from the absence of awake. Anything
    that is neither — `unavailable`, `unknown`, a state this does not recognise,
    a missing entity — counts as awake, because the failure directions are not
    symmetric: a light left on wastes some power, a light wrongly held off
    starves a plant for as long as the sensor stays broken.
    """
    if presence_state is None:
        return False
    parts = {part.strip() for part in presence_state.split(",")}
    if UP_STATES & parts:
        return False
    return bool(DOWN_STATES & parts)
