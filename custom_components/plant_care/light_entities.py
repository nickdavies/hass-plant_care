"""Entities that read the light controllers and DLI coordinators.

Thin, like the moisture ones: every value here comes straight from an object
that is tested on its own, so nothing needs a running Home Assistant to know
whether it is right.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.core import callback

from .dli import DliCoordinator
from .entity import FixtureEntity, PlantEntity
from .light_control import LightController
from .model import naming

LUX = "lx"
MINUTES = "min"
MOL_PER_SQUARE_METRE = "mol/m²"


class LightOnMinutesSensor(FixtureEntity, SensorEntity):
    """Minutes this fixture has actually been on today, against its window.

    The state is what happened; the attributes are what should have. Both are
    here because the pair is the diagnosis — 0 of a guaranteed 480 is a dead
    lamp, 1440 of a possible 780 is one stuck on, and everything between is a
    lamp doing its job.

    `tracked_minutes` is there so the pair still reads as a pair after an
    outage long enough to reopen the comparison: the expectations then cover
    less than the day the state reports, and it is the number they are actually
    being compared against.
    """

    _attr_native_unit_of_measurement = MINUTES
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:lightbulb-on-outline"

    def __init__(self, controller: LightController) -> None:
        fixture = controller.fixture
        super().__init__(
            fixture,
            naming.light_on_minutes(fixture),
            f"{fixture.name.replace('_', ' ').title()} on time",
        )
        self._controller = controller

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self._controller.async_add_listener(self._handle_update))

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()

    @property
    def native_value(self) -> int:
        return self._controller.on_minutes

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        guaranteed, possible = self._controller.expectation()
        deviation = self._controller.deviation()
        return {
            "tracked_minutes": self._controller.tracked_minutes,
            "guaranteed_minutes": guaranteed,
            "possible_minutes": possible,
            "killswitch": self._controller.killed,
            "deviation": deviation.direction.value if deviation else None,
        }


class LuxAverageSensor(FixtureEntity, SensorEntity):
    """A lux fixture's members, averaged.

    Belongs to the fixture rather than to a plant because several plants read
    the same number, and two copies of it could disagree.
    """

    _attr_native_unit_of_measurement = LUX
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:brightness-5"

    def __init__(self, coordinator: DliCoordinator) -> None:
        fixture = coordinator.lux_fixture
        super().__init__(
            fixture,
            naming.lux_average(fixture),
            f"{fixture.name.replace('_', ' ').title()} lux",
        )
        self._coordinator = coordinator

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self._coordinator.async_add_listener(self._handle_update))

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()

    @property
    def native_value(self) -> float | None:
        value = self._coordinator.lux
        return round(value, 1) if value is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"members": list(self._fixture.entities)}


class DliTodaySensor(PlantEntity, SensorEntity):
    """Light accumulated so far today, with the objective as attributes.

    Deliberately `MEASUREMENT` rather than `TOTAL_INCREASING`: it resets to zero
    at local midnight by design, and a total-increasing sensor that drops is
    read by the statistics engine as a meter replacement.
    """

    _attr_native_unit_of_measurement = MOL_PER_SQUARE_METRE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:white-balance-sunny"

    # The state moves every minute and the history is four weeks of days, so
    # recording it would write the same month of numbers into the database
    # fourteen hundred times a day. It is already durable in the component's own
    # store; this copy exists for the dashboard and for template access.
    _unrecorded_attributes = frozenset({"history"})

    def __init__(self, coordinator: DliCoordinator) -> None:
        plant = coordinator.plant
        super().__init__(plant, naming.dli_today(plant), f"{plant.display} DLI today")
        self._coordinator = coordinator

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self._coordinator.async_add_listener(self._handle_update))

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()

    @property
    def native_value(self) -> float:
        return self._coordinator.today

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        objective = self._coordinator.plant.dli
        assert objective is not None  # a coordinator cannot exist without one
        attributes: dict[str, Any] = {
            "category": objective.category,
            "preferred_low": objective.preferred.low,
            "preferred_high": objective.preferred.high,
            "budget": objective.budget,
            "window_days": objective.window_days,
            # Surfaced so the reason a band differs from its category stays
            # visible months later, rather than looking like a mistake.
            "preferred_overridden": objective.preferred_overridden,
            "history": {
                day.day.isoformat(): day.value for day in self._coordinator.history()
            },
        }
        if objective.survival is not None:
            attributes["survival_low"] = objective.survival.low
            attributes["survival_high"] = objective.survival.high
        return attributes
