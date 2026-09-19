"""New feed items reach a phone, once each — the right phone.

The feed is a sensor, and nobody watches a sensor. These pin the contract of
the one thing that leaves the dashboard: each item is pushed the moment it
appears, never again while it stays, again if it comes back, not at all
because Home Assistant restarted — and to its owner's action, with a fault
belonging to no plant going to `system_notify`.
"""

from __future__ import annotations

import dataclasses
from datetime import timedelta

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.loader import DATA_CUSTOM_COMPONENTS
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import (
    async_fire_time_changed,
    async_mock_service,
)

from custom_components.plant_care.model import parse
from custom_components.plant_care.notifier import (
    FeedNotifier,
    item_key,
    item_line,
    route,
)
from custom_components.plant_care.store import STORAGE_KEY, STORAGE_VERSION, EventLog

from .conftest import (
    DOMAIN,
    MONSTERA_PEST_DONE,
    MONSTERA_RAW,
    PASSIONFRUIT_BATTERY,
    PASSIONFRUIT_RAW,
    POT_WATER_DONE,
    TEST_CONFIG,
    _ensure_custom_components_path,
    at,
    mock_phones,
    press,
    probe_reports,
    start,
    tick,
)


async def setup_notifying(hass: HomeAssistant) -> None:
    hass.states.async_set(PASSIONFRUIT_RAW, "60.0")
    hass.states.async_set(PASSIONFRUIT_BATTERY, "85")
    hass.states.async_set(MONSTERA_RAW, "50.0")
    hass.data.pop(DATA_CUSTOM_COMPONENTS, None)
    _ensure_custom_components_path()
    assert await async_setup_component(hass, DOMAIN, TEST_CONFIG), "setup failed"
    await hass.async_block_till_done()


@pytest.fixture
async def notified(hass: HomeAssistant):
    """The component with every phone mocked and recording."""
    phones = mock_phones(hass)
    await setup_notifying(hass)
    return hass, phones


async def battery_reads(hass: HomeAssistant, pct: int, moisture: float) -> None:
    """Battery is read live on the next moisture report, not tracked."""
    hass.states.async_set(PASSIONFRUIT_BATTERY, str(pct))
    await probe_reports(hass, PASSIONFRUIT_RAW, moisture)


async def days_pass(hass: HomeAssistant, freezer: FrozenDateTimeFactory, days: int):
    """Days with no probe report — so a silent-probe item appears alongside
    whatever became overdue. Tests below count lines about their subject."""
    freezer.tick(timedelta(days=days))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


def mentions(calls, text: str) -> int:
    return sum(1 for call in calls if text in call.data["message"])


class TestPushOnce:
    async def test_nothing_outstanding_sends_nothing(self, notified) -> None:
        _, phones = notified
        assert all(calls == [] for calls in phones.values())

    async def test_a_new_fault_is_pushed_the_moment_it_appears(self, notified) -> None:
        hass, phones = notified
        await battery_reads(hass, 10, 60.5)

        calls = phones["nick"]
        assert len(calls) == 1
        assert calls[0].data["title"] == "Plants"
        assert calls[0].data["message"].startswith("Passionfruit: Probe battery low")

    async def test_the_same_fault_is_not_pushed_again(self, notified) -> None:
        hass, phones = notified
        await battery_reads(hass, 10, 60.5)
        await battery_reads(hass, 10, 60.7)
        await battery_reads(hass, 9, 60.6)
        assert len(phones["nick"]) == 1

    async def test_clearing_is_silent_and_a_return_is_pushed_again(
        self, notified
    ) -> None:
        hass, phones = notified
        await battery_reads(hass, 10, 60.5)
        await battery_reads(hass, 85, 60.6)
        assert len(phones["nick"]) == 1

        await battery_reads(hass, 10, 60.7)
        assert len(phones["nick"]) == 2

    async def test_a_restart_does_not_re_push_what_is_still_outstanding(
        self, notified
    ) -> None:
        """What `async_setup` would build next time: a notifier over a freshly
        loaded log, with the fault still present."""
        hass, phones = notified
        await battery_reads(hass, 10, 60.5)
        assert len(phones["nick"]) == 1

        log = EventLog(Store(hass, STORAGE_VERSION, STORAGE_KEY))
        await log.async_load()
        rebuilt = FeedNotifier(
            hass, dataclasses.replace(hass.data[DOMAIN], event_log=log)
        )
        await rebuilt.async_start()
        await hass.async_block_till_done()

        assert len(phones["nick"]) == 1


