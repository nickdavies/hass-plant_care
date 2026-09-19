"""Parsing the `plant_care:` config.

Every reference a plant makes is resolved at parse time, and everything that
would otherwise fail quietly at runtime is refused here by name. The theme of
these tests: a config that surprises us must fail loudly, never degrade.
"""

from __future__ import annotations

from datetime import time
from typing import Any

import pytest
import voluptuous as vol

from custom_components.plant_care.model import (
    AwakeAwareWindow,
    Calibrated,
    Calibrating,
    FixedWindow,
    InvalidPlantConfig,
    Lit,
    Weekday,
    parse,
    schema,
)
from custom_components.plant_care.model.config import DEFAULT_SUN_LUX_TO_PPFD

PROBE_MODELS: dict[str, Any] = {
    "thirdreality_soil_gen2": {"heartbeat_minutes": 10, "deadband_pp": 1.0}
}

CARE_TASKS: dict[str, Any] = {
    "water": {
        "display": "Water",
        "icon": "mdi:watering-can",
        "detected_by": "calibrated_moisture",
    },
    "feed": {"display": "Feed", "icon": "mdi:nutrition"},
    "pest_check": {"display": "Pest check", "icon": "mdi:bug-outline"},
}

DLI_CATEGORIES: dict[str, Any] = {
    "foliage_tropical": {"low": 4.0, "high": 9.0},
    "shade": {"low": 3.0, "high": 6.0},
}

OWNERS: dict[str, Any] = {
    "nick": "notify.nick",
    "britta": "notify.britta",
    "primary": "notify.phones",
}

# The household file, shared with light_motion_profiles. `guests` and
# `everyone` name people this component knows nothing about, on purpose.
GROUPS: dict[str, Any] = {
    "primary": ["nick", "britta"],
    "guests": ["guest_1", "guest_2"],
    "everyone": ["nick", "britta", "guest_1", "guest_2"],
}

SYSTEM_NOTIFY = "notify.phones"

CALIBRATED_PLANT: dict[str, Any] = {
    "name": "passionfruit",
    "display": "Passionfruit",
    "species": "passiflora edulis",
    "owner": "nick",
    "moisture": {
        "model": "thirdreality_soil_gen2",
        "entities": {
            "moisture": "sensor.roam_sensor_moisture_1_soil_moisture",
            "temperature": "sensor.roam_sensor_moisture_1_temperature",
            "battery": "sensor.roam_sensor_moisture_1_battery",
        },
        "calibration": {
            "field_capacity": 79.54,
            "dry_point": 53.18,
            "fc_tolerance": 8.0,
        },
    },
    "care": [{"task": "feed", "every_days": 14}],
}

CALIBRATING_PLANT: dict[str, Any] = {
    "name": "monstera",
    "owner": "nick",
    "moisture": {
        "model": "thirdreality_soil_gen2",
        "entities": {
            "moisture": "sensor.nick_study_sensor_monstera_window_soil_moisture"
        },
        "calibration": "calibrating",
    },
}

SENSORLESS_PLANT: dict[str, Any] = {
    "name": "front_step_pot",
    "display": "Front step pot",
    "owner": "nick",
    "care": [{"task": "water", "every_days": 4}],
}

STUDY_LIGHT: dict[str, Any] = {
    "name": "study_shelf",
    "switch": "switch.nick_study_outlet_sansi_100w_lamp",
    "lux_to_ppfd": 0.0125,
    "window": {
        "awake_aware": {
            "presence": "sensor.person_presence_nick",
            "on_if_awake_after": "06:00",
            "on_after": "09:00",
            "on_even_if_asleep_until": "17:00",
            "on_until": "19:00",
        }
    },
}

STUDY_LUX: dict[str, Any] = {
    "name": "study_shelf",
    "entities": ["sensor.esphome_study_lux_1", "sensor.esphome_study_lux_2"],
}

LIT_PLANT: dict[str, Any] = {
    "name": "monstera",
    "owner": "nick",
    "lights": ["study_shelf"],
    "lux": "study_shelf",
    "dli": {"category": "foliage_tropical"},
}


