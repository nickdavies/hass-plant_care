"""Parsing the resolved document.

The theme throughout: this document is machine-generated, so anything surprising
means the generator and this component have diverged. Every test here asserts
that such a divergence is loud.
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


def document(*plants: dict[str, Any]) -> dict[str, Any]:
    return {"plants": list(plants)}


CALIBRATED_PLANT: dict[str, Any] = {
    "name": "passionfruit",
    "display": "Passionfruit",
    "species": "passiflora edulis",
    "moisture": {
        "entities": {
            "moisture": "sensor.roam_sensor_moisture_1_soil_moisture",
            "temperature": "sensor.roam_sensor_moisture_1_temperature",
            "battery": "sensor.roam_sensor_moisture_1_battery",
        },
        "probe": {"heartbeat_minutes": 10, "deadband_pp": 1.0},
        "calibration": {
            "field_capacity": 79.54,
            "dry_point": 53.18,
            "fc_tolerance": 8.0,
        },
    },
    "care": [
        {"task": "feed", "display": "Feed", "icon": "mdi:nutrition", "every_days": 14}
    ],
}

CALIBRATING_PLANT: dict[str, Any] = {
    "name": "monstera",
    "display": "Monstera",
    "moisture": {
        "entities": {
            "moisture": "sensor.nick_study_sensor_monstera_window_soil_moisture"
        },
        "probe": {"heartbeat_minutes": 10, "deadband_pp": 1.0},
    },
}

SENSORLESS_PLANT: dict[str, Any] = {
    "name": "front_step_pot",
    "display": "Front step pot",
    "care": [
        {
            "task": "water",
            "display": "Water",
            "icon": "mdi:watering-can",
            "every_days": 4,
        }
    ],
}


def load(*plants: dict[str, Any]):
    """Just the plants — most tests here are about one plant's shape."""
    return load_config(*plants).plants


def load_config(*plants: dict[str, Any]):
    doc = schema()(document(*plants))
    return parse(doc)


class TestCalibrationContract:
    """The one contract the generator and this component must agree on."""

    def test_absent_calibration_means_calibrating_not_a_default(self) -> None:
        (plant,) = load(CALIBRATING_PLANT)
        assert isinstance(plant.moisture.calibration, Calibrating)
        assert not plant.moisture.is_calibrated

    def test_calibrating_is_not_falsy(self) -> None:
        """`Calibrating` is a real object, not None.

        Guards the specific bug an `Optional[Calibrated]` would invite: someone
        writes `if plant.moisture.calibration:` and a calibrating plant silently
        takes the "no probe at all" branch.
        """
        (plant,) = load(CALIBRATING_PLANT)
        assert bool(plant.moisture.calibration) is True

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
        plant_data = {
            **CALIBRATED_PLANT,
            "moisture": {
                **CALIBRATED_PLANT["moisture"],
                "calibration": {"field_capacity": 79.54, "dry_point": 53.18},
            },
        }
        (plant,) = load(plant_data)
        assert plant.moisture.calibration.fc_tolerance is None