class TestRouting:
    async def test_a_plants_item_reaches_its_owner_and_nobody_else(
        self, notified
    ) -> None:
        hass, phones = notified
        await battery_reads(hass, 10, 60.5)

        assert len(phones["nick"]) == 1
        assert phones["britta"] == []
        assert phones["phones"] == []

    async def test_a_shared_plants_item_reaches_the_groups_one_action(
        self, notified, freezer: FrozenDateTimeFactory
    ) -> None:
        """Monstera belongs to `primary`: one push to `notify.phones`, not one
        per member."""
        hass, phones = notified
        await press(hass, MONSTERA_PEST_DONE)
        await days_pass(hass, freezer, 8)

        assert mentions(phones["phones"], "Monstera: Pest check") == 1
        assert mentions(phones["nick"], "Monstera") == 0
        assert mentions(phones["britta"], "Monstera") == 0

    async def test_each_owner_gets_only_their_own_lines(
        self, notified, freezer: FrozenDateTimeFactory
    ) -> None:
        hass, phones = notified
        await press(hass, POT_WATER_DONE)
        await battery_reads(hass, 10, 60.5)
        await days_pass(hass, freezer, 5)

        assert "Passionfruit" in phones["nick"][0].data["message"]
        assert "Front step pot" not in phones["nick"][0].data["message"]
        assert "Front step pot: Water" in phones["britta"][0].data["message"]
        assert "Passionfruit" not in phones["britta"][0].data["message"]

    async def test_a_fault_belonging_to_no_plant_goes_to_system_notify(
        self, hass: HomeAssistant, freezer: FrozenDateTimeFactory
    ) -> None:
        """A killswitch left frozen for two days is nobody's plant."""
        phones = mock_phones(hass)
        await start(hass, freezer, at(12, 0))
        controller = hass.data[DOMAIN].light_controllers["study_shelf"]
        await controller.async_set_killed(True)
        # Whatever the lamps did in the meantime went to their owners; only
        # what arrives after 48h is under test.
        await tick(hass, freezer, minutes=49 * 60)

        frozen = [
            call
            for call in phones["phones"]
            if "automation frozen" in call.data["message"]
        ]
        assert len(frozen) == 1
        assert "study_shelf" in frozen[0].data["message"]

    async def test_a_plant_handed_to_a_new_owner_is_told_to_them(
        self, notified
    ) -> None:
        """The old owner already heard; the new one has not."""
        hass, phones = notified
        await battery_reads(hass, 10, 60.5)
        assert len(phones["nick"]) == 1

        document = {**TEST_CONFIG[DOMAIN]}
        document["plants"] = [
            {**plant, "owner": "britta"} if plant["name"] == "passionfruit" else plant
            for plant in document["plants"]
        ]
        data = hass.data[DOMAIN]
        rebuilt = FeedNotifier(hass, dataclasses.replace(data, config=parse(document)))
        await rebuilt.async_start()
        await hass.async_block_till_done()

        assert len(phones["britta"]) == 1
        assert "Passionfruit" in phones["britta"][0].data["message"]
        assert len(phones["nick"]) == 1