def load_config(
    *plants: dict[str, Any],
    lights: list[dict[str, Any]] | None = None,
    lux: list[dict[str, Any]] | None = None,
    **tables: Any,
):
    """Schema then parse, the way `CONFIG_SCHEMA` runs them."""
    document: dict[str, Any] = {
        "probe_models": PROBE_MODELS,
        "care_tasks": CARE_TASKS,
        "dli_categories": DLI_CATEGORIES,
        "owners": OWNERS,
        "groups": GROUPS,
        "system_notify": SYSTEM_NOTIFY,
        "plants": list(plants),
    }
    if lights is not None:
        document["lights"] = lights
    if lux is not None:
        document["lux_sensors"] = lux
    document.update(tables)
    return parse(schema()(document))


def load(*plants: dict[str, Any]):
    """Just the plants — most tests here are about one plant's shape."""
    return load_config(*plants).plants


def with_moisture(plant: dict[str, Any], **changes: Any) -> dict[str, Any]:
    return {**plant, "moisture": {**plant["moisture"], **changes}}


# ---- Tables -------------------------------------------------------------


class TestTables:
    def test_a_probe_model_is_looked_up_by_name(self) -> None:
        (plant,) = load(CALIBRATED_PLANT)
        assert plant.moisture.probe.heartbeat_minutes == 10
        assert plant.moisture.probe.deadband_pp == 1.0

    def test_an_unknown_probe_model_names_the_plant(self) -> None:
        with pytest.raises(InvalidPlantConfig, match="passionfruit.*nope"):
            load(with_moisture(CALIBRATED_PLANT, model="nope"))

    def test_a_care_task_arrives_with_its_definition_attached(self) -> None:
        """So nothing downstream needs the table."""
        (plant,) = load(CALIBRATED_PLANT)
        assert plant.care[0].display == "Feed"
        assert plant.care[0].icon == "mdi:nutrition"
        assert plant.care[0].every_days == 14

    def test_an_unknown_care_task_names_the_plant(self) -> None:
        with pytest.raises(InvalidPlantConfig, match="front_step_pot.*repot"):
            load({**SENSORLESS_PLANT, "care": [{"task": "repot", "every_days": 400}]})

    def test_an_unknown_dli_category_names_the_plant_and_lists_the_table(
        self,
    ) -> None:
        with pytest.raises(InvalidPlantConfig, match="monstera.*cactus.*shade"):
            load_config(
                {**LIT_PLANT, "dli": {"category": "cactus"}},
                lights=[STUDY_LIGHT],
                lux=[STUDY_LUX],
            )

    def test_a_table_key_must_be_an_identifier(self) -> None:
        """Task names become part of entity ids, so a key with a space or a
        capital would produce an id Home Assistant rejects."""
        with pytest.raises(InvalidPlantConfig, match="care_tasks.*Pest Check"):
            load_config(
                SENSORLESS_PLANT,
                care_tasks={
                    **CARE_TASKS,
                    "Pest Check": {"display": "Pest check", "icon": "mdi:bug-outline"},
                },
            )

    def test_an_inverted_category_band_names_the_category(self) -> None:
        with pytest.raises(InvalidPlantConfig, match="dli category 'shade'"):
            load_config(
                SENSORLESS_PLANT,
                dli_categories={"shade": {"low": 6.0, "high": 3.0}},
            )

    def test_a_zero_heartbeat_is_rejected(self) -> None:
        with pytest.raises(vol.Invalid):
            load_config(
                CALIBRATED_PLANT,
                probe_models={
                    "thirdreality_soil_gen2": {
                        "heartbeat_minutes": 0,
                        "deadband_pp": 1.0,
                    }
                },
            )

    def test_a_non_mdi_icon_is_rejected(self) -> None:
        """Renders as a blank square, so nothing downstream would ever notice."""
        for bad in ["mdi-nutrition", "nutrition", "mdi:", "mdi:Nutrition"]:
            with pytest.raises(vol.Invalid):
                load_config(
                    SENSORLESS_PLANT,
                    care_tasks={**CARE_TASKS, "feed": {"display": "Feed", "icon": bad}},
                )

    def test_an_unknown_detection_source_is_rejected(self) -> None:
        with pytest.raises(vol.Invalid):
            load_config(
                SENSORLESS_PLANT,
                care_tasks={
                    **CARE_TASKS,
                    "water": {**CARE_TASKS["water"], "detected_by": "vibes"},
                },
            )

    def test_the_tables_are_optional(self) -> None:
        """A config with nothing but plants that reference nothing is valid."""
        config = parse(
            schema()(
                {
                    "plants": [{"name": "pot", "owner": "nick"}],
                    "owners": {"nick": "notify.nick"},
                    "system_notify": "notify.nick",
                }
            )
        )
        assert config.plants[0].display == "Pot"


