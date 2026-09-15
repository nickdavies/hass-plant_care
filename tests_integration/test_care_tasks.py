"""Care tasks end to end: entities exist, a press lands, the feed reacts."""

from __future__ import annotations

from homeassistant.core import HomeAssistant

from .conftest import (
    MONSTERA_PEST_DONE,
    MONSTERA_PEST_DUE,
    OUTSTANDING,
    PASSIONFRUIT_ATTENTION,
    PASSIONFRUIT_FEED_DAYS,
    PASSIONFRUIT_FEED_DONE,
    PASSIONFRUIT_FEED_DUE,
    POT_WATER_DONE,
    press,
)


class TestEntitiesExist:
    async def test_every_care_task_gets_its_three_entities(
        self, integration: HomeAssistant
    ) -> None:
        for entity_id in (
            PASSIONFRUIT_FEED_DAYS,
            PASSIONFRUIT_FEED_DUE,
            PASSIONFRUIT_FEED_DONE,
        ):
            assert integration.states.get(entity_id) is not None, f"{entity_id} missing"

    async def test_a_sensorless_plant_still_gets_its_care_entities(
        self, integration: HomeAssistant
    ) -> None:
        """An outdoor pot with no probe is a first-class case, not a degraded
        one — and `water` is a legitimate task for it, because nothing detects
        watering without a probe."""
        assert integration.states.get(POT_WATER_DONE) is not None

    async def test_the_feed_exists_once_not_per_plant(
        self, integration: HomeAssistant
    ) -> None:
        assert integration.states.get(OUTSTANDING) is not None
        assert integration.states.get("sensor.plant_passionfruit_outstanding") is None

    async def test_no_entity_is_created_for_a_task_a_plant_does_not_have(
        self, integration: HomeAssistant
    ) -> None:
        assert integration.states.get("button.plant_monstera_feed_done") is None


class TestNeverDone:
    """Adding a plant must not produce an instant backlog."""

    async def test_days_since_is_unknown_not_zero(
        self, integration: HomeAssistant
    ) -> None:
        state = integration.states.get(PASSIONFRUIT_FEED_DAYS)
        assert state.state == "unknown"

    async def test_nothing_is_due(self, integration: HomeAssistant) -> None:
        assert integration.states.get(PASSIONFRUIT_FEED_DUE).state == "off"
        assert integration.states.get(MONSTERA_PEST_DUE).state == "off"

    async def test_the_feed_is_empty(self, integration: HomeAssistant) -> None:
        state = integration.states.get(OUTSTANDING)
        assert state.state == "0"
        assert state.attributes["items"] == []


class TestMarkingDone:
    async def test_a_press_sets_days_since_to_zero(
        self, integration: HomeAssistant
    ) -> None:
        await press(integration, PASSIONFRUIT_FEED_DONE)
        assert integration.states.get(PASSIONFRUIT_FEED_DAYS).state == "0.0"

    async def test_a_press_leaves_the_task_not_due(
        self, integration: HomeAssistant
    ) -> None:
        await press(integration, PASSIONFRUIT_FEED_DONE)
        assert integration.states.get(PASSIONFRUIT_FEED_DUE).state == "off"

    async def test_a_press_does_not_touch_another_plants_task(
        self, integration: HomeAssistant
    ) -> None:
        """The care log is keyed per (plant, task); a shared key would make one
        plant's feeding silently reset another's."""
        await press(integration, PASSIONFRUIT_FEED_DONE)
        assert integration.states.get(PASSIONFRUIT_FEED_DAYS).state == "0.0"
        assert (
            integration.states.get("sensor.plant_monstera_pest_check_days_since").state
            == "unknown"
        )

    async def test_a_press_is_visible_to_the_feed_immediately(
        self, integration: HomeAssistant
    ) -> None:
        """The sensors are not subscribed to the store, so the press has to tell
        them. Without the dispatcher signal they would show stale values until
        the next half-hour tick."""
        await press(integration, MONSTERA_PEST_DONE)
        assert (
            integration.states.get("sensor.plant_monstera_pest_check_days_since").state
            == "0.0"
        )


class TestFeedShape:
    """The feed is the integration point — a dashboard card now, a bridge to an
    external task system later. Its shape is a contract."""

    async def test_items_is_a_real_list_of_dicts(
        self, integration: HomeAssistant
    ) -> None:
        state = integration.states.get(OUTSTANDING)
        items = state.attributes["items"]
        assert isinstance(items, list)
        assert all(isinstance(item, dict) for item in items)

    async def test_attention_counts_only_its_own_plant(
        self, integration: HomeAssistant
    ) -> None:
        state = integration.states.get(PASSIONFRUIT_ATTENTION)
        assert state.state == "0"
        assert state.attributes["items"] == []

    async def test_interval_is_carried_so_a_consumer_need_not_look_it_up(
        self, integration: HomeAssistant
    ) -> None:
        state = integration.states.get(PASSIONFRUIT_FEED_DAYS)
        assert state.attributes["every_days"] == 14
