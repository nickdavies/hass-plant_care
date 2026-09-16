"""What survives a restart.

Every failure in here is silent and slow: a killswitch quietly forgiven, a
day's light quietly zeroed, a dry plant quietly unflagged. None of them raise,
none of them show up in a log, and all of them take a real restart plus a wait
to find by hand — which is exactly why they are worth automating.

A restart here means what it means to the component: `hass.data` is gone and
every coordinator and controller is built again, while the storage file is not.
So each test rebuilds those objects over a **freshly loaded** `EventLog` — the
same code path `async_setup` runs — rather than poking at the ones already
running.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from custom_components.plant_care.dli import DliCoordinator
from custom_components.plant_care.light_control import LightController
from custom_components.plant_care.model import DEFAULT_POLICY
from custom_components.plant_care.moisture import MoistureCoordinator
from custom_components.plant_care.store import STORAGE_KEY, STORAGE_VERSION, EventLog

from .conftest import (
    DOMAIN,
    LUX_1,
    LUX_2,
    STUDY_SWITCH,
    at,
    start,
    tick,
)


async def reloaded_log(hass: HomeAssistant) -> EventLog:
    """The `EventLog` the next run would build: a new one over the same file."""
    log = EventLog(Store(hass, STORAGE_VERSION, STORAGE_KEY))
    await log.async_load()
    return log


async def restart_lights(hass: HomeAssistant) -> dict[str, LightController]:
    """Rebuild every light controller as start-up would."""
    data = hass.data[DOMAIN]
    for controller in data.light_controllers.values():
        controller.async_stop()

    log = await reloaded_log(hass)
    rebuilt: dict[str, LightController] = {}
    for fixture in data.config.lights:
        controller = LightController(hass, fixture, log)
        await controller.async_start()
        rebuilt[fixture.name] = controller
    return rebuilt


async def restart_dli(hass: HomeAssistant) -> dict[str, DliCoordinator]:
    data = hass.data[DOMAIN]
    for coordinator in data.dli_coordinators.values():
        coordinator.async_stop()

    log = await reloaded_log(hass)
    rebuilt: dict[str, DliCoordinator] = {}
    for plant in data.plants:
        if plant.dli is None or plant.lux is None:
            continue
        lux = data.config.lux(plant.lux)
        coordinator = DliCoordinator(
            hass, plant, lux, data.config.fixtures_for(plant), log
        )
        await coordinator.async_start()
        rebuilt[plant.name] = coordinator
    return rebuilt


class TestKillswitch:
    async def test_a_frozen_fixture_comes_back_frozen(
        self, hass: HomeAssistant, freezer: FrozenDateTimeFactory
    ) -> None:
        await start(hass, freezer, at(12, 0))
        await hass.data[DOMAIN].light_controllers["study_shelf"].async_set_killed(True)

        rebuilt = await restart_lights(hass)

        assert rebuilt["study_shelf"].killed
        assert not rebuilt["spare_shelf"].killed

    async def test_a_frozen_fixture_is_not_driven_while_coming_up(
        self, hass: HomeAssistant, freezer: FrozenDateTimeFactory
    ) -> None:
        """The reason the killswitch is restored from the store rather than
        from its own entity: entity platforms are set up *after* the
        controllers, so reading the entity would mean commanding a lamp
        somebody deliberately froze, once, before finding out.
        """
        await start(hass, freezer, at(12, 0))
        await hass.data[DOMAIN].light_controllers["study_shelf"].async_set_killed(True)
        hass.states.async_set(STUDY_SWITCH, "off")  # window is open; it "wants" on

        commands: list[str] = []

        def _record(event) -> None:
            if event.data.get("service_data", {}).get("entity_id") == STUDY_SWITCH:
                commands.append(event.data["service"])

        hass.bus.async_listen("call_service", _record)

        await restart_lights(hass)
        await hass.async_block_till_done()

        assert commands == []

    async def test_the_48h_backstop_measures_from_the_original_flip(
        self, hass: HomeAssistant, freezer: FrozenDateTimeFactory
    ) -> None:
        """The `last_changed` trap, pinned.

        `RestoreState` would bring the switch's value back but stamp it with the
        restore time, so this two-day-old killswitch would read as brand new and
        the backstop would silently restart its clock — for ever, if Home
        Assistant restarts more often than every two days, which it does.
        """
        await start(hass, freezer, at(12, 0))
        await hass.data[DOMAIN].light_controllers["study_shelf"].async_set_killed(True)

        freezer.tick(timedelta(hours=49))
        rebuilt = await restart_lights(hass)

        issue = rebuilt["study_shelf"].frozen_issue()
        assert issue is not None
        assert issue.value == pytest.approx(49.0, abs=0.1)

    async def test_releasing_it_is_forgotten_across_a_restart_too(
        self, hass: HomeAssistant, freezer: FrozenDateTimeFactory
    ) -> None:
        """A stamp that outlived its killswitch would report a fixture as frozen
        that nobody has frozen."""
        await start(hass, freezer, at(12, 0))
        controller = hass.data[DOMAIN].light_controllers["study_shelf"]
        await controller.async_set_killed(True)
        await controller.async_set_killed(False)

        freezer.tick(timedelta(hours=49))
        rebuilt = await restart_lights(hass)

        assert not rebuilt["study_shelf"].killed
        assert rebuilt["study_shelf"].frozen_issue() is None


class TestDliHistory:
    async def test_todays_accumulation_resumes_rather_than_restarting_at_zero(
        self, hass: HomeAssistant, freezer: FrozenDateTimeFactory
    ) -> None:
        """A day reset to zero at teatime looks like a severe shortfall, and
        would spend error budget on a failure that never happened."""
        hass.states.async_set(LUX_1, "10000")
        hass.states.async_set(LUX_2, "10000")
        await start(hass, freezer, at(10, 0))
        await tick(hass, freezer, minutes=60)

        banked = hass.data[DOMAIN].dli_coordinators["monstera"].today
        assert banked > 0.6  # an hour of real daylight, not a rounding artefact

        rebuilt = await restart_dli(hass)

        assert rebuilt["monstera"].today == pytest.approx(banked, abs=0.001)

    async def test_finished_days_come_back(
        self, hass: HomeAssistant, freezer: FrozenDateTimeFactory
    ) -> None:
        """Without these the burn rates have no window to look at, so a plant
        would read as healthy for a week after every restart."""
        hass.states.async_set(LUX_1, "10000")
        hass.states.async_set(LUX_2, "10000")
        await start(hass, freezer, at(23, 0))
        await tick(hass, freezer, minutes=120)  # over midnight

        before = hass.data[DOMAIN].dli_coordinators["monstera"].history()
        assert len(before) == 2

        rebuilt = await restart_dli(hass)

        assert rebuilt["monstera"].history() == before

    async def test_a_restart_on_a_new_day_does_not_inherit_yesterdays_total(
        self, hass: HomeAssistant, freezer: FrozenDateTimeFactory
    ) -> None:
        """Resuming reads *today's* entry, so a restart the next morning has to
        find nothing and start at zero rather than picking up the last number
        written."""
        hass.states.async_set(LUX_1, "10000")
        hass.states.async_set(LUX_2, "10000")
        await start(hass, freezer, at(10, 0))
        await tick(hass, freezer, minutes=60)

        hass.states.async_set(LUX_1, "0")
        hass.states.async_set(LUX_2, "0")
        freezer.move_to(at(2, 0, day=16))
        rebuilt = await restart_dli(hass)

        assert rebuilt["monstera"].today == 0.0
        assert rebuilt["monstera"].history()  # yesterday is still on record


class TestNeedsWaterLatch:
    """Uses the moisture document, which is where the probes live."""

    async def test_a_flagged_plant_comes_back_flagged(
        self, integration: HomeAssistant
    ) -> None:
        """It is a latch, not a measurement: the pot is still dry after a
        reboot, and nobody watered it in the meantime."""
        data = integration.data[DOMAIN]
        await data.event_log.async_set_needs_water("passionfruit", True)

        plant = next(p for p in data.plants if p.name == "passionfruit")
        log = await reloaded_log(integration)
        coordinator = MoistureCoordinator(
            integration, plant, plant.moisture, DEFAULT_POLICY, log
        )
        await coordinator.async_start()

        assert coordinator.needs_water

    async def test_a_watering_detected_before_a_restart_is_still_on_record(
        self, integration: HomeAssistant
    ) -> None:
        """Days-since-watered is the one number in the system that cannot be
        recomputed from anything the probe will say next."""
        data = integration.data[DOMAIN]
        watered = dt_util.utcnow() - timedelta(days=3)
        await data.event_log.async_record_watering("passionfruit", watered)

        log = await reloaded_log(integration)

        assert log.days_since_watered("passionfruit", dt_util.utcnow()) == 3.0
