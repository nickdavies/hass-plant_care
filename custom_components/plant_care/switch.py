"""Per-fixture killswitches.

One switch per grow light, mirroring `switch.killswitch_motion_*` from
light_motion_profiles on purpose: the gesture for "stop automating this thing"
should look the same whichever system owns the thing.

No `RestoreEntity`. The state lives in the component's own store and the entity
reads it, because `RestoreState` brings a value back but stamps it with the
restore time — and the 48-hour catchall that exists to catch a forgotten
killswitch would then be reset by every Home Assistant restart.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

from .const import DOMAIN
from .entity import FixtureEntity
from .light_control import LightController
from .model import naming


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
        LightKillswitch(controller) for controller in data.light_controllers.values()
    )


class LightKillswitch(FixtureEntity, SwitchEntity):
    """Freeze this fixture's automation.

    On means the component stops touching the switch and leaves it exactly where
    it is. It does **not** mean off: forcing a state would take the decision away
    from whoever flipped this, which is the opposite of the point.
    """

    _attr_icon = "mdi:hand-back-left"
    _attr_entity_category = None

    def __init__(self, controller: LightController) -> None:
        fixture = controller.fixture
        super().__init__(
            fixture,
            naming.light_killswitch(fixture),
            f"{fixture.name.replace('_', ' ').title()} killswitch",
        )
        self._controller = controller

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self._controller.async_add_listener(self._handle_update))

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()

    @property
    def is_on(self) -> bool:
        return self._controller.killed

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "room": self._fixture.room,
            "switch": self._fixture.switch_entity,
        }

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._controller.async_set_killed(True)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._controller.async_set_killed(False)
        self.async_write_ha_state()
