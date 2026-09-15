"""Every entity id this component creates.

One module so the names exist in exactly one place. The previous attempt at this
system built ids inline with `f"plant_{slug}_state"` scattered across the
generator, which meant the same string was written from memory in several files
and nothing would catch a mismatch between the thing that created an entity and
the thing that read it.

Here a caller asks for `attention(plant)` and cannot spell it wrong.
"""

from __future__ import annotations

from .plant import CareTask, Domain, Entity, Plant

PREFIX = "plant"


def _name(plant: Plant, suffix: str) -> str:
    return f"{PREFIX}_{plant.name}_{suffix}"


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
    """The button. Exists only for tasks nothing can auto-detect — which the
    generator has already enforced by refusing to schedule a detectable task."""
    return Entity(Domain.BUTTON, _name(plant, f"{task.task}_done"))


# ---- The feed -----------------------------------------------------------


def attention(plant: Plant) -> Entity:
    """Count of everything this plant needs, with the list as an attribute."""
    return Entity(Domain.SENSOR, _name(plant, "attention"))


def outstanding() -> Entity:
    """Everything outstanding, across every plant. The integration point."""
    return Entity(Domain.SENSOR, f"{PREFIX}_outstanding")
