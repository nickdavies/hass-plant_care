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
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .budget import Interval, TimeBudget
from .health import HealthIssue, IssueKind
from .plant import Calibrated, Calibration, ProbeFacts
from .policy import Policy

SILENCE = "silence"
WET = "wet"
"""Names of the two persisted budgets, as the store keys them."""


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

    # Health-check state. Tracked here rather than recomputed because each of
    # these is about *duration*, and a rolling window one hour long cannot say
    # how long something has been true for twelve.
    _last_change: Reading | None = field(init=False, default=None)
    _settle_due: datetime | None = field(init=False, default=None)
    _shortfall: float | None = field(init=False, default=None)
    _started_at: datetime | None = field(init=False, default=None)

    # The two burn-rate budgets. Silence is learned about after the fact, when
    # the probe speaks again; wetness is known at every reading. `_wet` only
    # exists once calibrated, because "above field capacity" needs one.
    _silence: TimeBudget = field(init=False)
    _wet: TimeBudget | None = field(init=False, default=None)
    _dirty: bool = field(init=False, default=False)

    def __post_init__(self) -> None:
        self._window = RollingWindow(
            window=timedelta(hours=self.policy.median_window_hours(self.probe)),
            max_samples=self.policy.median_sampling_size(self.probe),
        )
        self._silence = TimeBudget(
            window=self.policy.availability_window(),
            allowance=self.policy.availability_allowance(),
        )
        if isinstance(self.calibration, Calibrated):
            self._wet = TimeBudget(
                window=self.policy.waterlogged_window(),
                allowance=self.policy.waterlogged_allowance(self.calibration),
            )

    # ---- Restoring across a restart ------------------------------------

    def restore(
        self,
        needs_water: bool,
        last_watered: datetime | None,
        now: datetime | None = None,
        silence: Iterable[Interval] = (),
        wet: Iterable[Interval] = (),
    ) -> None:
        """Re-adopt the latch and the budgets after a restart.

        The rolling window is deliberately **not** restored: it would be stale,
        and refilling it takes one window. `_below_since` is not restored
        either, so a plant that went dry while Home Assistant was down takes a
        full confirm period to flag after it returns.

        That costs a delay and never a missed flag, because a plant already
        flagged stays flagged — which is the direction worth being wrong in.

        A watering still inside its settle window *is* re-armed, so a restart
        during that hour does not quietly skip the shortfall check — which would
        be the one time it matters most, since a redeploy right after watering
        is exactly when you are stood over the pot.

        The budgets' closed stretches are restored, because the slow burn
        exists to see across days and Home Assistant restarts more often than
        that. A stretch still open at shutdown is lost; time Home Assistant
        itself was down is never counted against the probe.
        """
        self._needs_water = needs_water
        self._last_watered = last_watered
        self._silence.restore(silence)
        if self._wet is not None:
            self._wet.restore(wet)
        # Silence is measured from here until the first reading arrives. On a
        # cold start MQTT discovery has not created the probe entities yet, so
        # without this every plant would report a silent probe for the first few
        # seconds of every Home Assistant restart.
        self._started_at = now

        if last_watered is not None and now is not None:
            settle = timedelta(minutes=self.policy.shortfall_settle_minutes)
            if now - last_watered < settle:
                self._settle_due = last_watered + settle

    # ---- Observing -----------------------------------------------------

    def observe(self, when: datetime, value: float) -> bool:
        """Take a reading. Returns whether it looked like a watering.

        Watering is judged on the **raw** value against the trailing minimum,
        not on the smoothed one, so a watering clears the flag immediately
        rather than a window later.
        """
        previous = self._last
        self._window.add(Reading(when, value))
        self._last = Reading(when, value)

        # A gap longer than one heartbeat is silence, known only now that the
        # probe has spoken again. The first heartbeat interval is not missed
        # until it has passed, so only the excess counts.
        if previous is not None:
            expected = previous.when + timedelta(minutes=self.probe.heartbeat_minutes)
            if when > expected:
                self._silence.add(Interval(expected, when))
                self._dirty = True

        # "The value has not moved" needs the last time it actually moved, which
        # is not the last time it was reported — a wedged probe heartbeats
        # perfectly and repeats one number.
        if previous is None or previous.value != value:
            self._last_change = Reading(when, value)

        watered = self._detect_watering(value)
        if watered:
            self._last_watered = when
            self._needs_water = False
            self._below_since = None
            self._shortfall = None
            self._settle_due = when + timedelta(
                minutes=self.policy.shortfall_settle_minutes
            )
        else:
            self._evaluate_dry(when)

        self._evaluate_settle(when, value)
        self._track_waterlogging(when)

        return watered

    def _evaluate_settle(self, when: datetime, value: float) -> None:
        """An hour after a watering, did the water actually reach the roots?

        Dried peat mixes go hydrophobic and shed water down the gap between
        rootball and pot wall, straight out the drainage holes. The pot feels
        watered, the runoff looks convincing, and the core stays dry.

        Piggybacks on the next reading past the settle window rather than
        running a timer: readings arrive every few minutes anyway, and a timer
        would be one more thing to lose across a restart.

        Note what this cannot see. *Complete* channelling produces no rise, so no
        watering is detected and this never runs. That case is caught by the
        needs-water latch never clearing — which is why the latch is cleared
        only by a detected watering and not by moisture drifting up.
        """
        if self._settle_due is None or when < self._settle_due:
            return
        if not isinstance(self.calibration, Calibrated):
            self._settle_due = None
            return

        target = self.policy.fc_shortfall_target(self.calibration)
        self._shortfall = value if value < target else None
        self._settle_due = None

    def _track_waterlogging(self, when: datetime) -> None:
        if self._wet is None or not isinstance(self.calibration, Calibrated):
            return

        smoothed = self._window.median()
        wet = smoothed is not None and smoothed > self.calibration.field_capacity
        if self._wet.mark(when, bad=wet):
            self._dirty = True

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

    def intervals(self) -> dict[str, list[Interval]]:
        """The budgets' closed stretches, keyed for the store."""
        out = {SILENCE: self._silence.closed()}
        if self._wet is not None:
            out[WET] = self._wet.closed()
        return out

    def take_dirty(self) -> bool:
        """Whether a budget has closed a stretch since this was last asked —
        the moment there is something new worth persisting."""
        dirty, self._dirty = self._dirty, False
        return dirty

    def health(self, now: datetime) -> list[HealthIssue]:
        """Faults that would otherwise read as healthy soil.

        Evaluated against an injected `now` rather than the clock, so every
        duration here — hours silent, days of dropouts, a week wet — is
        testable without waiting.

        Battery is not here: it belongs to a different entity, and this type
        only sees moisture readings.
        """
        issues: list[HealthIssue] = []

        stale = timedelta(hours=self.policy.stale_hours(self.probe))
        # Before the first reading, silence is measured from when this started
        # listening. With neither, there is no clock to judge against and
        # claiming a fault would be guessing.
        since = self._last.when if self._last is not None else self._started_at
        silent_for = None if since is None else now - since

        # Covers both a probe gone `unavailable` and one that holds a plausible
        # number forever because zigbee2mqtt publishes bridge availability
        # rather than per-device: such a probe never goes unavailable, it just
        # stops talking, and the last reading sits there looking fine.
        if silent_for is not None and silent_for >= stale:
            hours = round(silent_for.total_seconds() / 3600, 1)
            issues.append(
                HealthIssue(
                    kind=IssueKind.PROBE_SILENT,
                    label="Probe not reporting",
                    detail=(
                        f"Silent for {hours:g} hours. Nothing can tell whether this "
                        "plant needs water until the probe is back. Check the battery "
                        "and that zigbee2mqtt still sees it."
                    ),
                    value=hours,
                )
            )
            # Everything below infers from readings, and there are none worth
            # trusting. Reporting a stuck or flaky probe on top of a silent one
            # would be two alerts for one fault.
            return issues

        # The slow burn: not down now, but down often. The stretch since the
        # last report is still open and counts too, past its heartbeat.
        open_since = None
        if self._last is not None:
            open_since = self._last.when + timedelta(
                minutes=self.probe.heartbeat_minutes
            )
        window = self.policy.availability_window()
        rate = self._silence.burn_rate(now, window, open_since)
        if rate >= self.policy.availability_slow_burn_rate:
            down = self._silence.accrued(now, window, open_since)
            issues.append(
                HealthIssue(
                    kind=IssueKind.PROBE_FLAKY,
                    label="Probe is flaky",
                    detail=(
                        f"Silent for {down.total_seconds() / 60:.0f} minutes in total "
                        f"over the last {self.policy.availability_window_days} days, "
                        f"against an allowance of "
                        f"{self._silence.allowance.total_seconds() / 60:.0f} "
                        f"({self.policy.availability_slo_pct:g}% availability). Every "
                        "dropout healed on its own, which is why nothing else "
                        "reported it. A dying battery, a pot at the edge of range, or "
                        "a link that wants a repeater."
                    ),
                    value=round(rate, 2),
                )
            )

        if self._last_change is not None:
            unchanged = now - self._last_change.when
            if unchanged >= timedelta(hours=self.policy.stuck_hours):
                issues.append(
                    HealthIssue(
                        kind=IssueKind.PROBE_STUCK,
                        label="Probe reading is stuck",
                        detail=(
                            "A pot in use always drifts as it dries. A perfectly flat "
                            "reading means the probe is out of the soil, has lost "
                            "contact, or its firmware has wedged."
                        ),
                        value=round(unchanged.total_seconds() / 3600, 1),
                    )
                )

        waterlogged = self._waterlogged(now)
        if waterlogged is not None:
            issues.append(waterlogged)

        if self._shortfall is not None and isinstance(self.calibration, Calibrated):
            target = self.policy.fc_shortfall_target(self.calibration)
            issues.append(
                HealthIssue(
                    kind=IssueKind.WATERING_SHORTFALL,
                    label="Watering fell short",
                    detail=(
                        f"An hour after watering the soil settled at "
                        f"{self._shortfall:.1f}%, below the {target:.1f}% it should "
                        "reach. Water probably channelled down the side — try again "
                        "slowly, or soak the pot from below."
                    ),
                    value=round(self._shortfall, 1),
                )
            )

        return issues

    def _waterlogged(self, now: datetime) -> HealthIssue | None:
        """Time above field capacity, at two speeds.

        Fast: wet for the whole of the last day — the pot is not draining now.
        Slow: wet for more of the week than this plant tolerates, even if never
        a whole day at a stretch — the pot is being overwatered.
        """
        if self._wet is None or not isinstance(self.calibration, Calibrated):
            return None

        budget_pct = self.policy.waterlogged_budget_pct(self.calibration)
        fast_over = timedelta(days=self.policy.waterlogged_fast_days)
        fast_rate = self._wet.burn_rate(now, fast_over)
        if fast_rate >= self.policy.waterlogged_fast_burn_rate:
            hours = self._wet.accrued(now, fast_over).total_seconds() / 3600
            return HealthIssue(
                kind=IssueKind.WATERLOGGED,
                label="Not draining",
                detail=(
                    f"Above field capacity for {hours:.0f} of the last "
                    f"{fast_over.total_seconds() / 3600:.0f} hours. Check the pot is "
                    "not standing in its own runoff and the drainage holes are clear."
                ),
                value=round(fast_rate, 2),
            )

        window = self.policy.waterlogged_window()
        slow_rate = self._wet.burn_rate(now, window)
        if slow_rate >= self.policy.waterlogged_slow_burn_rate:
            wet_pct = self._wet.accrued(now, window) / window * 100
            return HealthIssue(
                kind=IssueKind.WATERLOGGED,
                label="Overwatered",
                detail=(
                    f"Above field capacity {wet_pct:.0f}% of the last "
                    f"{self.policy.waterlogged_window_days} days, against a budget "
                    f"of {budget_pct:g}%. It drains, but it never dries out: water "
                    "less often, or less each time."
                ),
                value=round(slow_rate, 2),
            )

        return None