class TestStrictness:
    """A generated document that surprises us must fail, not degrade."""

    def test_a_half_written_calibration_is_rejected(self) -> None:
        plant_data = {
            **CALIBRATED_PLANT,
            "moisture": {
                **CALIBRATED_PLANT["moisture"],
                "calibration": {"field_capacity": 79.54},
            },
        }
        with pytest.raises(vol.Invalid):
            load(plant_data)

    def test_an_inverted_calibration_is_rejected_by_name(self) -> None:
        plant_data = {
            **CALIBRATED_PLANT,
            "moisture": {
                **CALIBRATED_PLANT["moisture"],
                "calibration": {"field_capacity": 50.0, "dry_point": 60.0},
            },
        }
        with pytest.raises(InvalidPlantConfig, match="passionfruit"):
            load(plant_data)

    def test_an_unknown_key_is_rejected(self) -> None:
        """A key this version does not understand means the generator emitted
        something newer. Running on a partial understanding is worse than not
        starting."""
        with pytest.raises(vol.Invalid):
            load({**CALIBRATED_PLANT, "lightLevel": "bright"})

    def test_a_device_reference_is_rejected(self) -> None:
        """The document carries entity ids and nothing else that points
        outward.

        A `{room, name}` path into the generator's zigbee inventory would be an
        identifier this component has no way to resolve, and would be useless to
        any other consumer of the same document. It used to be here; the schema
        now refuses it so it cannot come back by accident.
        """
        plant_data = {
            **CALIBRATING_PLANT,
            "moisture": {
                **CALIBRATING_PLANT["moisture"],
                "source": {"room": "nick_study", "name": "monstera_window"},
            },
        }
        with pytest.raises(vol.Invalid):
            load(plant_data)

    def test_a_bare_entity_name_is_rejected(self) -> None:
        plant_data = {
            **CALIBRATING_PLANT,
            "moisture": {
                **CALIBRATING_PLANT["moisture"],
                "entities": {"moisture": "not_an_entity_id"},
            },
        }
        with pytest.raises(vol.Invalid):
            load(plant_data)

    def test_a_zero_heartbeat_is_rejected(self) -> None:
        plant_data = {
            **CALIBRATING_PLANT,
            "moisture": {
                **CALIBRATING_PLANT["moisture"],
                "probe": {"heartbeat_minutes": 0, "deadband_pp": 1.0},
            },
        }
        with pytest.raises(vol.Invalid):
            load(plant_data)

    def test_duplicate_plant_names_are_rejected(self) -> None:
        """Every entity id is built from the name, so a duplicate would collide
        silently and the second plant would win."""
        with pytest.raises(InvalidPlantConfig, match="more than once"):
            load(CALIBRATED_PLANT, CALIBRATED_PLANT)

    def test_a_zero_interval_care_task_is_rejected(self) -> None:
        with pytest.raises(vol.Invalid):
            load(
                {
                    **SENSORLESS_PLANT,
                    "care": [{**SENSORLESS_PLANT["care"][0], "every_days": 0}],
                }
            )


class TestOptionalStructure:
    def test_a_plant_with_no_sensors_is_a_first_class_case(self) -> None:
        (plant,) = load(SENSORLESS_PLANT)
        assert plant.moisture is None
        assert len(plant.care) == 1
        assert plant.care[0].task == "water"

    def test_absent_probe_attributes_stay_absent(self) -> None:
        """A probe that publishes no temperature yields no temperature entity,
        rather than one pointing at an id that will never exist."""
        (plant,) = load(CALIBRATING_PLANT)
        assert plant.moisture.temperature_entity is None
        assert plant.moisture.battery_entity is None
        assert (
            plant.moisture.moisture_entity.entity_id
            == "sensor.nick_study_sensor_monstera_window_soil_moisture"
        )

    def test_species_is_optional(self) -> None:
        (with_species,) = load(CALIBRATED_PLANT)
        (without,) = load(CALIBRATING_PLANT)
        assert with_species.species == "passiflora edulis"
        assert without.species is None

    def test_care_lookup_by_task(self) -> None:
        (plant,) = load(CALIBRATED_PLANT)
        assert plant.care_task("feed").every_days == 14
        assert plant.care_task("repot") is None


# --- Lights, lux and DLI ---------------------------------------------------

STUDY_LIGHT: dict[str, Any] = {
    "name": "study_shelf",
    "switch": "switch.nick_study_outlet_sansi_100w_lamp",
    "room": "nick_study",
    "lux_to_ppfd": 0.0125,
    "window": {
        "mode": "awake_aware",
        "on_if_awake_after": "06:00",
        "on_after": "09:00",
        "on_even_if_asleep_until": "17:00",
        "on_until": "19:00",
        "presence": "sensor.person_presence_nick",
    },
}

STUDY_LUX: dict[str, Any] = {
    "name": "study_shelf",
    "entities": ["sensor.esphome_study_lux_1", "sensor.esphome_study_lux_2"],
    "sun_lux_to_ppfd": 0.0185,
}

