"""Grow light control end to end.

The window arithmetic is pinned exhaustively in the unit tests. These cover what
only a running Home Assistant shows: that the switch is actually driven, that a
restart mid-window lands on the right side of an edge that already passed, that
the killswitch freezes rather than forces, and that the on-time outcome reaches
the feed.
"""

from __future__ import annotations

from typing import Any

from freezegun.api import FrozenDateTimeFactory
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant, callback

from .conftest import (
    DOMAIN,
    PRESENCE,
    SPARE_SWITCH,
    STUDY_KILLSWITCH,
    STUDY_ON_MINUTES,
    STUDY_SWITCH,
    at,
    start,
    tick,
)

OUTSTANDING = "sensor.plant_outstanding"


def record_switch_calls(hass: HomeAssistant) -> list[Any]:
    """Capture switch commands without swallowing the killswitch's own.

    `async_mock_service` would replace the whole `switch.turn_on` handler, and
    the killswitch is a switch too — mocking it would make the very entity under
    test inert. Listening to the service call event instead records everything
    and changes nothing.
    """
    recorded: list[Any] = []

    async def _listener(event) -> None:
        if event.data.get("domain") != "switch":
            return
        recorded.append(event)

    hass.bus.async_listen("call_service", _listener)
    return recorded


def targets(recorded: list[Any], entity_id: str) -> list[str]:
    return [
        event.data["service"]
        for event in recorded
        if event.data.get("service_data", {}).get("entity_id") == entity_id
    ]


def simulate_lamps(hass: HomeAssistant, *entity_ids: str) -> None:
    """Make the lamps obey, so on-time measures something real.

    These switches belong to zigbee2mqtt, so nothing in the test process backs
    them and a `turn_on` would otherwise vanish — leaving every lamp reading as
    permanently off and every on-time check reporting a deficit that is an
    artefact of the harness rather than a finding.
    """
    wanted = set(entity_ids)

    @callback
    def _obey(event) -> None:
        if event.data.get("domain") != "switch":
            return
        entity_id = event.data.get("service_data", {}).get("entity_id")
        if entity_id not in wanted:
            return
        hass.states.async_set(
            entity_id, STATE_ON if event.data["service"] == "turn_on" else STATE_OFF
        )

    hass.bus.async_listen("call_service", _obey)


class TestDrivingTheWindow:
    async def test_a_restart_mid_window_drives_the_lamp_on(
        self, hass: HomeAssistant, freezer: FrozenDateTimeFactory
    ) -> None:
        """The edge at 09:00 has already passed, and edges do not fire twice.

        Without this the lamp would sit off until tomorrow morning — the failure
        the hand-written version needed a start trigger and a periodic re-check
        to avoid.
        """
        recorded = record_switch_calls(hass)
        await start(hass, freezer, at(12, 0))

        assert targets(recorded, STUDY_SWITCH) == ["turn_on"]

    async def test_a_restart_outside_the_window_drives_the_lamp_off(
        self, hass: HomeAssistant, freezer: FrozenDateTimeFactory
    ) -> None:
        hass.states.async_set(STUDY_SWITCH, STATE_ON)
        recorded = record_switch_calls(hass)
        await start(hass, freezer, at(22, 0))

        assert targets(recorded, STUDY_SWITCH) == ["turn_off"]

    async def test_a_lamp_already_correct_is_left_alone(
        self, hass: HomeAssistant, freezer: FrozenDateTimeFactory
    ) -> None:
        """Re-commanding every minute would fill the logbook and wake the device
        for nothing."""
        hass.states.async_set(STUDY_SWITCH, STATE_ON)
        recorded = record_switch_calls(hass)
        await start(hass, freezer, at(12, 0))
        await tick(hass, freezer, minutes=5)

        assert targets(recorded, STUDY_SWITCH) == []

    async def test_the_window_closes_on_its_own_tick(
        self, hass: HomeAssistant, freezer: FrozenDateTimeFactory
    ) -> None:
        hass.states.async_set(STUDY_SWITCH, STATE_ON)
        await start(hass, freezer, at(18, 55))
        recorded = record_switch_calls(hass)

        await tick(hass, freezer, minutes=6)

        assert targets(recorded, STUDY_SWITCH) == ["turn_off"]

    async def test_a_fixed_window_ignores_presence_entirely(
        self, hass: HomeAssistant, freezer: FrozenDateTimeFactory
    ) -> None:
        recorded = record_switch_calls(hass)
        await start(hass, freezer, at(18, 0))
        hass.states.async_set(PRESENCE, "asleep")
        await hass.async_block_till_done()

        # On at start-up, and the spare room's lamp never hears about anyone's
        # bedtime — its window runs to 19:00 whatever the study is doing.
        assert targets(recorded, SPARE_SWITCH) == ["turn_on"]


