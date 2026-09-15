"""Sensors: days-since per care task, per-plant attention, and the feed."""

from __future__ import annotations

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
from .store import CareLog

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
            entities.append(CareDaysSinceSensor(plant, task, data.care_log))
        entities.append(PlantAttentionSensor(plant, data.care_log))

    entities.append(OutstandingSensor(data.plants, data.care_log))
    async_add_entities(entities)


class _CareDrivenSensor(PlantEntity, SensorEntity):
    """Recomputes on a care press and on a slow timer.

    Two triggers because the underlying quantity changes for two different
    reasons: a press changes it instantly, and the passage of time changes it
    continuously. Neither alone is enough.
    """

    def __init__(self, plant: Plant, entity, name: str, care_log: CareLog) -> None:
        super().__init__(plant, entity, name)
        self._care_log = care_log

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

    def __init__(self, plant: Plant, task: CareTask, care_log: CareLog) -> None:
        super().__init__(
            plant,
            naming.care_days_since(plant, task),
            f"{plant.display} {task.display.lower()} days since",
            care_log,
        )
        self._task = task
        self._attr_icon = task.icon

    @property
    def native_value(self) -> float | None:
        return self._care_log.days_since(
            self._plant.name, self._task.task, dt_util.utcnow()
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"every_days": self._task.every_days}


def _care_items(plant: Plant, care_log: CareLog) -> list[dict[str, Any]]:
    """Everything this plant currently needs, as plain dicts.

    Plain dicts because this becomes a state attribute, and a consumer — a
    dashboard card now, a task-system bridge later — should not have to know
    anything about this component's types to read it.
    """
    now = dt_util.utcnow()
    items: list[dict[str, Any]] = []

    for task in plant.care:
        days = care_log.days_since(plant.name, task.task, now)
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

    def __init__(self, plant: Plant, care_log: CareLog) -> None:
        super().__init__(
            plant, naming.attention(plant), f"{plant.display} attention", care_log
        )

    @property
    def native_value(self) -> int:
        return len(_care_items(self._plant, self._care_log))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {ATTR_ITEMS: _care_items(self._plant, self._care_log)}


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

    def __init__(self, plants: tuple[Plant, ...], care_log: CareLog) -> None:
        entity = naming.outstanding()
        self.entity_id = entity.full
        self._attr_unique_id = entity.full
        self._attr_name = "Plants outstanding"
        self._plants = plants
        self._care_log = care_log

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

    def _items(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for plant in self._plants:
            items.extend(_care_items(plant, self._care_log))
        return items

    @property
    def native_value(self) -> int:
        return len(self._items())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {ATTR_ITEMS: self._items()}
