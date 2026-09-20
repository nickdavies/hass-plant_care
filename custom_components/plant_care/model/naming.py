"""Every entity id this component creates.

One module so the names exist in exactly one place. An id built inline with an
f-string in several files is written from memory each time, and nothing catches
a mismatch between the thing that creates an entity and the thing that reads it.

Here a caller asks for `attention(plant)` and cannot spell it wrong.
"""

from __future__ import annotations

from .light import LightFixture, LuxFixture
from .plant import CareTask, Domain, Entity, Plant

PREFIX = "plant"


def _name(plant: Plant, suffix: str) -> str:
    return f"{PREFIX}_{plant.name}_{suffix}"


def _fixture_name(fixture: LightFixture | LuxFixture, suffix: str) -> str:
    return f"{PREFIX}_light_{fixture.name}_{suffix}"


# ---- Moisture signal layer ---------------------------------------------


def moisture_smoothed(plant: Plant) -> Entity:
    """Median over the policy window. What threshold crossings are measured
    against, so a single bad read cannot flag a plant."""
    return Entity(Domain.SENSOR, _name(plant, "moisture_smoothed"))


def moisture_min(plant: Plant) -> Entity:
    """Trailing minimum, used only to derive the rise below."""
    return Entity(Domain.SENSOR, _name(plant, "moisture_min"))


def moisture_rise(plant: Plant) -> Entity:
    """How far the raw reading has risen above its own trailing minimum.

    This is the watering detector, and it is why there is no "I watered it"
    button: a watering by anyone, with no phone anywhere near it, still shows up
    here.
    """
    return Entity(Domain.SENSOR, _name(plant, "moisture_rise"))


def available_water(plant: Plant) -> Entity:
    """Percentage of available water remaining. Only exists once calibrated —
    without both endpoints there is no scale to express it on."""
    return Entity(Domain.SENSOR, _name(plant, "available_water"))


def days_since_watered(plant: Plant) -> Entity:
    return Entity(Domain.SENSOR, _name(plant, "days_since_watered"))


def needs_water(plant: Plant) -> Entity:
    return Entity(Domain.BINARY_SENSOR, _name(plant, "needs_water"))


# ---- Care tasks ---------------------------------------------------------


def care_days_since(plant: Plant, task: CareTask) -> Entity:
    return Entity(Domain.SENSOR, _name(plant, f"{task.task}_days_since"))


def care_due(plant: Plant, task: CareTask) -> Entity:
    return Entity(Domain.BINARY_SENSOR, _name(plant, f"{task.task}_due"))


def care_done(plant: Plant, task: CareTask) -> Entity:
    """The button. Exists only for tasks nothing can auto-detect — which
    parsing has already enforced by refusing to schedule a detectable task."""
    return Entity(Domain.BUTTON, _name(plant, f"{task.task}_done"))


# ---- Lights and light measurement ---------------------------------------


def light_killswitch(fixture: LightFixture) -> Entity:
    """Freezes this fixture's automation. Mirrors `switch.killswitch_motion_*`
    from light_motion_profiles, deliberately: one gesture for "stop automating
    this thing", whichever system owns it."""
    return Entity(Domain.SWITCH, f"{PREFIX}_light_killswitch_{fixture.name}")


def light_on_minutes(fixture: LightFixture) -> Entity:
    """Minutes this fixture has actually been on today, against what its window
    allows. The outcome, not the intent — which is the only thing that catches a
    dead bulb behind a live outlet."""
    return Entity(Domain.SENSOR, _fixture_name(fixture, "on_minutes"))


def lux_average(fixture: LuxFixture) -> Entity:
    """The fixture's members, averaged. Belongs to the fixture rather than a
    plant because several plants share one reading."""
    return Entity(Domain.SENSOR, f"{PREFIX}_lux_{fixture.name}")


def dli_today(plant: Plant) -> Entity:
    """Light accumulated so far today, mol/m². Resets at local midnight."""
    return Entity(Domain.SENSOR, _name(plant, "dli_today"))


# ---- The feed -----------------------------------------------------------


def attention(plant: Plant) -> Entity:
    """Count of everything this plant needs, with the list as an attribute."""
    return Entity(Domain.SENSOR, _name(plant, "attention"))


def outstanding() -> Entity:
    """Everything outstanding, across every plant. The integration point."""
    return Entity(Domain.SENSOR, f"{PREFIX}_outstanding")


def person_outstanding(person: str) -> Entity:
    """One person's plants, owned outright or through a group."""
    return Entity(Domain.SENSOR, f"{PREFIX}_outstanding_{person}")
