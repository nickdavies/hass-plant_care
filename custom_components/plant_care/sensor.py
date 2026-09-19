"""Sensors: days-since per care task, per-plant attention, and the feed."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType
from homeassistant.util import dt as dt_util

from .const import ATTR_ITEMS, DOMAIN, RECOMPUTE_INTERVAL, SIGNAL_CARE_UPDATED
from .entity import PlantEntity
from .feed import all_items, person_items, plant_items
from .light_entities import DliTodaySensor, LightOnMinutesSensor, LuxAverageSensor
from .model import CareTask, Entity, Plant, naming
from .moisture_entities import moisture_sensors
from .store import EventLog

if TYPE_CHECKING:
    from . import PlantCareData


async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    async_add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    if discovery_info is None:
        return

    data: PlantCareData = hass.data[DOMAIN]

    entities: list[SensorEntity] = []
    lux_seen: set[str] = set()
    for plant in data.plants:
        for task in plant.care:
            entities.append(CareDaysSinceSensor(plant, task, data.event_log))
        entities.append(PlantAttentionSensor(data, plant))

        coordinator = data.coordinators.get(plant.name)
        if coordinator is not None:
            entities.extend(moisture_sensors(coordinator, data.event_log))

        dli = data.dli_coordinators.get(plant.name)
        if dli is not None:
            entities.append(DliTodaySensor(dli))
            # One entity per lux *fixture*, not per plant: several plants can
            # share a fixture, and a second copy would claim the same entity id
            # and silently win. Whichever coordinator backs it reads the same
            # entities and averages them the same way.
            if dli.lux_fixture.name not in lux_seen:
                lux_seen.add(dli.lux_fixture.name)
                entities.append(LuxAverageSensor(dli))

    entities.extend(
        LightOnMinutesSensor(controller)
        for controller in data.light_controllers.values()
    )
    entities.append(OutstandingSensor(data))
    entities.extend(
        PersonOutstandingSensor(data, person) for person in data.config.people()
    )
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


class PlantAttentionSensor(_CareDrivenSensor):
    """How many things this plant needs, with the list as an attribute."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:alert-circle-outline"
    _unrecorded_attributes = frozenset({ATTR_ITEMS})

    def __init__(self, data: PlantCareData, plant: Plant) -> None:
        super().__init__(
            plant, naming.attention(plant), f"{plant.display} attention", data.event_log
        )
        self._data = data

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        # Also re-read when a measurement moves: needs-water and the light
        # checks are items too, and neither is driven by a care press.
        coordinator = self._data.coordinators.get(self._plant.name)
        if coordinator is not None:
            self.async_on_remove(coordinator.async_add_listener(self._handle_update))
        dli = self._data.dli_coordinators.get(self._plant.name)
        if dli is not None:
            self.async_on_remove(dli.async_add_listener(self._handle_update))

    def _items(self) -> list[dict[str, Any]]:
        return plant_items(self._data, self._plant)

    @property
    def native_value(self) -> int:
        return len(self._items())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {ATTR_ITEMS: self._items()}


class _FeedSensor(SensorEntity):
    """A count of outstanding items with the list as an attribute.

    Not a `PlantEntity`: a feed spans plants, so it gets no device.

    Built by walking the plants directly rather than by reading the per-plant
    attention sensors' attributes. Reading state would make this depend on the
    order entities happen to update in, and would go stale for one cycle every
    time anything changed; walking the same source they walk cannot disagree
    with them.
    """

    _attr_should_poll = False
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:format-list-checks"

    # The count is worth a history; the list is prose, re-derivable at any time,
    # and would be written into the database on every measurement that moves.
    _unrecorded_attributes = frozenset({ATTR_ITEMS})

    def __init__(self, data: PlantCareData, entity: Entity, name: str) -> None:
        self.entity_id = entity.full
        self._attr_unique_id = entity.full
        self._attr_name = name
        self._data = data

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
        for coordinator in self._data.coordinators.values():
            self.async_on_remove(coordinator.async_add_listener(self._handle_update))
        for dli in self._data.dli_coordinators.values():
            self.async_on_remove(dli.async_add_listener(self._handle_update))
        for controller in self._data.light_controllers.values():
            self.async_on_remove(controller.async_add_listener(self._handle_update))

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()

    @callback
    def _handle_interval(self, _now) -> None:
        self.async_write_ha_state()

    def _items(self) -> list[dict[str, Any]]:
        raise NotImplementedError

    @property
    def native_value(self) -> int:
        return len(self._items())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {ATTR_ITEMS: self._items()}


class OutstandingSensor(_FeedSensor):
    """Everything outstanding, across every plant. The integration point."""

    def __init__(self, data: PlantCareData) -> None:
        super().__init__(data, naming.outstanding(), "Plants outstanding")

    def _items(self) -> list[dict[str, Any]]:
        return all_items(self._data)


class PersonOutstandingSensor(_FeedSensor):
    """One person's share of the feed: their plants and their groups' plants."""

    def __init__(self, data: PlantCareData, person: str) -> None:
        super().__init__(
            data,
            naming.person_outstanding(person),
            f"{person.replace('_', ' ').title()}'s plants outstanding",
        )
        self._person = person

    def _items(self) -> list[dict[str, Any]]:
        return person_items(self._data, self._person)
