"""Turning a stream of probe readings into a decision.

Pure: driven by explicit `observe(when, value)` calls, so every behaviour here —
how the median rejects a bad read, what counts as a watering, how long dry has
to hold — is unit testable with injected times rather than by waiting.

The shape mirrors what the hand-written Home Assistant package did with
`statistics` sensors and a blueprint, because that shape was right; what it
could not do was be tested.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .plant import Calibrated, Calibration, ProbeFacts
from .policy import Policy


@dataclass(frozen=True)
class Reading:
    when: datetime
    value: float


class RollingWindow:
    """Readings inside a time window.

    `max_samples` is a cap that should never bind — the window is what governs
    the result. It exists so a probe that suddenly reports every second cannot
    grow this without limit.
    """

    def __init__(self, window: timedelta, max_samples: int) -> None:
        self._window = window
        self._max_samples = max_samples
        self._readings: list[Reading] = []

    def add(self, reading: Reading) -> None:
        self._readings.append(reading)
        self._prune(reading.when)

    def _prune(self, now: datetime) -> None:
        cutoff = now - self._window
        self._readings = [r for r in self._readings if r.when > cutoff]
        if len(self._readings) > self._max_samples:
            self._readings = self._readings[-self._max_samples :]

    @property
    def values(self) -> list[float]:
        return [r.value for r in self._readings]

    def median(self) -> float | None:
        """A median, not a mean.

        A median discards a watering spike or a single bad read outright,
        instead of smearing it across the rest of the window the way a mean
        would.
        """
        values = self.values
        if not values:
            return None
        return round(statistics.median(values), 2)

    def minimum(self) -> float | None:
        values = self.values
        if not values:
            return None
        return min(values)

    def __len__(self) -> int:
        return len(self._readings)


@dataclass(frozen=True)
class MoistureSignals:
    """What the monitor currently believes, as the sensors will report it.

    Every field is `None` before the first reading arrives, which is honest:
    after a restart the window is empty and there is genuinely nothing to say
    yet. A zero here would read as "bone dry" and a watering that never happened
    would look overdue.
    """

    smoothed: float | None = None
    minimum: float | None = None
    rise: float | None = None
    available_water: float | None = None


@dataclass
class MoistureMonitor:
    """The rolling signals, the watering detector, and the needs-water latch.

    `needs_water` is a latch rather than a comparison: it is set when the
    smoothed reading has held below the threshold long enough to be believed,
    and cleared only by a detected watering. That asymmetry is deliberate —
    moisture creeping back up on its own (a cool night, a probe settling) is not
    someone having watered the plant.
    """

    policy: Policy
    probe: ProbeFacts
    calibration: Calibration

    _window: RollingWindow = field(init=False)
    _last: Reading | None = field(init=False, default=None)
    _below_since: datetime | None = field(init=False, default=None)
    _needs_water: bool = field(init=False, default=False)
    _last_watered: datetime | None = field(init=False, default=None)

    def __post_init__(self) -> None:
        self._window = RollingWindow(
            window=timedelta(hours=self.policy.median_window_hours(self.probe)),
            max_samples=self.policy.median_sampling_size(self.probe),
        )

    # ---- Restoring across a restart ------------------------------------

    def restore(self, needs_water: bool, last_watered: datetime | None) -> None:
        """Re-adopt the latch after a restart.

        The rolling window is deliberately **not** restored: it would be stale,
        and refilling it takes one window. `_below_since` is not restored
        either, so a plant that went dry while Home Assistant was down takes a
        full confirm period to flag after it returns.

        That costs a delay and never a missed flag, because a plant already
        flagged stays flagged — which is the direction worth being wrong in.
        """
        self._needs_water = needs_water
        self._last_watered = last_watered

    # ---- Observing -----------------------------------------------------

    def observe(self, when: datetime, value: float) -> bool:
        """Take a reading. Returns whether it looked like a watering.

        Watering is judged on the **raw** value against the trailing minimum,
        not on the smoothed one, so a watering clears the flag immediately
        rather than a window later.
        """
        self._window.add(Reading(when, value))
        self._last = Reading(when, value)

        watered = self._detect_watering(value)
        if watered:
            self._last_watered = when
            self._needs_water = False
            self._below_since = None
        else:
            self._evaluate_dry(when)

        return watered

    def _detect_watering(self, value: float) -> bool:
        if not isinstance(self.calibration, Calibrated):
            # No calibrated span, so no scale on which to say a rise is large.
            # Monitoring continues; detection does not.
            return False

        trough = self._window.minimum()
        if trough is None:
            return False

        rise = value - trough
        return rise >= self.policy.watering_rise(self.calibration)

    def _evaluate_dry(self, when: datetime) -> None:
        if not isinstance(self.calibration, Calibrated):
            return

        smoothed = self._window.median()
        if smoothed is None:
            return

        if smoothed > self.policy.refill_threshold(self.calibration):
            self._below_since = None
            return

        if self._below_since is None:
            self._below_since = when
            return

        confirmed = timedelta(hours=self.policy.dry_confirm_hours)
        if when - self._below_since >= confirmed:
            self._needs_water = True

    # ---- Reading out ---------------------------------------------------

    @property
    def signals(self) -> MoistureSignals:
        smoothed = self._window.median()
        minimum = self._window.minimum()

        rise: float | None = None
        if self._last is not None and minimum is not None:
            rise = round(max(self._last.value - minimum, 0.0), 2)

        available: float | None = None
        if isinstance(self.calibration, Calibrated) and smoothed is not None:
            available = round(self.calibration.available_water(smoothed), 1)

        return MoistureSignals(
            smoothed=smoothed,
            minimum=minimum,
            rise=rise,
            available_water=available,
        )

    @property
    def needs_water(self) -> bool:
        return self._needs_water

    @property
    def last_watered(self) -> datetime | None:
        return self._last_watered

    @property
    def has_data(self) -> bool:
        return len(self._window) > 0