class TestPresenceGating:
    async def test_the_early_stretch_waits_for_them_to_be_up(
        self, hass: HomeAssistant, freezer: FrozenDateTimeFactory
    ) -> None:
        hass.states.async_set(PRESENCE, "asleep")
        recorded = record_switch_calls(hass)
        await start(hass, freezer, at(7, 0))

        assert targets(recorded, STUDY_SWITCH) == []

    async def test_being_up_early_opens_it_early(
        self, hass: HomeAssistant, freezer: FrozenDateTimeFactory
    ) -> None:
        hass.states.async_set(PRESENCE, "asleep")
        recorded = record_switch_calls(hass)
        await start(hass, freezer, at(7, 0))

        hass.states.async_set(PRESENCE, "awake")
        await hass.async_block_till_done()

        assert targets(recorded, STUDY_SWITCH) == ["turn_on"]

    async def test_going_to_bed_in_the_tail_closes_it_without_waiting_for_a_tick(
        self, hass: HomeAssistant, freezer: FrozenDateTimeFactory
    ) -> None:
        """A minute of lamp in a room somebody just went to sleep in is exactly
        the annoyance the window exists to avoid."""
        hass.states.async_set(STUDY_SWITCH, STATE_ON)
        await start(hass, freezer, at(18, 0))
        recorded = record_switch_calls(hass)

        hass.states.async_set(PRESENCE, "asleep")
        await hass.async_block_till_done()

        assert targets(recorded, STUDY_SWITCH) == ["turn_off"]

    async def test_one_person_up_in_a_group_keeps_it_on(
        self, hass: HomeAssistant, freezer: FrozenDateTimeFactory
    ) -> None:
        """`sensor.group_presence_*` is a comma-joined set."""
        hass.states.async_set(STUDY_SWITCH, STATE_ON)
        await start(hass, freezer, at(18, 0))
        recorded = record_switch_calls(hass)

        hass.states.async_set(PRESENCE, "asleep,awake")
        await hass.async_block_till_done()

        assert targets(recorded, STUDY_SWITCH) == []

    async def test_a_broken_presence_sensor_does_not_hold_the_lamp_off(
        self, hass: HomeAssistant, freezer: FrozenDateTimeFactory
    ) -> None:
        """The failure directions are not symmetric: an unavailable sensor must
        not starve a plant for as long as it stays broken."""
        hass.states.async_set(PRESENCE, "asleep")
        recorded = record_switch_calls(hass)
        await start(hass, freezer, at(7, 0))

        hass.states.async_set(PRESENCE, "unavailable")
        await hass.async_block_till_done()

        assert targets(recorded, STUDY_SWITCH) == ["turn_on"]