class TestMissingAction:
    async def test_a_missing_action_is_retried_once_it_exists(
        self, hass: HomeAssistant, caplog
    ) -> None:
        """A phone that is not set up yet must not eat the fault."""
        await setup_notifying(hass)
        await battery_reads(hass, 10, 60.5)
        assert "notify.nick does not exist" in caplog.text

        calls = async_mock_service(hass, "notify", "nick")
        await battery_reads(hass, 10, 60.6)
        assert len(calls) == 1

    async def test_one_missing_phone_does_not_silence_the_others(
        self, hass: HomeAssistant, freezer: FrozenDateTimeFactory, caplog
    ) -> None:
        """The rollback is per action: what Nick's phone received stays
        announced while Britta's is retried."""
        nick = async_mock_service(hass, "notify", "nick")
        await setup_notifying(hass)
        await press(hass, POT_WATER_DONE)
        await battery_reads(hass, 10, 60.5)
        await days_pass(hass, freezer, 5)

        assert mentions(nick, "Probe battery low") == 1
        assert "notify.britta does not exist" in caplog.text

        britta = async_mock_service(hass, "notify", "britta")
        await days_pass(hass, freezer, 1)

        assert mentions(britta, "Front step pot") == 1
        assert mentions(nick, "Front step pot") == 0
        assert mentions(nick, "Probe battery low") == 1


class TestStoredShape:
    async def test_the_flat_list_from_before_routing_is_pushed_once_more(
        self, hass: HomeAssistant
    ) -> None:
        """The old set cannot say which phone it went to, so it is discarded."""
        await Store(hass, STORAGE_VERSION, STORAGE_KEY).async_save(
            {"announced": ["passionfruit.battery_low."]}
        )
        phones = mock_phones(hass)
        hass.states.async_set(PASSIONFRUIT_RAW, "60.0")
        hass.states.async_set(PASSIONFRUIT_BATTERY, "10")
        hass.states.async_set(MONSTERA_RAW, "50.0")
        hass.data.pop(DATA_CUSTOM_COMPONENTS, None)
        _ensure_custom_components_path()
        assert await async_setup_component(hass, DOMAIN, TEST_CONFIG)
        await hass.async_block_till_done()
        await battery_reads(hass, 10, 60.5)

        assert len(phones["nick"]) == 1


class TestShape:
    def test_a_key_is_what_the_item_is_about(self) -> None:
        care = {"plant": "monstera", "kind": "care", "task": "feed", "days": 3.0}
        assert item_key(care) == "monstera.care.feed"
        assert item_key({**care, "days": 4.0}) == item_key(care)

    def test_a_system_fault_is_keyed_by_its_fixture(self) -> None:
        """Two frozen lamps are two items. Without the fixture in the key the
        second would never be pushed."""
        study = {"plant": None, "kind": "automation_frozen", "fixture": "study"}
        spare = {**study, "fixture": "spare"}
        assert item_key(study) == "None.automation_frozen.study"
        assert item_key(study) != item_key(spare)

    def test_an_item_routes_to_its_owner_or_to_system_notify(self) -> None:
        config = parse(TEST_CONFIG[DOMAIN])
        assert route(config, {"owner": "nick", "kind": "care"}) == "notify.nick"
        assert route(config, {"owner": "primary", "kind": "care"}) == "notify.phones"
        assert route(config, {"owner": None, "kind": "automation_frozen"}) == (
            "notify.phones"
        )

    def test_a_care_line_says_how_late(self) -> None:
        line = item_line(
            {
                "plant": "monstera",
                "name": "Monstera",
                "kind": "care",
                "label": "Feed",
                "days": 23.4,
                "every": 21,
            }
        )
        assert line == "Monstera: Feed (23 days, every 21)"

    def test_a_fault_line_carries_the_remedy(self) -> None:
        line = item_line(
            {
                "plant": "passionfruit",
                "name": "Passionfruit",
                "kind": "battery_low",
                "label": "Probe battery low",
                "detail": "Replace it.",
            }
        )
        assert line == "Passionfruit: Probe battery low — Replace it."