DLI: dict[str, Any] = {
    "category": "foliage_tropical",
    "preferred": {"low": 4.0, "high": 9.0},
    "survival": {"low": 2.0, "high": 20.0},
    "preferred_overridden": False,
    "window_days": 28,
    "budget": 20.0,
}

LIT_PLANT: dict[str, Any] = {
    "name": "monstera",
    "display": "Monstera",
    "lights": ["study_shelf"],
    "lux": "study_shelf",
    "dli": DLI,
}


def load_lights(
    *plants: dict[str, Any],
    lights: list[dict[str, Any]] | None = None,
    lux: list[dict[str, Any]] | None = None,
):
    doc = schema()(
        {
            "lights": lights if lights is not None else [STUDY_LIGHT],
            "lux_sensors": lux if lux is not None else [STUDY_LUX],
            "plants": list(plants),
        }
    )
    return parse(doc)


class TestFixtureReferences:
    """The generator has already checked all of this. A failure here therefore
    means the two have diverged, which is worth refusing to start over."""

    def test_fixtures_resolve_by_name(self) -> None:
        config = load_lights(LIT_PLANT)
        (plant,) = config.plants
        (fixture,) = config.fixtures_for(plant)
        assert fixture.switch_entity == "switch.nick_study_outlet_sansi_100w_lamp"
        assert config.lux("study_shelf").entities == (
            "sensor.esphome_study_lux_1",
            "sensor.esphome_study_lux_2",
        )

    def test_an_unknown_light_fixture_is_rejected_by_name(self) -> None:
        with pytest.raises(InvalidPlantConfig, match="greenhouse"):
            load_lights({**LIT_PLANT, "lights": ["greenhouse"]})

    def test_an_unknown_lux_fixture_is_rejected_by_name(self) -> None:
        with pytest.raises(InvalidPlantConfig, match="hallway"):
            load_lights({**LIT_PLANT, "lux": "hallway"})

    def test_an_objective_nothing_measures_is_rejected(self) -> None:
        """A DLI budget with no lux fixture would sit at zero for ever and burn
        continuously, which looks exactly like a plant in a cupboard."""
        plant = {k: v for k, v in LIT_PLANT.items() if k != "lux"}
        with pytest.raises(InvalidPlantConfig, match="nothing would measure"):
            load_lights(plant)

    def test_a_mixed_plant_under_a_lamp_with_no_factor_is_rejected(self) -> None:
        """Its lux reading is sun for part of the day and lamp for the rest. A
        lamp that cannot say what its own light is worth makes every DLI figure
        for this plant wrong, quietly, in a direction nobody can guess."""
        no_factor = {k: v for k, v in STUDY_LIGHT.items() if k != "lux_to_ppfd"}
        with pytest.raises(InvalidPlantConfig, match="lux_to_ppfd"):
            load_lights(LIT_PLANT, lights=[no_factor])

    def test_a_sun_only_plant_needs_no_lamp_factor(self) -> None:
        """Derived, not configured: nothing is switching, so one factor serves
        the whole day and the requirement does not apply."""
        no_factor = {k: v for k, v in STUDY_LIGHT.items() if k != "lux_to_ppfd"}
        sun_only = {k: v for k, v in LIT_PLANT.items() if k != "lights"}
        config = load_lights(sun_only, lights=[no_factor])
        assert config.plants[0].light_source is Lit.SUN
        assert not config.plants[0].is_mixed_light