class TestKillswitch:
    async def test_it_exists_per_fixture_and_names_its_room(
        self, hass: HomeAssistant, freezer: FrozenDateTimeFactory
    ) -> None:
        await start(hass, freezer, at(12, 0))

        state = hass.states.get(STUDY_KILLSWITCH)
        assert state is not None
        assert state.state == STATE_OFF
        assert state.attributes["room"] == "nick_study"
        assert state.attributes["switch"] == STUDY_SWITCH

    async def test_turning_it_on_stamps_the_store_rather_than_leaning_on_last_changed(
        self, hass: HomeAssistant, freezer: FrozenDateTimeFactory
    ) -> None:
        await start(hass, freezer, at(12, 0))

        await hass.services.async_call(
            "switch", "turn_on", {"entity_id": STUDY_KILLSWITCH}, blocking=True
        )
        await hass.async_block_till_done()

        assert hass.states.get(STUDY_KILLSWITCH).state == STATE_ON
        assert hass.data[DOMAIN].event_log.killswitch_since("study_shelf") is not None

        await hass.services.async_call(
            "switch", "turn_off", {"entity_id": STUDY_KILLSWITCH}, blocking=True
        )
        await hass.async_block_till_done()

        assert hass.data[DOMAIN].event_log.killswitch_since("study_shelf") is None

    async def test_a_frozen_fixture_is_left_exactly_where_it_was(
        self, hass: HomeAssistant, freezer: FrozenDateTimeFactory
    ) -> None:
        """Freeze, not force. The lamp is on and the window is about to close;
        a frozen controller must leave it on, because whoever flipped the
        killswitch is the one in control now.
        """
        hass.states.async_set(STUDY_SWITCH, STATE_ON)
        await start(hass, freezer, at(18, 0))

        controller = hass.data[DOMAIN].light_controllers["study_shelf"]
        await controller.async_set_killed(True)
        recorded = record_switch_calls(hass)

        await tick(hass, freezer, minutes=120)

        assert targets(recorded, STUDY_SWITCH) == []

    async def test_it_freezes_a_lamp_that_is_off_just_as_hard(
        self, hass: HomeAssistant, freezer: FrozenDateTimeFactory
    ) -> None:
        """The other direction, and the dangerous one: frozen off starves the
        plants under it. Nothing here notices, which is why the feed watches
        on-time instead."""
        await start(hass, freezer, at(5, 0))

        controller = hass.data[DOMAIN].light_controllers["study_shelf"]
        await controller.async_set_killed(True)
        recorded = record_switch_calls(hass)

        await tick(hass, freezer, minutes=7 * 60)

        assert targets(recorded, STUDY_SWITCH) == []

    async def test_releasing_it_takes_control_back_immediately(
        self, hass: HomeAssistant, freezer: FrozenDateTimeFactory
    ) -> None:
        await start(hass, freezer, at(12, 0))
        controller = hass.data[DOMAIN].light_controllers["study_shelf"]
        await controller.async_set_killed(True)
        hass.states.async_set(STUDY_SWITCH, STATE_OFF)
        recorded = record_switch_calls(hass)

        await controller.async_set_killed(False)
        await hass.async_block_till_done()

        assert targets(recorded, STUDY_SWITCH) == ["turn_on"]

    async def test_a_killswitch_left_on_for_two_days_becomes_an_item(
        self, hass: HomeAssistant, freezer: FrozenDateTimeFactory
    ) -> None:
        """The backstop for the real failure mode, which is forgetting it.

        Note it fires regardless of whether the plants underneath look fine yet
        — a deliberate temporary intervention that has lasted two days is worth
        saying on its own.
        """
        await start(hass, freezer, at(12, 0))
        controller = hass.data[DOMAIN].light_controllers["study_shelf"]
        await controller.async_set_killed(True)

        items = hass.states.get(OUTSTANDING).attributes["items"]
        assert [item for item in items if item["kind"] == "automation_frozen"] == []

        await tick(hass, freezer, minutes=49 * 60)

        frozen = [
            item
            for item in hass.states.get(OUTSTANDING).attributes["items"]
            if item["kind"] == "automation_frozen"
        ]
        assert len(frozen) == 1
        assert "study_shelf" in frozen[0]["label"]
        # Not attached to a plant: hanging it off one of the three under this
        # fixture would hide it from anyone looking at the other two.
        assert frozen[0]["plant"] is None