# ---- Calibration --------------------------------------------------------


class TestCalibration:
    """A plant is either calibrating or has both endpoints. Nothing between."""

    def test_calibrating_is_explicit_and_not_falsy(self) -> None:
        """`Calibrating` is a real object, not `None`, so a calibrating plant
        can never be mistaken for one with no probe at all."""
        (plant,) = load(CALIBRATING_PLANT)
        assert isinstance(plant.moisture.calibration, Calibrating)
        assert bool(plant.moisture.calibration) is True
        assert not plant.moisture.is_calibrated

    def test_measured_endpoints_are_carried_verbatim(self) -> None:
        (plant,) = load(CALIBRATED_PLANT)
        cal = plant.moisture.calibration
        assert isinstance(cal, Calibrated)
        assert cal.field_capacity == 79.54
        assert cal.dry_point == 53.18
        assert cal.fc_tolerance == 8.0

    def test_an_unset_tolerance_stays_unset(self) -> None:
        """Not defaulted at parse time, so "8 was chosen" and "nobody said"
        remain distinguishable right up to the point of use."""
        (plant,) = load(
            with_moisture(
                CALIBRATED_PLANT,
                calibration={"field_capacity": 79.54, "dry_point": 53.18},
            )
        )
        assert plant.moisture.calibration.fc_tolerance is None

    def test_the_waterlogged_budget_is_per_plant_and_optional(self) -> None:
        (plant,) = load(CALIBRATED_PLANT)
        assert plant.moisture.calibration.waterlogged_budget_pct is None

        marshy = with_moisture(
            CALIBRATED_PLANT,
            calibration={
                **CALIBRATED_PLANT["moisture"]["calibration"],
                "waterlogged_budget_pct": 70,
            },
        )
        (plant,) = load(marshy)
        assert plant.moisture.calibration.waterlogged_budget_pct == 70.0

    def test_a_waterlogged_budget_outside_the_range_is_rejected(self) -> None:
        for bad in [0, 101, -5]:
            with pytest.raises(vol.Invalid):
                load(
                    with_moisture(
                        CALIBRATED_PLANT,
                        calibration={
                            **CALIBRATED_PLANT["moisture"]["calibration"],
                            "waterlogged_budget_pct": bad,
                        },
                    )
                )

    def test_a_half_written_calibration_is_rejected(self) -> None:
        with pytest.raises(vol.Invalid):
            load(with_moisture(CALIBRATED_PLANT, calibration={"field_capacity": 79.54}))

    def test_an_inverted_calibration_names_the_plant(self) -> None:
        with pytest.raises(InvalidPlantConfig, match="passionfruit"):
            load(
                with_moisture(
                    CALIBRATED_PLANT,
                    calibration={"field_capacity": 50.0, "dry_point": 60.0},
                )
            )

    def test_calibration_is_required_on_a_probe(self) -> None:
        """Absence would be ambiguous between "not yet" and "forgot"."""
        moisture = {k: v for k, v in CALIBRATED_PLANT["moisture"].items()}
        del moisture["calibration"]
        with pytest.raises(vol.Invalid):
            load({**CALIBRATED_PLANT, "moisture": moisture})

    def test_only_the_word_calibrating_means_calibrating(self) -> None:
        with pytest.raises(vol.Invalid, match="calibrating"):
            load(with_moisture(CALIBRATING_PLANT, calibration="pending"))


# ---- Care tasks and detection -------------------------------------------


class TestCareTasks:
    def test_water_is_rejected_when_a_calibrated_probe_detects_it(self) -> None:
        """No "I did a thing" button for anything a sensor already sees."""
        with pytest.raises(InvalidPlantConfig, match="passionfruit.*already detects"):
            load(
                {
                    **CALIBRATED_PLANT,
                    "care": [
                        {"task": "feed", "every_days": 14},
                        {"task": "water", "every_days": 4},
                    ],
                }
            )

    def test_water_is_allowed_while_calibrating(self) -> None:
        """No detector exists yet, so a reminder is the only thing it can have."""
        (plant,) = load(
            {**CALIBRATING_PLANT, "care": [{"task": "water", "every_days": 4}]}
        )
        assert plant.care_task("water") is not None

    def test_water_is_allowed_with_no_probe_at_all(self) -> None:
        (plant,) = load(SENSORLESS_PLANT)
        assert plant.care_task("water").every_days == 4

    def test_the_same_task_twice_names_the_plant(self) -> None:
        with pytest.raises(InvalidPlantConfig, match="front_step_pot.*more than once"):
            load(
                {
                    **SENSORLESS_PLANT,
                    "care": [
                        {"task": "feed", "every_days": 14},
                        {"task": "feed", "every_days": 21},
                    ],
                }
            )

    def test_a_zero_interval_is_rejected(self) -> None:
        with pytest.raises(vol.Invalid):
            load({**SENSORLESS_PLANT, "care": [{"task": "feed", "every_days": 0}]})


