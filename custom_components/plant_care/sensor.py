"""Sensors: days-since per care task, per-plant attention, and the feed."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import timedelta
from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType
from homeassistant.util import dt as dt_util

from .const import ATTR_ITEMS, DOMAIN, SIGNAL_CARE_UPDATED
from .entity import PlantEntity
from .model import CareTask, Plant, naming
from .moisture import MoistureCoordinator
from .moisture_entities import moisture_sensors
from .store import EventLog

# Days-since only changes meaningfully once an hour; polling faster would burn
# state writes to move a one-decimal number that barely moves.
RECOMPUTE_INTERVAL = timedelta(minutes=30)


async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    async_add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    if discovery_info is None:
        return

    data = hass.data[DOMAIN]

    entities: list[SensorEntity] = []
    for plant in data.plants:
        for task in plant.care:
            entities.append(CareDaysSinceSensor(plant, task, data.event_log))
        entities.append(
            PlantAttentionSensor(
                plant, data.event_log, data.coordinators.get(plant.name)
            )
        )

        coordinator = data.coordinators.get(plant.name)
        if coordinator is not None:
            entities.extend(moisture_sensors(coordinator, data.event_log))

    entities.append(OutstandingSensor(data.plants, data.event_log, data.coordinators))
    async_add_entities(entities)


class _CareDrivenSensor(PlantEntity, SensorEntity):
    """Recomputes on a care press and on a slow timer.

    Two triggers because the underlying quantity changes for two different
    reasons: a press changes it instantly, and the passage of time changes it
    continuously. Neither alone is enough.
    """

    def __init__(self, plant: Plant, entity, name: str, event_log: EventLog) -> None:
        super().__init__(plant, entity, name)
        self._event_log = event_log

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, SIGNAL_CARE_UPDATED, self._handle_update
            )
        )
        self.async_on_remove(
            async_track_time_interval(
                self.hass, self._handle_interval, RECOMPUTE_INTERVAL
            )
        )

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()

    @callback
    def _handle_interval(self, _now) -> None:
        self.async_write_ha_state()


class CareDaysSinceSensor(_CareDrivenSensor):
    """How long since a care task was last done.

    `None` when never done, which Home Assistant renders as `unknown`. That is
    the honest answer and it matters: the hand-written version of this stored
    last-done in an `input_datetime`, which defaults to *today at midnight* when
    unset, so it reported a fraction of a day since something that never
    happened.
    """

    _attr_native_unit_of_measurement = "d"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, plant: Plant, task: CareTask, event_log: EventLog) -> None:
        super().__init__(
            plant,
            naming.care_days_since(plant, task),
            f"{plant.display} {task.display.lower()} days since",
            event_log,
        )
        self._task = task
        self._attr_icon = task.icon

    @property
    def native_value(self) -> float | None:
        return self._event_log.care_days_since(
            self._plant.name, self._task.task, dt_util.utcnow()
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"every_days": self._task.every_days}


def _items_for(
    plant: Plant,
    event_log: EventLog,
    coordinator: MoistureCoordinator | None,
) -> list[dict[str, Any]]:
    """Everything this plant currently needs, as plain dicts.

    Plain dicts because this becomes a state attribute, and a consumer — a
    dashboard card now, a task-system bridge later — should not have to know
    anything about this component's types to read it.

    `kind` separates the sources: `needs_water` is detected by a probe, `care` is
    a schedule someone has to act on, and the health kinds are faults. A consumer
    that treats them identically still works; one that wants to route them
    differently can.

    Faults come first. A silent probe means nothing else about this plant can be
    believed, so it should not be buried under three overdue feedings.
    """
    now = dt_util.utcnow()
    items: list[dict[str, Any]] = []

    if coordinator is not None:
        items.extend(
            issue.as_item(plant.name, plant.display) for issue in coordinator.health()
        )

    if coordinator is not None and coordinator.needs_water:
        items.append(
            {
                "plant": plant.name,
                "name": plant.display,
                "kind": "needs_water",
                "label": "Needs water",
                "days": event_log.days_since_watered(plant.name, now),
            }
        )

    for task in plant.care:
        days = event_log.care_days_since(plant.name, task.task, now)
        if not task.is_overdue(days):
            continue
        items.append(
            {
                "plant": plant.name,
                "name": plant.display,
                "kind": "care",
                "task": task.task,
                "label": task.display,
                "days": days,
                "every": task.every_days,
            }
        )

    return items


class PlantAttentionSensor(_CareDrivenSensor):
    """How many things this plant needs, with the list as an attribute."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:alert-circle-outline"

    def __init__(
        self,
        plant: Plant,
        event_log: EventLog,
        coordinator: MoistureCoordinator | None,
    ) -> None:
        super().__init__(
            plant, naming.attention(plant), f"{plant.display} attention", event_log
        )
        self._coordinator = coordinator

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        # Also re-read when the probe moves: needs-water is one of the items.
        if self._coordinator is not None:
            self.async_on_remove(
                self._coordinator.async_add_listener(self._handle_update)
            )

    @property
    def native_value(self) -> int:
        return len(_items_for(self._plant, self._event_log, self._coordinator))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {ATTR_ITEMS: _items_for(self._plant, self._event_log, self._coordinator)}


class OutstandingSensor(SensorEntity):
    """Everything outstanding, across every plant. The integration point.

    Not a `PlantEntity`: it belongs to no single plant, so it gets no device.

    Built by walking the plants directly rather than by reading the per-plant
    attention sensors' attributes. Reading state would make this depend on the
    order entities happen to update in, and would go stale for one cycle every
    time anything changed; walking the same source they walk cannot disagree
    with them.
    """

    _attr_should_poll = False
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:format-list-checks"

    def __init__(
        self,
        plants: tuple[Plant, ...],
        event_log: EventLog,
        coordinators: Mapping[str, MoistureCoordinator],
    ) -> None:
        entity = naming.outstanding()
        self.entity_id = entity.full
        self._attr_unique_id = entity.full
        self._attr_name = "Plants outstanding"
        self._plants = plants
        self._event_log = event_log
        self._coordinators = coordinators

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, SIGNAL_CARE_UPDATED, self._handle_update
            )
        )
        self.async_on_remove(
            async_track_time_interval(
                self.hass, self._handle_interval, RECOMPUTE_INTERVAL
            )
        )
        for coordinator in self._coordinators.values():
            self.async_on_remove(coordinator.async_add_listener(self._handle_update))

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()

    @callback
    def _handle_interval(self, _now) -> None:
        self.async_write_ha_state()

    def _items(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for plant in self._plants:
            items.extend(
                _items_for(plant, self._event_log, self._coordinators.get(plant.name))
            )
        return items

    @property
    def native_value(self) -> int:
        return len(self._items())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {ATTR_ITEMS: self._items()}