class TestOnTimeOutcome:
    async def test_the_sensor_reports_what_happened_against_what_was_allowed(
        self, hass: HomeAssistant, freezer: FrozenDateTimeFactory
    ) -> None:
        hass.states.async_set(STUDY_SWITCH, STATE_ON)
        await start(hass, freezer, at(0, 0))

        await tick(hass, freezer, minutes=12 * 60)

        state = hass.states.get(STUDY_ON_MINUTES)
        assert state is not None
        # 09:00-12:00 is guaranteed; 06:00-12:00 is the most it could open.
        assert state.attributes["guaranteed_minutes"] == 180
        assert state.attributes["possible_minutes"] == 360

    async def test_a_dead_lamp_reaches_the_feed_for_the_plants_under_it(
        self, hass: HomeAssistant, freezer: FrozenDateTimeFactory
    ) -> None:
        """The outlet says off all morning. Nothing here knows why — a dead
        bulb, a dropped device and a frozen automation all read the same, which
        is the point of checking the outcome instead of the boolean.
        """
        simulate_lamps(hass, SPARE_SWITCH)  # the spare lamp works; the study's does not
        await start(hass, freezer, at(0, 0))

        controller = hass.data[DOMAIN].light_controllers["study_shelf"]
        await controller.async_set_killed(True)  # so nothing tries to fix it

        await tick(hass, freezer, minutes=12 * 60)

        items = [
            item
            for item in hass.states.get(OUTSTANDING).attributes["items"]
            if item["kind"] == "light_hours_deviation"
        ]
        assert {item["plant"] for item in items} == {"ficus_alii"}
        assert items[0]["value"] == 180
        assert items[0]["fixture"] == "study_shelf"

    async def test_a_measured_plant_is_not_told_twice(
        self, hass: HomeAssistant, freezer: FrozenDateTimeFactory
    ) -> None:
        """The monstera sits under the same dead lamp, but it has a lux fixture
        — so it gets the DLI signal instead. Reporting both would be two items
        for one problem."""
        await start(hass, freezer, at(0, 0))
        await hass.data[DOMAIN].light_controllers["study_shelf"].async_set_killed(True)

        await tick(hass, freezer, minutes=12 * 60)

        kinds = {
            item["kind"]
            for item in hass.states.get(OUTSTANDING).attributes["items"]
            if item["plant"] == "monstera"
        }
        assert "light_hours_deviation" not in kinds

    async def test_a_lamp_running_its_window_raises_nothing(
        self, hass: HomeAssistant, freezer: FrozenDateTimeFactory
    ) -> None:
        simulate_lamps(hass, STUDY_SWITCH, SPARE_SWITCH)
        await start(hass, freezer, at(9, 0))

        await tick(hass, freezer, minutes=3 * 60)

        kinds = {
            item["kind"] for item in hass.states.get(OUTSTANDING).attributes["items"]
        }
        assert "light_hours_deviation" not in kinds

    async def test_a_lamp_left_on_all_night_is_caught_the_other_way(
        self, hass: HomeAssistant, freezer: FrozenDateTimeFactory
    ) -> None:
        """Twelve hours by noon against a window that could allow six. The
        plants under it are getting a photoperiod nobody chose, and the heat
        with it — a failure a "did the lamp run" check would call a perfect day.
        """
        hass.states.async_set(STUDY_SWITCH, STATE_ON)
        await start(hass, freezer, at(0, 0))
        await hass.data[DOMAIN].light_controllers["study_shelf"].async_set_killed(True)

        await tick(hass, freezer, minutes=12 * 60)

        items = [
            item
            for item in hass.states.get(OUTSTANDING).attributes["items"]
            if item["kind"] == "light_hours_deviation" and item["plant"] == "ficus_alii"
        ]
        assert len(items) == 1
        assert "running long" in items[0]["label"]
