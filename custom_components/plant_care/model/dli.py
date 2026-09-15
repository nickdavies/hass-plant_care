"""Daily light integral as an objective with an error budget.

A plain count of days outside a band is a threshold alert wearing SLO clothes:
one bad day consumes a large fraction of a small budget, so it either pages on a
cloudy afternoon or tolerates a week of near-misses. Neither is useful.

So the budget is a **continuous quantity** — cumulative mol/m² of deviation from
the preferred band over a rolling window. A day 10% under burns a little; a day
at zero burns a lot; a winter month sitting just above the low bound burns
slowly and steadily, which is exactly the case a day-count cannot see.

Two alert speeds fall out of it, the standard multi-window shape: a fast burn
says go and look now, a slow burn says adjust the schedule.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import Enum


@dataclass(frozen=True)
class Band:
    low: float
    high: float

    def __post_init__(self) -> None:
        if self.low >= self.high:
            raise ValueError(f"band low {self.low} must be below high {self.high}")

    def deviation(self, value: float) -> float:
        """How far outside the band, in the band's own units. Zero when inside.

        Symmetric by construction: too much light is a failure too — heat,
        photoinhibition, a disrupted photoperiod — not merely an absence of
        failure.
        """
        if value < self.low:
            return self.low - value
        if value > self.high:
            return value - self.high
        return 0.0

    def contains(self, value: float) -> bool:
        return self.low <= value <= self.high


class BurnKind(Enum):
    FAST = "fast"
    SLOW = "slow"


class Direction(Enum):
    """Which way the budget is being spent.

    Carried because "mostly under" and "mostly over" want opposite schedule
    changes, and an alert that does not say which is half an alert.
    """

    UNDER = "under"
    OVER = "over"
    BOTH = "both"


@dataclass(frozen=True)
class DliObjective:
    """What "enough light" means for one plant."""

    category: str
    preferred: Band
    survival: Band | None = None
    window_days: int = 28
    budget: float = 20.0
    preferred_overridden: bool = False

    def __post_init__(self) -> None:
        if self.window_days <= 0:
            raise ValueError(f"windowDays must be positive, got {self.window_days}")
        if self.budget <= 0:
            raise ValueError(f"budget must be positive, got {self.budget}")
        if self.survival is not None and (
            self.survival.low > self.preferred.low
            or self.survival.high < self.preferred.high
        ):
            raise ValueError("preferred band reaches outside survival band")

    @property
    def unstable_spread(self) -> float:
        """How much day-to-day swing counts as instability.

        Derived from the band's own width rather than configured, so it scales
        with how fussy the plant is and there is no third number to get wrong. A
        plant averaging in-band while swinging three band-widths either side is
        not getting what the band describes, and the mean alone cannot see it.
        """
        return (self.preferred.high - self.preferred.low) * 3.0


@dataclass(frozen=True)
class DailyDli:
    day: date
    value: float


@dataclass(frozen=True)
class BurnAlert:
    kind: BurnKind
    direction: Direction
    rate: float
    """Multiples of the budget's own pace. 1.0 means exactly on track to spend
    the whole budget over the whole window."""
    deviation: float
    days: int


# Standard multi-window burn rates. Fast fires on a day bad enough to spend a
# tenth of a 28-day budget in one day; slow on a week running at twice pace.
#
# Worth knowing before tuning either: **the fast burn is asymmetric, and
# necessarily so.** A day's shortfall can never exceed `preferred.low` — a plant
# in total darkness is still only `low` mol under — while a day's excess has no
# ceiling at all. So for a plant whose band starts low relative to its daily
# budget allowance, no single dark day can reach the fast threshold, and the
# fast signal is in practice a high-side one.
#
# That is why the fast page is "burn rate **or** outside survival", and why the
# intra-day certainty checks exist. Between them the low side is covered by two
# signals that do not depend on the budget's scale at all. Lowering this number
# to close the gap would only make the high side hair-triggered.
FAST_WINDOW_DAYS = 1
FAST_BURN_RATE = 10.0
SLOW_WINDOW_DAYS = 7
SLOW_BURN_RATE = 2.0


def burn_rate(deviation: float, days: int, objective: DliObjective) -> float:
    """Deviation accrued over `days`, as a multiple of the budget's own pace.

    The budget is spread evenly across the window, so `budget * days /
    window_days` is what "on pace" would have spent by now. Dividing by it makes
    the number comparable across window lengths, which is the whole point of
    expressing it as a rate rather than an amount.
    """
    on_pace = objective.budget * days / objective.window_days
    if on_pace <= 0:
        return 0.0
    return deviation / on_pace


def _direction(history: list[DailyDli], band: Band) -> Direction:
    under = any(day.value < band.low for day in history)
    over = any(day.value > band.high for day in history)
    if under and over:
        return Direction.BOTH
    return Direction.OVER if over else Direction.UNDER


def evaluate(
    history: list[DailyDli], objective: DliObjective, today: date
) -> list[BurnAlert]:
    """Burn alerts from completed days.

    Takes only completed days: a partially accumulated today always looks like a
    shortfall, and alerting on it would fire every morning.
    """
    completed = [day for day in history if day.day < today]
    alerts: list[BurnAlert] = []

    for kind, span, threshold in (
        (BurnKind.FAST, FAST_WINDOW_DAYS, FAST_BURN_RATE),
        (BurnKind.SLOW, SLOW_WINDOW_DAYS, SLOW_BURN_RATE),
    ):
        cutoff = today - timedelta(days=span)
        window = [day for day in completed if day.day >= cutoff]
        if not window:
            continue

        deviation = sum(objective.preferred.deviation(day.value) for day in window)
        if deviation <= 0:
            continue

        rate = burn_rate(deviation, span, objective)
        if rate >= threshold:
            alerts.append(
                BurnAlert(
                    kind=kind,
                    direction=_direction(window, objective.preferred),
                    rate=round(rate, 2),
                    deviation=round(deviation, 2),
                    days=len(window),
                )
            )

    return alerts


def outside_survival(value: float, objective: DliObjective) -> bool:
    """Past the point where this stops being a slow problem.

    Separate from the burn rates because it needs no history: one day this far
    out is worth saying immediately.
    """
    if objective.survival is None:
        return False
    return not objective.survival.contains(value)


def unstable(
    history: list[DailyDli], objective: DliObjective, today: date
) -> float | None:
    """The window's spread, when it is wider than the plant should see.

    Needs a reasonable number of days to mean anything: two days is not a
    spread, it is a difference.
    """
    cutoff = today - timedelta(days=objective.window_days)
    window = [day.value for day in history if cutoff <= day.day < today]
    if len(window) < MIN_DAYS_FOR_SPREAD:
        return None

    spread = max(window) - min(window)
    return round(spread, 2) if spread > objective.unstable_spread else None


# ---- Accumulating today -------------------------------------------------

MAX_SAMPLE_GAP = timedelta(minutes=15)
"""Longest interval a single reading is allowed to stand for.