# ---- Plant shape --------------------------------------------------------


class TestPlantShape:
    def test_a_plant_needs_nothing_but_a_name(self) -> None:
        (plant,) = load({"name": "front_step_pot", "owner": "nick"})
        assert plant.moisture is None
        assert plant.care == ()
        assert plant.species is None
        assert plant.light_source is None

    def test_display_defaults_to_the_title_cased_name(self) -> None:
        (plant,) = load({"name": "ficus_alii", "owner": "nick"})
        assert plant.display == "Ficus Alii"

    def test_display_overrides_the_derived_name(self) -> None:
        (plant,) = load(
            {"name": "ficus_elastica", "display": "Rubber Tree", "owner": "nick"}
        )
        assert plant.display == "Rubber Tree"

    def test_a_blank_or_padded_display_is_rejected(self) -> None:
        """Both render as a card with no readable title."""
        for bad in ["", "   ", " Monstera", "Monstera "]:
            with pytest.raises(vol.Invalid):
                load({"name": "monstera", "display": bad, "owner": "nick"})

    def test_a_name_must_be_an_identifier(self) -> None:
        """Every entity id is built from it."""
        for bad in ["Monstera", "big monstera", "monstera-1", ""]:
            with pytest.raises(vol.Invalid):
                load({"name": bad, "owner": "nick"})

    def test_duplicate_plant_names_are_rejected(self) -> None:
        """Every entity id is built from the name, so a duplicate would collide
        silently and the second plant would win."""
        with pytest.raises(InvalidPlantConfig, match="more than once"):
            load(CALIBRATED_PLANT, CALIBRATED_PLANT)

    def test_an_unknown_key_is_rejected(self) -> None:
        """A typo is an error, not a silently ignored field."""
        with pytest.raises(vol.Invalid):
            load({**CALIBRATED_PLANT, "lightLevel": "bright"})

    def test_a_bare_entity_name_is_rejected(self) -> None:
        with pytest.raises(vol.Invalid):
            load(
                with_moisture(
                    CALIBRATING_PLANT, entities={"moisture": "not_an_entity_id"}
                )
            )

    def test_absent_probe_attributes_stay_absent(self) -> None:
        """A probe that publishes no temperature has no temperature entity,
        rather than one pointing at an id that will never exist."""
        (plant,) = load(CALIBRATING_PLANT)
        assert plant.moisture.temperature_entity is None
        assert plant.moisture.battery_entity is None
        assert (
            plant.moisture.moisture_entity.entity_id
            == "sensor.nick_study_sensor_monstera_window_soil_moisture"
        )

    def test_care_lookup_by_task(self) -> None:
        (plant,) = load(CALIBRATED_PLANT)
        assert plant.care_task("feed").every_days == 14
        assert plant.care_task("repot") is None


# ---- Fixtures -----------------------------------------------------------


