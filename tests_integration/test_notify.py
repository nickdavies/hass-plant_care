"""New feed items reach a phone, once each.

The feed is a sensor, and nobody watches a sensor. These pin the contract of
the one thing that leaves the dashboard: each item is pushed the moment it
appears, never again while it stays, again if it comes back, and not at all
because Home Assistant restarted.
"""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.loader import DATA_CUSTOM_COMPONENTS
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import async_mock_service

from custom_components.plant_care.notifier import FeedNotifier, item_key, item_line
from custom_components.plant_care.store import STORAGE_KEY, STORAGE_VERSION, EventLog

from .conftest import (
    DOMAIN,
    MONSTERA_RAW,
    PASSIONFRUIT_BATTERY,
    PASSIONFRUIT_RAW,
    TEST_CONFIG,
    _ensure_custom_components_path,
    probe_reports,
)

NOTIFY_CONFIG: dict[str, Any] = {
    DOMAIN: {**TEST_CONFIG[DOMAIN], "notify": "notify.nick"}
}


async def setup_notifying(hass: HomeAssistant) -> None:
    hass.states.async_set(PASSIONFRUIT_RAW, "60.0")
    hass.states.async_set(PASSIONFRUIT_BATTERY, "85")
    hass.states.async_set(MONSTERA_RAW, "50.0")
    hass.data.pop(DATA_CUSTOM_COMPONENTS, None)
    _ensure_custom_components_path()
    assert await async_setup_component(hass, DOMAIN, NOTIFY_CONFIG), "setup failed"
    await hass.async_block_till_done()


@pytest.fixture
async def notified(hass: HomeAssistant):
    """The component with `notify.nick` mocked and recording."""
    calls = async_mock_service(hass, "notify", "nick")
    await setup_notifying(hass)
    return hass, calls


async def battery_reads(hass: HomeAssistant, pct: int, moisture: float) -> None:
    """Battery is read live on the next moisture report, not tracked."""
    hass.states.async_set(PASSIONFRUIT_BATTERY, str(pct))
    await probe_reports(hass, PASSIONFRUIT_RAW, moisture)


class TestPushOnce:
    async def test_nothing_outstanding_sends_nothing(self, notified) -> None:
        _, calls = notified
        assert calls == []

    async def test_a_new_fault_is_pushed_the_moment_it_appears(self, notified) -> None:
        hass, calls = notified
        await battery_reads(hass, 10, 60.5)

        assert len(calls) == 1
        assert calls[0].data["title"] == "Plants"
        assert calls[0].data["message"].startswith("Passionfruit: Probe battery low")

    async def test_the_same_fault_is_not_pushed_again(self, notified) -> None:
        hass, calls = notified
        await battery_reads(hass, 10, 60.5)
        await battery_reads(hass, 10, 60.7)
        await battery_reads(hass, 9, 60.6)
        assert len(calls) == 1

    async def test_clearing_is_silent_and_a_return_is_pushed_again(
        self, notified
    ) -> None:
        hass, calls = notified
        await battery_reads(hass, 10, 60.5)
        await battery_reads(hass, 85, 60.6)
        assert len(calls) == 1

        await battery_reads(hass, 10, 60.7)
        assert len(calls) == 2

    async def test_a_restart_does_not_re_push_what_is_still_outstanding(
        self, notified
    ) -> None:
        """What `async_setup` would build next time: a notifier over a freshly
        loaded log, with the fault still present."""
        hass, calls = notified
        await battery_reads(hass, 10, 60.5)
        assert len(calls) == 1

        log = EventLog(Store(hass, STORAGE_VERSION, STORAGE_KEY))
        await log.async_load()
        rebuilt = FeedNotifier(
            hass, dataclasses.replace(hass.data[DOMAIN], event_log=log), "notify.nick"
        )
        await rebuilt.async_start()
        await hass.async_block_till_done()

        assert len(calls) == 1


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


class TestShape:
    def test_a_key_is_what_the_item_is_about(self) -> None:
        care = {"plant": "monstera", "kind": "care", "task": "feed", "days": 3.0}
        assert item_key(care) == "monstera.care.feed"
        assert item_key({**care, "days": 4.0}) == item_key(care)
        assert item_key({"plant": None, "kind": "killswitch_forgotten"}) == (
            "None.killswitch_forgotten."
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
