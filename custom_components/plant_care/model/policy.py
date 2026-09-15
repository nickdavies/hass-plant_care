"""The decisions — as opposed to the facts in `plant.py`.

These constants moved here from the generator, which had no business holding
them: how much of a pot's available water counts as "needs water" is a judgement
about plant care, not a fact about a zigbee device, and the generator exports
only facts.

Gathered into a frozen dataclass with defaults rather than left as module
constants so a test can vary one without monkeypatching, and so per-plant
overrides have somewhere to go later without a signature change everywhere.
"""

from __future__ import annotations

from dataclasses import dataclass

from .plant import Calibrated, ProbeFacts


@dataclass(frozen=True)
class Policy:
    """How to treat a plant. Every value here is a choice, not a measurement."""

    refill_available_water_pct: float = 15.0
    """Flag the plant with 15% of its available water left.

    Deliberately above the point a plant is judged thirsty by hand, so the flag
    raises slightly early — the error worth making, since passionfruit drops
    flowers and young fruit under water stress.
    """

    watering_rise_pct_of_span: float = 25.0
    """A rise of a quarter of the span counts as a watering.

    Large enough that sensor noise cannot reach it, small enough that a stingy
    top-up still registers. A splash deliberately does not clear the flag.
    """

    default_fc_tolerance: float = 5.0
    """How far below field capacity a settled watering may land before it is
    reported as having fallen short. Percentage points of raw reading, not of
    span."""

    dry_confirm_hours: int = 2
    """How long the smoothed reading must hold below the threshold before the
    plant is flagged. At a 10 minute heartbeat that is about a dozen consecutive
    reports agreeing.

    This, not the median window, is what sets alert latency."""

    median_window_heartbeats: int = 6
    """How far back the median looks, in heartbeats.

    Long enough for a median to discard a single bad read, short enough that
    real dry-down does not bias it. A pot loses roughly 0.7-1.6 points a day, so
    an hour of genuine drying is 0.03-0.07 points — far below a typical probe's
    0.16pp quantisation, which means the window holds a flat signal plus noise,
    the only thing a median is good at. Widen it and the median starts trailing
    a falling reading, delaying the flag rather than steadying it.

    Note this does nothing about the daily soil-temperature artifact under grow
    lights: that is a ~24h oscillation and needs compensation, not a wider
    window.
    """

    stale_heartbeats: int = 12
    """Missed reports before a probe is treated as not reporting. Generous
    enough to ride out a zigbee2mqtt restart or a Flux redeploy."""

    # ---- Health checks -------------------------------------------------

    battery_low_pct: float = 15.0
    """Enough warning to find a battery before the probe goes silent."""

    stuck_hours: int = 12
    """Reporting fine, but the value has not moved.

    An actively transpiring pot always drifts downward. A perfectly flat reading
    means the probe is out of the soil, has lost contact, or its firmware has
    wedged — none of which look like anything from a threshold's point of view.
    """

    waterlogged_hours: int = 24
    """Sitting above field capacity this long. The pot is not draining, or it is
    standing in its own runoff."""

    shortfall_settle_minutes: int = 60
    """How long after a watering to judge whether it worked.

    The same hour the calibration protocol uses to read field capacity, so the
    comparison is like with like. Any other interval would be measuring against
    a number taken differently.
    """

    # ---- Derived from probe facts -------------------------------------

    def median_window_hours(self, probe: ProbeFacts) -> int:
        return max(
            1, _ceil_div(probe.heartbeat_minutes * self.median_window_heartbeats, 60)
        )

    def median_sampling_size(self, probe: ProbeFacts) -> int:
        """A cap that should never bind; the window governs the median.

        Five times the guaranteed hourly heartbeat count, because a
        report-on-change deadband means extra reports arrive whenever the value
        actually moves.
        """
        return (60 // probe.heartbeat_minutes) * 5

    def stale_hours(self, probe: ProbeFacts) -> int:
        return max(1, _ceil_div(probe.heartbeat_minutes * self.stale_heartbeats, 60))

    # ---- Derived from a calibration -----------------------------------

    def refill_threshold(self, calibration: Calibrated) -> float:
        """The raw reading at which the plant is flagged."""
        return calibration.raw_at(self.refill_available_water_pct)

    def watering_rise(self, calibration: Calibrated) -> float:
        """The rise above a trailing minimum that counts as a watering."""
        return calibration.span * self.watering_rise_pct_of_span / 100.0

    def fc_tolerance(self, calibration: Calibrated) -> float:
        """The configured tolerance, or the default when none was chosen."""
        if calibration.fc_tolerance is None:
            return self.default_fc_tolerance
        return calibration.fc_tolerance

    def fc_shortfall_target(self, calibration: Calibrated) -> float:
        """What a settled watering should reach an hour after watering.

        Below this and the water probably channelled down the side of a
        hydrophobic rootball rather than wetting the core — the most common real
        watering failure, and invisible without a probe at depth.
        """
        return calibration.field_capacity - self.fc_tolerance(calibration)


def _ceil_div(numerator: int, denominator: int) -> int:
    return -(-numerator // denominator)


DEFAULT_POLICY = Policy()