class TestFixtures:
    def test_fixtures_resolve_by_name(self) -> None:
        config = load_config(LIT_PLANT, lights=[STUDY_LIGHT], lux=[STUDY_LUX])
        (plant,) = config.plants
        (fixture,) = config.fixtures_for(plant)
        assert fixture.switch_entity == "switch.nick_study_outlet_sansi_100w_lamp"
        assert config.lux("study_shelf").entities == (
            "sensor.esphome_study_lux_1",
            "sensor.esphome_study_lux_2",
        )

    def test_an_unknown_light_fixture_names_the_plant(self) -> None:
        with pytest.raises(InvalidPlantConfig, match="monstera.*greenhouse"):
            load_config(
                {**LIT_PLANT, "lights": ["greenhouse"]},
                lights=[STUDY_LIGHT],
                lux=[STUDY_LUX],
            )

    def test_an_unknown_lux_fixture_names_the_plant(self) -> None:
        with pytest.raises(InvalidPlantConfig, match="monstera.*hallway"):
            load_config(
                {**LIT_PLANT, "lux": "hallway"}, lights=[STUDY_LIGHT], lux=[STUDY_LUX]
            )

    def test_duplicate_fixture_names_are_rejected(self) -> None:
        with pytest.raises(InvalidPlantConfig, match="light fixture.*more than once"):
            load_config(LIT_PLANT, lights=[STUDY_LIGHT, STUDY_LIGHT], lux=[STUDY_LUX])
        with pytest.raises(InvalidPlantConfig, match="lux fixture.*more than once"):
            load_config(LIT_PLANT, lights=[STUDY_LIGHT], lux=[STUDY_LUX, STUDY_LUX])

    def test_an_unreferenced_fixture_is_reported_not_refused(self) -> None:
        """The lamp still runs its window; only its outcome checks are off."""
        spare = {
            "name": "spare_shelf",
            "switch": "switch.spare_outlet_grow_lamp_1",
            "window": {"fixed": {"from": "07:00", "to": "19:00"}},
        }
        config = load_config(LIT_PLANT, lights=[STUDY_LIGHT, spare], lux=[STUDY_LUX])
        assert [f.name for f in config.unreferenced_lights()] == ["spare_shelf"]

    def test_the_sun_factor_defaults_and_can_be_overridden(self) -> None:
        config = load_config(LIT_PLANT, lights=[STUDY_LIGHT], lux=[STUDY_LUX])
        assert config.lux("study_shelf").sun_lux_to_ppfd == DEFAULT_SUN_LUX_TO_PPFD

        measured = {**STUDY_LUX, "sun_lux_to_ppfd": 0.02}
        config = load_config(LIT_PLANT, lights=[STUDY_LIGHT], lux=[measured])
        assert config.lux("study_shelf").sun_lux_to_ppfd == 0.02

    def test_a_lux_fixture_needs_at_least_one_member(self) -> None:
        """Averaging nothing reads as `unknown` for ever, which looks exactly
        like darkness."""
        with pytest.raises(vol.Invalid):
            load_config(
                LIT_PLANT, lights=[STUDY_LIGHT], lux=[{**STUDY_LUX, "entities": []}]
            )


