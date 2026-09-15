"""Parsing the resolved document.

The theme throughout: this document is machine-generated, so anything surprising
means the generator and this component have diverged. Every test here asserts
that such a divergence is loud.
"""

from __future__ import annotations

from typing import Any

import pytest
import voluptuous as vol

from custom_components.plant_care.model import (
    Calibrated,
    Calibrating,
    InvalidPlantConfig,
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
        "source": "roam.sensors.moisture_1",
        "entities": {
            "moisture": "sensor.roam_sensor_moisture_1_soil_moisture",
            "temperature": "sensor.roam_sensor_moisture_1_temperature",
            "battery": "sensor.roam_sensor_moisture_1_battery",
        },
        "probe": {"heartbeatMinutes": 10, "deadbandPp": 1.0},
        "calibration": {"fieldCapacity": 79.54, "dryPoint": 53.18, "fcTolerance": 8.0},
    },
    "care": [
        {"task": "feed", "display": "Feed", "icon": "mdi:nutrition", "everyDays": 14}
    ],
}

CALIBRATING_PLANT: dict[str, Any] = {
    "name": "monstera",
    "display": "Monstera",
    "moisture": {
        "source": "nick_study.sensors.monstera_window",
        "entities": {
            "moisture": "sensor.nick_study_sensor_monstera_window_soil_moisture"
        },
        "probe": {"heartbeatMinutes": 10, "deadbandPp": 1.0},
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
            "everyDays": 4,
        }
    ],
}


def load(*plants: dict[str, Any]):
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
                "calibration": {"fieldCapacity": 79.54, "dryPoint": 53.18},
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
                "calibration": {"fieldCapacity": 79.54},
            },
        }
        with pytest.raises(vol.Invalid):
            load(plant_data)

    def test_an_inverted_calibration_is_rejected_by_name(self) -> None:
        plant_data = {
            **CALIBRATED_PLANT,
            "moisture": {
                **CALIBRATED_PLANT["moisture"],
                "calibration": {"fieldCapacity": 50.0, "dryPoint": 60.0},
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
                "probe": {"heartbeatMinutes": 0, "deadbandPp": 1.0},
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
                    "care": [{**SENSORLESS_PLANT["care"][0], "everyDays": 0}],
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
