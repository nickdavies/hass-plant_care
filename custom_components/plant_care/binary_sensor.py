"""Binary sensors: whether each care task is currently due."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType
from homeassistant.util import dt as dt_util

from .const import DOMAIN, SIGNAL_CARE_UPDATED
from .entity import PlantEntity
from .model import Calibrated, CareTask, Plant, naming
from .moisture_entities import NeedsWaterBinarySensor
from .store import EventLog

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

    entities: list[BinarySensorEntity] = []
    for plant in data.plants:
        entities.extend(
            CareDueBinarySensor(plant, task, data.event_log) for task in plant.care
        )
        # Only a calibrated plant gets one: without both measured endpoints
        # there is no threshold that was not invented.
        coordinator = data.coordinators.get(plant.name)
        if coordinator is not None and isinstance(
            plant.moisture.calibration, Calibrated
        ):
            entities.append(NeedsWaterBinarySensor(coordinator))

    async_add_entities(entities)


class CareDueBinarySensor(PlantEntity, BinarySensorEntity):
    """Whether a task is overdue.

    A task never marked done is **off**, not on: adding a plant should not
    produce an instant backlog, the clock starts at the first mark-done. That
    rule lives in `CareTask.is_overdue` so it is stated once and unit tested,
    rather than re-derived here and in the feed.
    """

    def __init__(self, plant: Plant, task: CareTask, event_log: EventLog) -> None:
        super().__init__(
            plant,
            naming.care_due(plant, task),
            f"{plant.display} {task.display.lower()} due",
        )
        self._task = task
        self._event_log = event_log
        self._attr_icon = task.icon

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

    @property
    def is_on(self) -> bool:
        days = self._event_log.care_days_since(
            self._plant.name, self._task.task, dt_util.utcnow()
        )
        return self._task.is_overdue(days)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"every_days": self._task.every_days}