Integration holds the last reading until the next one arrives, so a sensor that
drops out for six hours would otherwise have its final daylight reading credited
across the whole gap. Capping it means an outage under-counts rather than
inventing light that was never measured — the safe direction, since a shortfall
is visible and a fabricated surplus is not.
"""

MIN_DAYS_FOR_CERTAINTY = 3
"""Completed days needed before today's shortfall can be called unrecoverable.

The deficit test needs some idea of what a good day looks like here, and one
day's history is an anecdote.
"""

MIN_DAYS_FOR_SPREAD = 5


class DliAccumulator:
    """Integrates PPFD into a daily light integral, one day at a time.

    PPFD arrives in µmol/m²/s and DLI is mol/m²/day, so this is a Riemann sum
    over seconds divided by a million. Zero-order hold — each reading stands
    until the next — because a lux sensor reports on change and interpolating
    between two readings would invent a ramp nobody measured.
    """

    def __init__(self, day: date, total: float = 0.0) -> None:
        self.day = day
        self.total = total
        self._last_at: datetime | None = None
        self._last_ppfd: float | None = None

    def observe(self, now: datetime, ppfd: float | None) -> DailyDli | None:
        """Fold one reading in. Returns the finished day when the date rolls.

        `ppfd` of `None` means the sensor has nothing to say — unavailable, or
        every member of the group out. The interval up to that point is still
        credited, but nothing is held forward past it, so an outage stops
        accumulating instead of extrapolating its last reading.

        Rollover credits the straddling interval to the day that is ending. It
        can be off by at most `MAX_SAMPLE_GAP`, and it is an interval containing
        local midnight, when the quantity being measured is zero.
        """
        self._credit(now)

        finished: DailyDli | None = None
        if now.date() != self.day:
            finished = DailyDli(day=self.day, value=round(self.total, 3))
            self.day = now.date()
            self.total = 0.0

        self._last_at = now
        self._last_ppfd = ppfd
        return finished

    def _credit(self, now: datetime) -> None:
        if self._last_at is None or self._last_ppfd is None:
            return
        gap = min(now - self._last_at, MAX_SAMPLE_GAP)
        if gap <= timedelta(0):
            # Time going backwards is a clock correction, not negative light.
            return
        self.total += self._last_ppfd * gap.total_seconds() / 1_000_000.0


def day_fraction_remaining(now: datetime) -> float:
    elapsed = now.hour * 3600 + now.minute * 60 + now.second
    return max(0.0, 1.0 - elapsed / 86400.0)


def today_certainty(
    accumulated: float,
    now: datetime,
    history: list[DailyDli],
    objective: DliObjective,
) -> Direction | None:
    """What today has *already* settled — never a projection.

    Light is not flat across a day, so extrapolating a morning's rate would call
    every sunrise a disaster. Both tests here are statements about what can no
    longer change:

    - accumulation has already passed the upper bound while the day is still
      running, so the excess is a fact whatever happens next. This is the
      stuck-on lamp, caught exactly.
    - the lower bound is out of reach even if the rest of the day matches the
      best day on record, so the shortfall is a fact too. Generous on purpose:
      the best day is an over-estimate of what the *remainder* of a day can
      deliver, which means this fires late rather than wrongly.
    """
    if accumulated > objective.preferred.high:
        return Direction.OVER

    completed = [day.value for day in history if day.day < now.date()]
    if len(completed) < MIN_DAYS_FOR_CERTAINTY:
        return None

    best_remaining = max(completed) * day_fraction_remaining(now)
    if accumulated + best_remaining < objective.preferred.low:
        return Direction.UNDER

    return None
