"""Care task due-ness, and the entity naming that reads it."""

from __future__ import annotations

import pytest

from custom_components.plant_care.model import CareTask, Domain, Plant, naming

FEED = CareTask(task="feed", display="Feed", icon="mdi:nutrition", every_days=14)


class TestDueness:
    def test_overdue_at_the_interval(self) -> None:
        assert FEED.is_overdue(14.0)
        assert FEED.is_overdue(23.7)

    def test_not_overdue_before_it(self) -> None:
        assert not FEED.is_overdue(13.9)
        assert not FEED.is_overdue(0.0)

    def test_never_done_is_not_overdue(self) -> None:
        """Adding a plant should not immediately produce a backlog; the clock
        starts at the first mark-done."""
        assert not FEED.is_overdue(None)

    def test_a_zero_interval_cannot_be_constructed(self) -> None:
        """Would make a task permanently overdue from the moment it is done."""
        with pytest.raises(ValueError, match="positive"):
            CareTask(task="feed", display="Feed", icon="mdi:nutrition", every_days=0)


class TestNaming:
    """Names exist in one place so the thing that creates an entity and the
    thing that reads it cannot disagree."""

    def setup_method(self) -> None:
        self.plant = Plant(
            name="monstera",
            display="Monstera",
            species=None,
            moisture=None,
            care=(FEED,),
            owner="nick",
        )

    def test_per_plant_names_are_namespaced(self) -> None:
        assert naming.attention(self.plant).full == "sensor.plant_monstera_attention"
        assert (
            naming.needs_water(self.plant).full
            == "binary_sensor.plant_monstera_needs_water"
        )

    def test_care_names_include_the_task(self) -> None:
        assert (
            naming.care_done(self.plant, FEED).full == "button.plant_monstera_feed_done"
        )
        assert (
            naming.care_days_since(self.plant, FEED).full
            == "sensor.plant_monstera_feed_days_since"
        )

    def test_the_feed_is_not_per_plant(self) -> None:
        assert naming.outstanding().full == "sensor.plant_outstanding"

    def test_domains_are_typed_not_strings(self) -> None:
        assert naming.care_done(self.plant, FEED).domain is Domain.BUTTON
        assert naming.moisture_smoothed(self.plant).domain is Domain.SENSOR

    def test_two_plants_never_collide(self) -> None:
        other = Plant(
            name="monstera_two",
            display="Monstera Two",
            species=None,
            moisture=None,
            care=(FEED,),
            owner="nick",
        )
        assert naming.attention(self.plant).full != naming.attention(other).full