class TestWindows:
    def test_an_awake_aware_window_carries_its_presence_entity(self) -> None:
        config = load_config(LIT_PLANT, lights=[STUDY_LIGHT], lux=[STUDY_LUX])
        window = config.light("study_shelf").window
        assert isinstance(window, AwakeAwareWindow)
        assert window.presence_entity == "sensor.person_presence_nick"
        assert window.on_if_awake_after == time(6, 0)
        assert window.on_until == time(19, 0)
        assert config.light("study_shelf").is_sleep_sensitive

    def test_a_fixed_window_has_no_presence_at_all(self) -> None:
        fixed = {**STUDY_LIGHT, "window": {"fixed": {"from": "07:00", "to": "19:00"}}}
        config = load_config(LIT_PLANT, lights=[fixed], lux=[STUDY_LUX])
        window = config.light("study_shelf").window
        assert isinstance(window, FixedWindow)
        assert window.start == time(7, 0)
        assert window.days is None  # every day
        assert config.light("study_shelf").presence_entity is None

    def test_a_window_must_pick_exactly_one_shape(self) -> None:
        both = {
            **STUDY_LIGHT,
            "window": {
                **STUDY_LIGHT["window"],
                "fixed": {"from": "07:00", "to": "19:00"},
            },
        }
        with pytest.raises(vol.Invalid, match="sleep-sensitive or it is not"):
            load_config(LIT_PLANT, lights=[both], lux=[STUDY_LUX])

        neither = {**STUDY_LIGHT, "window": {}}
        with pytest.raises(vol.Invalid, match="fixed.*awake_aware"):
            load_config(LIT_PLANT, lights=[neither], lux=[STUDY_LUX])

    def test_a_half_written_window_is_rejected(self) -> None:
        broken = {
            **STUDY_LIGHT,
            "window": {"awake_aware": {"presence": "sensor.p", "on_until": "19:00"}},
        }
        with pytest.raises(vol.Invalid):
            load_config(LIT_PLANT, lights=[broken], lux=[STUDY_LUX])

    def test_an_unquoted_time_says_to_quote_it(self) -> None:
        """YAML reads a bare `06:00` as the integer 360."""
        broken = {**STUDY_LIGHT, "window": {"fixed": {"from": 420, "to": "19:00"}}}
        with pytest.raises(vol.Invalid, match="quote"):
            load_config(LIT_PLANT, lights=[broken], lux=[STUDY_LUX])

    def test_a_malformed_time_is_rejected(self) -> None:
        for bad in ["seven", "7:05", "24:00", "07:60", "0700"]:
            broken = {**STUDY_LIGHT, "window": {"fixed": {"from": bad, "to": "19:00"}}}
            with pytest.raises(vol.Invalid):
                load_config(LIT_PLANT, lights=[broken], lux=[STUDY_LUX])

    def test_a_window_that_runs_backwards_names_the_fixture(self) -> None:
        backwards = {
            **STUDY_LIGHT,
            "window": {"fixed": {"from": "19:00", "to": "07:00"}},
        }
        with pytest.raises(InvalidPlantConfig, match="study_shelf.*forward"):
            load_config(LIT_PLANT, lights=[backwards], lux=[STUDY_LUX])

        out_of_order = {
            **STUDY_LIGHT,
            "window": {
                "awake_aware": {
                    **STUDY_LIGHT["window"]["awake_aware"],
                    "on_after": "05:00",
                }
            },
        }
        with pytest.raises(InvalidPlantConfig, match="study_shelf.*on_if_awake_after"):
            load_config(LIT_PLANT, lights=[out_of_order], lux=[STUDY_LUX])

    def test_a_window_crossing_midnight_is_refused(self) -> None:
        empty = {**STUDY_LIGHT, "window": {"fixed": {"from": "07:00", "to": "07:00"}}}
        with pytest.raises(InvalidPlantConfig, match="midnight"):
            load_config(LIT_PLANT, lights=[empty], lux=[STUDY_LUX])

    def test_days_are_parsed_into_the_enum_not_left_as_strings(self) -> None:
        weekend = {
            **STUDY_LIGHT,
            "window": {
                "fixed": {"from": "07:00", "to": "19:00", "days": ["sat", "sun"]}
            },
        }
        config = load_config(LIT_PLANT, lights=[weekend], lux=[STUDY_LUX])
        assert config.light("study_shelf").window.days == frozenset(
            {Weekday.SAT, Weekday.SUN}
        )

    def test_a_day_that_is_not_a_weekday_is_rejected(self) -> None:
        broken = {
            **STUDY_LIGHT,
            "window": {"fixed": {"from": "07:00", "to": "19:00", "days": ["caturday"]}},
        }
        with pytest.raises(vol.Invalid):
            load_config(LIT_PLANT, lights=[broken], lux=[STUDY_LUX])


# ---- DLI ----------------------------------------------------------------


