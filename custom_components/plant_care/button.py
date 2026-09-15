"""Mark-done buttons for care tasks.

One per (plant, task). A button rather than a dashboard action writing a
timestamp directly, so the press is a real state change — it shows in the
logbook and anything else can trigger on it.

There is deliberately no watering button on a calibrated plant: a moisture probe
sees a watering by anyone, with no phone nearby and no discipline required, so a
button there would be a worse second source of truth. The generator enforces
that by refusing to schedule a detectable task, so by the time a task reaches
here it is one nothing can detect.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType
from homeassistant.util import dt as dt_util

from .const import ATTR_PLANT, ATTR_TASK, DOMAIN, SIGNAL_CARE_UPDATED
from .entity import PlantEntity
from .model import CareTask, Plant, naming
from .store import CareLog

_LOGGER = logging.getLogger(__name__)


async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    async_add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    if discovery_info is None:
        return

    data = hass.data[DOMAIN]
    async_add_entities(
        CareDoneButton(plant, task, data.care_log)
        for plant in data.plants
        for task in plant.care
    )


class CareDoneButton(PlantEntity, ButtonEntity):
    def __init__(self, plant: Plant, task: CareTask, care_log: CareLog) -> None:
        super().__init__(
            plant,
            naming.care_done(plant, task),
            f"{plant.display} {task.display.lower()} done",
        )
        self._task = task
        self._care_log = care_log
        self._attr_icon = task.icon

    async def async_press(self) -> None:
        now = dt_util.utcnow()
        await self._care_log.async_mark_done(self._plant.name, self._task.task, now)
        _LOGGER.debug(
            "plant_care: %s %s marked done at %s",
            self._plant.name,
            self._task.task,
            now,
        )
        async_dispatcher_send(self.hass, SIGNAL_CARE_UPDATED)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {ATTR_PLANT: self._plant.name, ATTR_TASK: self._task.task}
