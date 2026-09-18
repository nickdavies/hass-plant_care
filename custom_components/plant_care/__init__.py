"""Plant care: moisture monitoring, care tasks, and one outstanding feed.

Configured from YAML rather than a config flow, so the configuration lives in
git — a `plant_care:` block in `configuration.yaml` or a package. Every check
on it, cross-references included, runs inside `CONFIG_SCHEMA`, so
`check_config` refuses a bad config before Home Assistant ever starts with it.

**This module imports no Home Assistant at runtime.** Importing
`custom_components.plant_care.model` runs this file first, so an unguarded HA
import here would drag the framework into every unit test and force them to mock
it. The framework imports are therefore either `TYPE_CHECKING`-only (so the type
hints stay real) or done inside `async_setup`, which never runs outside Home
Assistant. `tests/test_no_ha_imports.py` enforces it.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import voluptuous as vol

from .const import DOMAIN
from .model import DEFAULT_POLICY, Plant, PlantCareConfig, Policy, parse, schema
from .store import STORAGE_KEY, STORAGE_VERSION, EventLog

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.typing import ConfigType

    from .dli import DliCoordinator
    from .light_control import LightController
    from .moisture import MoistureCoordinator

_LOGGER = logging.getLogger(__name__)

# Plain strings rather than `homeassistant.const.Platform`, which would be a
# runtime HA import for four values that are stable identifiers anyway.
PLATFORMS: tuple[str, ...] = ("sensor", "binary_sensor", "button", "switch")


def _validated(data: Mapping[str, Any]) -> Mapping[str, Any]:
    """Run the cross-reference checks as part of `CONFIG_SCHEMA`.

    The shape alone is not the contract: a plant naming a probe model that
    does not exist is exactly as wrong as a missing field, and `check_config`
    only sees what the schema raises. `parse` raises `InvalidPlantConfig`,
    which is a `vol.Invalid`, so it reports like any other config error.

    Returns the data unchanged rather than the parsed model: `async_setup`
    parses again, which is cheap, and the validated config stays a plain
    mapping the way Home Assistant expects.
    """
    parse(data)
    return data


# Strict throughout — unknown keys are rejected everywhere. A typo is an
# error, not a silently ignored field, and running on a partial understanding
# of the config is worse than refusing to start.
CONFIG_SCHEMA = vol.Schema(
    {DOMAIN: vol.All(schema(), _validated)}, extra=vol.ALLOW_EXTRA
)


@dataclass(frozen=True)
class PlantCareData:
    """What the platforms need, assembled once at setup.

    Held in `hass.data[DOMAIN]` rather than passed through discovery info,
    because discovery info has to be JSON-serialisable and these are real typed
    objects — which is the entire point of having parsed them.
    """

    config: PlantCareConfig
    """The whole document, so a platform can resolve the fixtures a plant names."""
    policy: Policy
    event_log: EventLog
    coordinators: Mapping[str, MoistureCoordinator]
    """Keyed by plant name. Only plants with a probe have one."""
    light_controllers: Mapping[str, LightController]
    """Keyed by *fixture* name — a fixture is shared, so it is not per plant."""
    dli_coordinators: Mapping[str, DliCoordinator]
    """Keyed by plant name. Only plants with both a lux fixture and an
    objective have one; the pair is enforced at parse time."""

    @property
    def plants(self) -> tuple[Plant, ...]:
        return self.config.plants


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    # Imported here rather than at module scope: see the module docstring.
    from homeassistant.helpers import discovery
    from homeassistant.helpers.storage import Store

    from .dashboards import PlantsDashboard
    from .dli import DliCoordinator
    from .light_control import LightController
    from .moisture import MoistureCoordinator

    domain_config: Mapping[str, Any] | None = config.get(DOMAIN)
    if domain_config is None:
        return True

    # Anything raised here is fatal on purpose. A plant care system that comes
    # up with half its plants missing looks like it is working, and the failure
    # shows up as a plant nobody watered.
    parsed = parse(domain_config)
    plants = parsed.plants

    for fixture in parsed.unreferenced_lights():
        _LOGGER.warning(
            "plant_care: light fixture '%s' has no plants under it; its on-time "
            "checks are disabled",
            fixture.name,
        )

    event_log = EventLog(Store(hass, STORAGE_VERSION, STORAGE_KEY))
    await event_log.async_load()

    # Coordinators are started here rather than by an entity, so a plant that is
    # still calibrating — and therefore has no needs-water entity — is monitored
    # just the same. The same reasoning covers the light controllers: a fixture
    # must run its window whether or not its killswitch entity exists yet.
    coordinators: dict[str, MoistureCoordinator] = {}
    for plant in plants:
        if plant.moisture is None:
            continue
        coordinator = MoistureCoordinator(
            hass, plant, plant.moisture, DEFAULT_POLICY, event_log
        )
        await coordinator.async_start()
        coordinators[plant.name] = coordinator

    light_controllers: dict[str, LightController] = {}
    for fixture in parsed.lights:
        controller = LightController(hass, fixture, event_log)
        await controller.async_start()
        light_controllers[fixture.name] = controller

    dli_coordinators: dict[str, DliCoordinator] = {}
    for plant in plants:
        if plant.dli is None or plant.lux is None:
            continue
        lux = parsed.lux(plant.lux)
        assert lux is not None  # cross-checked in `parse`
        dli = DliCoordinator(hass, plant, lux, parsed.fixtures_for(plant), event_log)
        await dli.async_start()
        dli_coordinators[plant.name] = dli

    hass.data[DOMAIN] = PlantCareData(
        config=parsed,
        policy=DEFAULT_POLICY,
        event_log=event_log,
        coordinators=coordinators,
        light_controllers=light_controllers,
        dli_coordinators=dli_coordinators,
    )

    _LOGGER.debug(
        "plant_care: loaded %d plants (%d with a probe, %d with a light budget) "
        "and %d light fixtures",
        len(plants),
        len(coordinators),
        len(dli_coordinators),
        len(light_controllers),
    )

    for platform in PLATFORMS:
        hass.async_create_task(
            discovery.async_load_platform(hass, platform, DOMAIN, {}, config)
        )

    PlantsDashboard(parsed).add_to_hass(hass)

    return True