class TestWindowParsing:
    def test_an_awake_aware_window_carries_its_presence_entity(self) -> None:
        config = load_lights(LIT_PLANT)
        window = config.light("study_shelf").window
        assert isinstance(window, AwakeAwareWindow)
        assert window.presence_entity == "sensor.person_presence_nick"
        assert window.on_if_awake_after == time(6, 0)
        assert window.on_until == time(19, 0)

    def test_a_fixed_window_has_no_presence_at_all(self) -> None:
        fixed = {
            **STUDY_LIGHT,
            "window": {"mode": "fixed", "from": "07:00", "to": "19:00"},
        }
        config = load_lights(LIT_PLANT, lights=[fixed])
        window = config.light("study_shelf").window
        assert isinstance(window, FixedWindow)
        assert window.start == time(7, 0)
        assert window.days is None  # every day

    def test_a_half_written_window_names_the_fixture(self) -> None:
        """These messages reach a human reading the log, and "missing notBefore"
        without saying which fixture is most of the way to useless."""
        broken = {**STUDY_LIGHT, "window": {"mode": "awake_aware", "on_until": "19:00"}}
        with pytest.raises(InvalidPlantConfig, match="study_shelf"):
            load_lights(LIT_PLANT, lights=[broken])

    def test_an_unknown_window_mode_is_rejected(self) -> None:
        broken = {**STUDY_LIGHT, "window": {"mode": "auto", "on_until": "19:00"}}
        with pytest.raises(vol.Invalid):
            load_lights(LIT_PLANT, lights=[broken])

    def test_a_malformed_time_is_rejected(self) -> None:
        broken = {
            **STUDY_LIGHT,
            "window": {"mode": "fixed", "from": "seven", "to": "19:00"},
        }
        with pytest.raises(vol.Invalid):
            load_lights(LIT_PLANT, lights=[broken])

    def test_days_are_parsed_into_the_enum_not_left_as_strings(self) -> None:
        weekend = {
            **STUDY_LIGHT,
            "window": {
                "mode": "fixed",
                "from": "07:00",
                "to": "19:00",
                "days": ["sat", "sun"],
            },
        }
        config = load_lights(LIT_PLANT, lights=[weekend])
        assert config.light("study_shelf").window.days == frozenset(
            {Weekday.SAT, Weekday.SUN}
        )

    def test_a_day_that_is_not_a_weekday_is_rejected(self) -> None:
        broken = {
            **STUDY_LIGHT,
            "window": {
                "mode": "fixed",
                "from": "07:00",
                "to": "19:00",
                "days": ["caturday"],
            },
        }
        with pytest.raises(vol.Invalid):
            load_lights(LIT_PLANT, lights=[broken])


class TestDliParsing:
    def test_the_objective_is_carried_verbatim(self) -> None:
        (plant,) = load_lights(LIT_PLANT).plants
        assert plant.dli.category == "foliage_tropical"
        assert plant.dli.preferred.low == 4.0
        assert plant.dli.survival.high == 20.0
        assert plant.dli.budget == 20.0

    def test_survival_is_optional(self) -> None:
        dli = {k: v for k, v in DLI.items() if k != "survival"}
        (plant,) = load_lights({**LIT_PLANT, "dli": dli}).plants
        assert plant.dli.survival is None

    def test_an_inverted_band_names_the_plant(self) -> None:
        dli = {**DLI, "preferred": {"low": 9.0, "high": 4.0}}
        with pytest.raises(InvalidPlantConfig, match="monstera"):
            load_lights({**LIT_PLANT, "dli": dli})

    def test_a_preferred_band_outside_survival_names_the_plant(self) -> None:
        dli = {**DLI, "survival": {"low": 5.0, "high": 8.0}}
        with pytest.raises(InvalidPlantConfig, match="monstera"):
            load_lights({**LIT_PLANT, "dli": dli})

    def test_a_zero_budget_is_rejected(self) -> None:
        with pytest.raises(vol.Invalid):
            load_lights({**LIT_PLANT, "dli": {**DLI, "budget": 0}})

    def test_the_light_source_is_derived_not_read(self) -> None:
        """It is a fact about `lights` and `lux`, both of which are already in
        the document. Sending it too would be a second spelling of what we have,
        and the generator's copy of it was in fact wrong — it could never say
        `grow`. So the schema refuses the key outright."""
        with pytest.raises(vol.Invalid):
            load_lights({**LIT_PLANT, "light_source": "mixed"})

        (lit,) = load_lights(LIT_PLANT).plants
        assert lit.light_source is Lit.MIXED

        grow_only = {k: v for k, v in LIT_PLANT.items() if k not in ("lux", "dli")}
        (grow,) = load_lights(grow_only).plants
        assert grow.light_source is Lit.GROW

        (bare,) = load_lights({"name": "pot", "display": "Pot"}).plants
        assert bare.light_source is None