class TestDli:
    def test_the_category_band_is_applied(self) -> None:
        (plant,) = load_config(LIT_PLANT, lights=[STUDY_LIGHT], lux=[STUDY_LUX]).plants
        assert plant.dli.category == "foliage_tropical"
        assert plant.dli.preferred.low == 4.0
        assert plant.dli.preferred.high == 9.0
        assert plant.dli.preferred_overridden is False

    def test_a_preferred_override_wins_and_is_marked(self) -> None:
        """The override is how you say "this corner is dim and I have accepted
        that"; the mark is what keeps that visible months later."""
        dim = {
            **LIT_PLANT,
            "dli": {"category": "foliage_tropical", "preferred": {"low": 2, "high": 4}},
        }
        (plant,) = load_config(dim, lights=[STUDY_LIGHT], lux=[STUDY_LUX]).plants
        assert plant.dli.preferred.low == 2.0
        assert plant.dli.preferred_overridden is True

    def test_window_and_budget_default_from_the_objective(self) -> None:
        (plant,) = load_config(LIT_PLANT, lights=[STUDY_LIGHT], lux=[STUDY_LUX]).plants
        assert plant.dli.window_days == 28
        assert plant.dli.budget == 20.0
        assert plant.dli.survival is None

    def test_survival_and_budget_are_carried(self) -> None:
        full = {
            **LIT_PLANT,
            "dli": {
                "category": "foliage_tropical",
                "survival": {"low": 2.0, "high": 20.0},
                "window_days": 14,
                "budget": 5.0,
            },
        }
        (plant,) = load_config(full, lights=[STUDY_LIGHT], lux=[STUDY_LUX]).plants
        assert plant.dli.survival.high == 20.0
        assert plant.dli.window_days == 14
        assert plant.dli.budget == 5.0

    def test_an_inverted_preferred_band_names_the_plant(self) -> None:
        bad = {
            **LIT_PLANT,
            "dli": {"category": "foliage_tropical", "preferred": {"low": 9, "high": 4}},
        }
        with pytest.raises(InvalidPlantConfig, match="monstera"):
            load_config(bad, lights=[STUDY_LIGHT], lux=[STUDY_LUX])

    def test_a_preferred_band_outside_survival_names_the_plant(self) -> None:
        bad = {
            **LIT_PLANT,
            "dli": {"category": "foliage_tropical", "survival": {"low": 5, "high": 8}},
        }
        with pytest.raises(InvalidPlantConfig, match="monstera"):
            load_config(bad, lights=[STUDY_LIGHT], lux=[STUDY_LUX])

    def test_a_zero_budget_is_rejected(self) -> None:
        bad = {**LIT_PLANT, "dli": {"category": "foliage_tropical", "budget": 0}}
        with pytest.raises(vol.Invalid):
            load_config(bad, lights=[STUDY_LIGHT], lux=[STUDY_LUX])

    def test_an_objective_nothing_measures_is_rejected(self) -> None:
        """A DLI budget with no lux fixture would sit at zero for ever and burn
        continuously, which looks exactly like a plant in a cupboard."""
        unmeasured = {k: v for k, v in LIT_PLANT.items() if k != "lux"}
        with pytest.raises(InvalidPlantConfig, match="nothing would measure"):
            load_config(unmeasured, lights=[STUDY_LIGHT], lux=[STUDY_LUX])

    def test_a_mixed_plant_under_a_lamp_with_no_factor_is_rejected(self) -> None:
        """Its lux reading is sun for part of the day and lamp for the rest. A
        lamp that cannot say what its own light is worth makes every DLI figure
        for this plant wrong, quietly, in a direction nobody can guess."""
        no_factor = {k: v for k, v in STUDY_LIGHT.items() if k != "lux_to_ppfd"}
        with pytest.raises(InvalidPlantConfig, match="lux_to_ppfd"):
            load_config(LIT_PLANT, lights=[no_factor], lux=[STUDY_LUX])

    def test_a_sun_only_plant_needs_no_lamp_factor(self) -> None:
        """Nothing is switching, so one factor serves the whole day."""
        no_factor = {k: v for k, v in STUDY_LIGHT.items() if k != "lux_to_ppfd"}
        sun_only = {k: v for k, v in LIT_PLANT.items() if k != "lights"}
        config = load_config(sun_only, lights=[no_factor], lux=[STUDY_LUX])
        assert config.plants[0].light_source is Lit.SUN
        assert not config.plants[0].is_mixed_light

    def test_the_light_source_is_derived_from_what_the_plant_has(self) -> None:
        (lit,) = load_config(LIT_PLANT, lights=[STUDY_LIGHT], lux=[STUDY_LUX]).plants
        assert lit.light_source is Lit.MIXED

        grow_only = {k: v for k, v in LIT_PLANT.items() if k not in ("lux", "dli")}
        (grow,) = load_config(grow_only, lights=[STUDY_LIGHT], lux=[STUDY_LUX]).plants
        assert grow.light_source is Lit.GROW


# ---- Owners -------------------------------------------------------------

BRITTAS_PLANT: dict[str, Any] = {**SENSORLESS_PLANT, "owner": "britta"}
SHARED_PLANT: dict[str, Any] = {**CALIBRATING_PLANT, "owner": "primary"}


