"""Pinning this component's entities through the config bridge.

Every entity here has a unique id, so it is in the entity registry, where the
UI can rename it, move it to an area or hide it. Each platform claims the
entities it adds with the config bridge, which puts them back at every boot
to the component's own values, so what they are stays in git.

Claims are per platform and merged, because each platform sets up on its
own, before Home Assistant has started, which is when the bridge reads them.

The bridge is optional. Without it set up nothing is claimed, and setup logs
a warning once.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING, Final

from .const import DOMAIN

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity import Entity

_LOGGER = logging.getLogger(__name__)

CONFIG_BRIDGE: Final = "config_bridge"

CLAIMED: Final = f"{DOMAIN}_claimed"
"""`hass.data` key: platform to the entity ids it claimed."""


def bridge_is_set_up(hass: HomeAssistant) -> bool:
    return CONFIG_BRIDGE in hass.config.components


def warn_without_bridge(hass: HomeAssistant) -> None:
    if not bridge_is_set_up(hass):
        _LOGGER.warning(
            "config_bridge isn't set up, so UI changes to plant_care's entities "
            "are kept"
        )


def add_and_claim(
    hass: HomeAssistant,
    platform: str,
    async_add_entities: Callable[[Iterable[Entity]], None],
    entities: Iterable[Entity],
) -> None:
    """Add a platform's entities, and claim the ones with unique ids."""
    entities = list(entities)
    async_add_entities(entities)
    if not bridge_is_set_up(hass):
        return
    claimed = hass.data.setdefault(CLAIMED, {})
    claimed[platform] = [e.entity_id for e in entities if e.unique_id is not None]
    # Imported here: the bridge is a separate custom component, and only
    # there when it is set up.
    from custom_components.config_bridge import claim_entities

    claim_entities(
        hass,
        DOMAIN,
        {entity_id: None for ids in claimed.values() for entity_id in ids},
    )
