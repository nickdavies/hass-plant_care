"""Entities that read a plant's moisture coordinator.

Thin on purpose. Every one of these is a view onto `MoistureMonitor`, which is
where the judgement lives and where it is tested; an entity that computed
anything itself would be untestable without a running Home Assistant.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.core import callback
from homeassistant.util import dt as dt_util

from .entity import PlantEntity
from .model import Calibrated, Plant, naming
from .moisture import MoistureCoordinator

PERCENT = "%"


class _CoordinatorEntity(PlantEntity):
    """Re-reads whenever the coordinator says something changed."""

    def __init__(self, coordinator: MoistureCoordinator, entity, name: str) -> None:
        super().__init__(coordinator.plant, entity, name)
        self._coordinator = coordinator

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self._coordinator.async_add_listener(self._handle_update))

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()


class SmoothedMoistureSensor(_CoordinatorEntity, SensorEntity):
    """The median over the policy window.

    What threshold crossings are measured against, so a single bad read cannot
    flag a plant. `unknown` until the window has a reading — after a restart
    there is genuinely nothing to say, and a zero would read as bone dry.
    """

    _attr_native_unit_of_measurement = PERCENT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:water-percent"

    def __init__(self, coordinator: MoistureCoordinator) -> None:
        plant = coordinator.plant
        super().__init__(
            coordinator,
            naming.moisture_smoothed(plant),
            f"{plant.display} moisture (median)",
        )

    @property
    def native_value(self) -> float | None:
        return self._coordinator.signals.smoothed


class MoistureMinimumSensor(_CoordinatorEntity, SensorEntity):
    """Trailing minimum over the window. Exists to make the rise below
    explicable on the dashboard — on its own it says little."""

    _attr_native_unit_of_measurement = PERCENT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:arrow-collapse-down"
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: MoistureCoordinator) -> None:
        plant = coordinator.plant
        super().__init__(
            coordinator, naming.moisture_min(plant), f"{plant.display} moisture (low)"
        )

    @property
    def native_value(self) -> float | None:
        return self._coordinator.signals.minimum


class MoistureRiseSensor(_CoordinatorEntity, SensorEntity):
    """How far the raw reading has risen above its own trailing minimum.

    This is the watering detector made visible. During a dry-down the minimum
    tracks the reading down, so it sits near zero; an hour after a watering the
    minimum has caught up and it re-arms itself.
    """

    _attr_native_unit_of_measurement = PERCENT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:trending-up"

    def __init__(self, coordinator: MoistureCoordinator) -> None:
        plant = coordinator.plant
        super().__init__(
            coordinator, naming.moisture_rise(plant), f"{plant.display} moisture rise"
        )

    @property
    def native_value(self) -> float | None:
        return self._coordinator.signals.rise


class AvailableWaterSensor(_CoordinatorEntity, SensorEntity):
    """Percentage of available water remaining: 0% at the measured dry point,
    100% at field capacity.

    Only created for a calibrated plant, because without both endpoints there is
    no scale to express it on.
    """

    _attr_native_unit_of_measurement = PERCENT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:water-percent"

    def __init__(self, coordinator: MoistureCoordinator) -> None:
        plant = coordinator.plant
        super().__init__(
            coordinator,
            naming.available_water(plant),
            f"{plant.display} available water",
        )

    @property
    def native_value(self) -> float | None:
        return self._coordinator.signals.available_water


class DaysSinceWateredSensor(_CoordinatorEntity, SensorEntity):
    """How long since a watering was detected.

    Detected, not declared — there is no "I watered it" button, because the
    probe sees a watering by anyone with no phone anywhere near it.
    """

    _attr_native_unit_of_measurement = "d"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:watering-can"

    def __init__(self, coordinator: MoistureCoordinator, event_log) -> None:
        plant = coordinator.plant
        super().__init__(
            coordinator,
            naming.days_since_watered(plant),
            f"{plant.display} days since watered",
        )
        self._event_log = event_log

    @property
    def native_value(self) -> float | None:
        return self._event_log.days_since_watered(self._plant.name, dt_util.utcnow())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """The exact moment, so "3.2 days" can be checked against memory and
        corrected with `plant_care.record_watering` if it is wrong."""
        last = self._event_log.last_watered(self._plant.name)
        return {"last_watered": None if last is None else last.isoformat()}


class NeedsWaterBinarySensor(_CoordinatorEntity, BinarySensorEntity):
    """Whether the plant is flagged as needing water.

    A latch, not a comparison: set once the smoothed reading has held below the
    threshold long enough to be believed, and cleared only by a detected
    watering. Moisture drifting up on its own — a cool night, a probe settling —
    is not someone having watered the plant.

    Only created for a calibrated plant. A plant still calibrating can be
    monitored but not judged, because any threshold would be invented.
    """

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_icon = "mdi:water-alert"

    def __init__(self, coordinator: MoistureCoordinator) -> None:
        plant = coordinator.plant
        super().__init__(
            coordinator, naming.needs_water(plant), f"{plant.display} needs water"
        )

    @property
    def is_on(self) -> bool:
        return self._coordinator.needs_water

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        calibration = self._plant.moisture.calibration
        if not isinstance(calibration, Calibrated):
            return {}
        return {
            "refill_threshold": round(
                self._coordinator.monitor.policy.refill_threshold(calibration), 2
            ),
            "field_capacity": calibration.field_capacity,
            "dry_point": calibration.dry_point,
        }


def moisture_sensors(coordinator: MoistureCoordinator, event_log) -> list[SensorEntity]:
    """Which sensors a plant gets, derived from whether it is calibrated."""
    plant: Plant = coordinator.plant
    entities: list[SensorEntity] = [
        SmoothedMoistureSensor(coordinator),
        MoistureMinimumSensor(coordinator),
        MoistureRiseSensor(coordinator),
    ]
    if isinstance(plant.moisture.calibration, Calibrated):
        entities.append(AvailableWaterSensor(coordinator))
        entities.append(DaysSinceWateredSensor(coordinator, event_log))
    return entities