class TestOwners:
    def test_an_owner_is_required(self) -> None:
        """An unowned plant has no phone to page and no tab to appear on."""
        unowned = {k: v for k, v in CALIBRATED_PLANT.items() if k != "owner"}
        with pytest.raises(vol.Invalid, match="owner"):
            load(unowned)

    def test_an_unknown_owner_names_the_plant_and_the_owner(self) -> None:
        with pytest.raises(InvalidPlantConfig, match="front_step_pot.*ghost"):
            load({**SENSORLESS_PLANT, "owner": "ghost"})

    def test_a_person_owner_resolves_to_their_action(self) -> None:
        config = load_config(CALIBRATED_PLANT, BRITTAS_PLANT)
        nicks, brittas = config.plants
        assert config.notify_for(nicks) == "notify.nick"
        assert config.notify_for(brittas) == "notify.britta"

    def test_a_group_owner_resolves_to_the_shared_action(self) -> None:
        """One action per group, not one per member: the group's phones are
        the notify platform's business."""
        config = load_config(SHARED_PLANT)
        (shared,) = config.plants
        assert config.is_group("primary")
        assert config.notify_for(shared) == "notify.phones"

    def test_people_are_the_owners_that_are_not_groups(self) -> None:
        config = load_config(CALIBRATED_PLANT)
        assert config.people() == ("nick", "britta")
        assert config.members("nick") == ("nick",)
        assert config.members("primary") == ("nick", "britta")

    def test_a_person_sees_their_own_and_their_groups_plants(self) -> None:
        config = load_config(CALIBRATED_PLANT, BRITTAS_PLANT, SHARED_PLANT)
        assert [p.name for p in config.plants_for("britta")] == [
            "front_step_pot",
            "monstera",
        ]
        assert [p.name for p in config.plants_for("nick")] == [
            "passionfruit",
            "monstera",
        ]

    def test_a_persons_fixtures_are_those_over_their_plants_once_each(
        self,
    ) -> None:
        spare = {
            **STUDY_LIGHT,
            "name": "spare_shelf",
            "switch": "switch.spare_outlet_grow_lamp_1",
        }
        under_both = {**LIT_PLANT, "name": "fern", "lights": ["study_shelf"]}
        config = load_config(
            LIT_PLANT,
            under_both,
            {**BRITTAS_PLANT, "lights": ["spare_shelf"]},
            lights=[STUDY_LIGHT, spare],
            lux=[STUDY_LUX],
        )
        assert [f.name for f in config.fixtures_for_person("nick")] == ["study_shelf"]
        assert [f.name for f in config.fixtures_for_person("britta")] == ["spare_shelf"]

    def test_a_group_member_without_an_action_names_the_group_and_member(
        self,
    ) -> None:
        """Every member gets a tab and a sensor, so needs an action."""
        with pytest.raises(InvalidPlantConfig, match="owner 'primary'.*'britta'"):
            load_config(
                CALIBRATED_PLANT,
                owners={"nick": "notify.nick", "primary": "notify.phones"},
            )

    def test_a_group_inside_a_group_is_refused(self) -> None:
        with pytest.raises(InvalidPlantConfig, match="owner 'everyone'.*'primary'"):
            load_config(
                CALIBRATED_PLANT,
                owners={**OWNERS, "everyone": "notify.phones"},
                groups={**GROUPS, "everyone": ["primary"]},
            )

    def test_an_empty_group_is_refused(self) -> None:
        with pytest.raises(InvalidPlantConfig, match="owner 'primary'.*no members"):
            load_config(CALIBRATED_PLANT, groups={**GROUPS, "primary": []})

    def test_a_group_nobody_owns_through_may_name_strangers(self) -> None:
        """`guests` is not in `owners`, so its strangers are not checked."""
        config = load_config(CALIBRATED_PLANT)
        assert config.is_group("guests")
        assert "guests" not in config.owners.actions
        assert "guest_1" not in config.people()

    def test_an_owner_key_must_be_an_identifier(self) -> None:
        """They become entity ids and tab paths."""
        with pytest.raises(InvalidPlantConfig, match="owners key 'Nick'"):
            load_config(CALIBRATED_PLANT, owners={**OWNERS, "Nick": "notify.nick"})

    def test_all_is_not_an_owner(self) -> None:
        """The shared tab's path."""
        with pytest.raises(InvalidPlantConfig, match="'all'.*reserved"):
            load_config(CALIBRATED_PLANT, owners={**OWNERS, "all": "notify.phones"})

    @pytest.mark.parametrize(
        "bad", ["nick", "sensor.nick", "notify.Nick", "notify.", 3]
    )
    def test_anything_but_a_notify_action_is_rejected(self, bad: Any) -> None:
        with pytest.raises(vol.Invalid, match="notify action"):
            load_config(CALIBRATED_PLANT, owners={**OWNERS, "nick": bad})
        with pytest.raises(vol.Invalid, match="notify action"):
            load_config(CALIBRATED_PLANT, system_notify=bad)

    def test_system_notify_is_required(self) -> None:
        """A fault belonging to no plant still has to reach someone."""
        document = {
            "owners": OWNERS,
            "groups": GROUPS,
            "plants": [CALIBRATED_PLANT],
        }
        with pytest.raises(vol.Invalid, match="system_notify"):
            schema()(document)

    def test_system_notify_is_kept_as_written(self) -> None:
        assert load_config(CALIBRATED_PLANT).owners.system_notify == "notify.phones"
