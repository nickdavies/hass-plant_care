"""The derivations that moved here from the generator.

These were pinned in Rust against the hand-written passionfruit config. That
config is what this component replaces, so the gate moves here with them: if
these numbers drift, a plant that has been watered correctly for months starts
being judged differently.
"""

from __future__ import annotations

import pytest

from custom_components.plant_care.model import (
    DEFAULT_POLICY,
    Calibrated,
    Policy,
    ProbeFacts,
)

# The passionfruit, measured 2026-08-27. See inputs/plants.yaml in homelab-data
# for how these were taken and why an earlier fieldCapacity was discarded.
PASSIONFRUIT = Calibrated(field_capacity=79.54, dry_point=53.18, fc_tolerance=8.0)

# ThirdReality 3RSM0347Z: ~10 minute heartbeat, >=1% report-on-change deadband.
THIRD_REALITY_GEN2 = ProbeFacts(heartbeat_minutes=10, deadband_pp=1.0)


class TestPassionfruitRegression:
    """Must reproduce what packages/plants/plant_wiring.yaml carried by hand."""

    def test_refill_threshold(self) -> None:
        # The hand-written file carried 57.1, rounded to one decimal. The
        # difference is below the probe's own 0.16pp quantisation, so no
        # threshold crossing changes.
        assert DEFAULT_POLICY.refill_threshold(PASSIONFRUIT) == pytest.approx(
            57.13, abs=0.01
        )

    def test_watering_rise(self) -> None:
        # Hand-written: 6.5, again rounded from 6.59.
        assert DEFAULT_POLICY.watering_rise(PASSIONFRUIT) == pytest.approx(
            6.59, abs=0.01
        )

    def test_fc_tolerance_override_is_honoured(self) -> None:
        # 8 rather than the default 5, because two waterings that both produced
        # free runoff landed 15 points apart.
        assert DEFAULT_POLICY.fc_tolerance(PASSIONFRUIT) == 8.0

    def test_fc_shortfall_target(self) -> None:
        assert DEFAULT_POLICY.fc_shortfall_target(PASSIONFRUIT) == pytest.approx(71.54)

    def test_probe_derived_windows(self) -> None:
        # plant_signals.yaml carried max_age 1h / sampling_size 30, and
        # plant_sensor_health.yaml carried stale_hours 2.
        assert DEFAULT_POLICY.median_window_hours(THIRD_REALITY_GEN2) == 1
        assert DEFAULT_POLICY.median_sampling_size(THIRD_REALITY_GEN2) == 30
        assert DEFAULT_POLICY.stale_hours(THIRD_REALITY_GEN2) == 2


class TestAvailableWaterScale:
    """Decisions are made on available water, not raw counts, because raw
    capacitive readings are not portable between pots."""

    def test_endpoints_map_to_zero_and_one_hundred(self) -> None:
        assert PASSIONFRUIT.available_water(53.18) == pytest.approx(0.0)
        assert PASSIONFRUIT.available_water(79.54) == pytest.approx(100.0)

    def test_round_trips_with_raw_at(self) -> None:
        for pct in (0.0, 15.0, 50.0, 100.0):
            raw = PASSIONFRUIT.raw_at(pct)
            assert PASSIONFRUIT.available_water(raw) == pytest.approx(pct)

    def test_the_threshold_sits_above_the_measured_dry_point(self) -> None:
        """Deliberately early: passionfruit drops flowers and young fruit under
        water stress, so the error worth making is flagging too soon."""
        assert DEFAULT_POLICY.refill_threshold(PASSIONFRUIT) > PASSIONFRUIT.dry_point

    def test_available_water_can_go_negative(self) -> None:
        """Below the measured dry point is meaningful, not an error — it says
        the plant is drier than it has ever been observed."""
        assert PASSIONFRUIT.available_water(50.0) < 0


class TestPolicyIsData:
    def test_a_different_probe_gets_different_windows(self) -> None:
        """The point of deriving from probe facts: swapping hardware does not
        mean editing any per-plant config."""
        slow = ProbeFacts(heartbeat_minutes=30, deadband_pp=0.5)
        assert DEFAULT_POLICY.median_window_hours(slow) == 3
        assert DEFAULT_POLICY.stale_hours(slow) == 6

    def test_the_default_tolerance_applies_only_when_unset(self) -> None:
        unset = Calibrated(field_capacity=80.0, dry_point=50.0)
        assert DEFAULT_POLICY.fc_tolerance(unset) == DEFAULT_POLICY.default_fc_tolerance

    def test_policy_can_be_varied_without_monkeypatching(self) -> None:
        eager = Policy(refill_available_water_pct=40.0)
        assert eager.refill_threshold(PASSIONFRUIT) > DEFAULT_POLICY.refill_threshold(
            PASSIONFRUIT
        )


class TestModelInvariants:
    def test_an_inverted_calibration_cannot_be_constructed(self) -> None:
        with pytest.raises(ValueError, match="must be above"):
            Calibrated(field_capacity=50.0, dry_point=60.0)

    def test_an_equal_calibration_cannot_be_constructed(self) -> None:
        """Zero span would make available_water a division by zero."""
        with pytest.raises(ValueError):
            Calibrated(field_capacity=50.0, dry_point=50.0)

    def test_a_zero_heartbeat_probe_cannot_be_constructed(self) -> None:
        with pytest.raises(ValueError, match="positive"):
            ProbeFacts(heartbeat_minutes=0, deadband_pp=1.0)
