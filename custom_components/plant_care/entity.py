"""Shared entity plumbing.

Every entity this component creates belongs to a plant, gets its id from
`model.naming`, and groups under a device so a plant reads as one thing in the
UI rather than a dozen loose sensors.
"""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity as HassEntity

from .model import Entity, LightFixture, LuxFixture, Plant

DOMAIN = "plant_care"


class PlantEntity(HassEntity):
    """Base for anything belonging to one plant."""

    _attr_should_poll = False
    _attr_has_entity_name = False

    def __init__(self, plant: Plant, entity: Entity, name: str) -> None:
        self._plant = plant
        # `entity_id` is assigned rather than suggested: these ids are part of
        # the contract with the dashboard and with anything reading the feed, so
        # they must not drift because a friendly name changed.
        self.entity_id = entity.full
        self._attr_unique_id = entity.full
        self._attr_name = name

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._plant.name)},
            name=self._plant.display,
            manufacturer="plant_care",
            model=self._plant.species or "plant",
        )


class FixtureEntity(HassEntity):
    """Base for anything belonging to a light or lux fixture.

    A separate device from the plants, because a fixture is shared: hanging its
    killswitch off one of the plants under it would be arbitrary, and would hide
    it from anyone looking at the others.
    """

    _attr_should_poll = False
    _attr_has_entity_name = False

    def __init__(
        self, fixture: LightFixture | LuxFixture, entity: Entity, name: str
    ) -> None:
        self._fixture = fixture
        self.entity_id = entity.full
        self._attr_unique_id = entity.full
        self._attr_name = name

    @property
    def device_info(self) -> DeviceInfo:
        room = getattr(self._fixture, "room", None)
        return DeviceInfo(
            identifiers={(DOMAIN, f"fixture_{self._fixture.name}")},
            name=self._fixture.name.replace("_", " ").title(),
            manufacturer="plant_care",
            model="grow light" if room else "lux group",
            suggested_area=room,
        )
