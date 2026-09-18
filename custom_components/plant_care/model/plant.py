"""The plant domain model.

Deliberately free of any Home Assistant import. Everything here is ordinary
Python with real types, so it can be unit tested without mocking a framework and
the type hints actually mean something — Home Assistant's own typing is loose
enough that letting it leak in here would erode both.

The Home Assistant edge lives in the platform modules; this is what they act on.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .dli import DliObjective
from .light import Lit


class Domain(Enum):
    """Home Assistant entity domains this component creates entities in."""

    SENSOR = "sensor"
    BINARY_SENSOR = "binary_sensor"
    BUTTON = "button"
    SWITCH = "switch"


@dataclass(frozen=True)
class Entity:
    """An entity id this component owns.

    Built from a domain and a name rather than a string, so an id can never be
    half-written or assembled in two different ways in two places.
    """

    domain: Domain
    name: str

    @property
    def full(self) -> str:
        return f"{self.domain.value}.{self.name}"


@dataclass(frozen=True)
class SourceEntity:
    """An entity id belonging to something else — a zigbee probe, say.

    A distinct type from [`Entity`] on purpose: this component reads these and
    must never invent one. They arrive already resolved, derived by the
    generator from the device inventory, so nothing here has to guess how
    zigbee2mqtt named a device.
    """

    entity_id: str


@dataclass(frozen=True)
class ProbeFacts:
    """What the hardware does. Facts, not decisions.

    Windows and thresholds are derived from these by [`Policy`]; keeping the two
    apart is what lets a different probe model work without touching policy.
    """

    heartbeat_minutes: int
    deadband_pp: float

    def __post_init__(self) -> None:
        if self.heartbeat_minutes <= 0:
            raise ValueError(
                f"heartbeatMinutes must be positive, got {self.heartbeat_minutes}"
            )
        if self.deadband_pp < 0:
            raise ValueError(f"deadbandPp cannot be negative, got {self.deadband_pp}")


@dataclass(frozen=True)
class Calibrating:
    """The plant is being calibrated: it can be monitored, but not judged.

    An explicit type rather than `None`, because the difference between "no
    calibration was measured" and "there is no moisture source" is the whole
    reason the input format refuses to accept a partial calibration. A `None`
    here would be falsy, and the first `if plant.calibration:` anyone writes
    would silently treat a calibrating plant as one with no probe at all.
    """


@dataclass(frozen=True)
class Calibrated:
    """Both measured endpoints of a wet/dry cycle, exactly as recorded.

    `fc_tolerance` stays `None` when it was never set. The default is applied at
    the point of use, so "5 was chosen" and "nobody said" remain distinguishable
    all the way through.
    """

    field_capacity: float
    dry_point: float
    fc_tolerance: float | None = None

    def __post_init__(self) -> None:
        if self.field_capacity <= self.dry_point:
            raise ValueError(
                f"fieldCapacity {self.field_capacity} must be above "
                f"dryPoint {self.dry_point}: a wetter reading is the higher number"
            )

    @property
    def span(self) -> float:
        """Wet-to-dry span, the unit thresholds are expressed as a fraction of."""
        return self.field_capacity - self.dry_point

    def available_water(self, raw: float) -> float:
        """Raw reading as a percentage of available water.

        0% at the measured dry point, 100% at field capacity. Decisions are made
        on this scale because raw capacitive readings are not portable between
        pots — they depend on soil mix, density and probe contact, which is why
        one pot reads 53-80 where a species reference assumes 20-60.
        """
        return (raw - self.dry_point) / self.span * 100.0

    def raw_at(self, available_water_pct: float) -> float:
        """The raw reading corresponding to a percentage of available water."""
        return self.dry_point + self.span * available_water_pct / 100.0


Calibration = Calibrated | Calibrating
"""Either measured endpoints, or an explicit statement that none exist yet."""


@dataclass(frozen=True)
class Moisture:
    """A plant's soil probe, already resolved to entity ids.

    There is no reference back to the device it came from. The generator names
    devices by a `{room, name}` path into an inventory this component cannot
    see, so carrying one here would be an identifier nothing on this side could
    resolve — and the entity ids already encode the room, the device type and
    the name.
    """

    moisture_entity: SourceEntity
    temperature_entity: SourceEntity | None
    battery_entity: SourceEntity | None
    probe: ProbeFacts
    calibration: Calibration

    @property
    def is_calibrated(self) -> bool:
        return isinstance(self.calibration, Calibrated)


@dataclass(frozen=True)
class CareTask:
    """A recurring job no sensor can detect, so it needs a manual mark-done."""

    task: str
    display: str
    icon: str
    every_days: int

    def __post_init__(self) -> None:
        if self.every_days <= 0:
            raise ValueError(
                f"everyDays must be positive for '{self.task}', got {self.every_days}"
            )

    def is_overdue(self, days_since: float | None) -> bool:
        """Whether the task is due.

        `None` means never done, which is deliberately **not** overdue: adding a
        plant should not immediately produce a backlog, it should start counting
        from the first time the task is marked done.
        """
        if days_since is None:
            return False
        return days_since >= self.every_days


@dataclass(frozen=True)
class Plant:
    """One plant, as resolved by the generator."""

    name: str
    display: str
    species: str | None
    moisture: Moisture | None
    care: tuple[CareTask, ...]

    lights: tuple[str, ...] = ()
    """Fixture names. The fixtures themselves live once on the config, not
    repeated per plant."""

    lux: str | None = None
    """The lux fixture measuring this plant, by name."""

    dli: DliObjective | None = None
    """Only ever set alongside `lux` — an objective nothing measures is checked
    at parse time, not tolerated."""

    @property
    def light_source(self) -> Lit | None:
        """Where this plant's light comes from.

        Derived here rather than read from the document, because it is a fact
        about `lights` and `lux` and the document already carries both. Sending
        it as well would be a second spelling of something we have, and two
        spellings can disagree — as they did: the generator's version of this
        could never produce `GROW`, and nothing noticed, because nothing else
        had reason to.

        `None` when nothing knows anything about this plant's light: no fixture
        over it and no sensor on it.
        """
        if not self.lights:
            return Lit.SUN if self.lux else None
        return Lit.MIXED if self.lux else Lit.GROW

    @property
    def is_mixed_light(self) -> bool:
        """Sun and lamp together, so one lux→PPFD scalar will not do."""
        return self.light_source is Lit.MIXED

    def care_task(self, task: str) -> CareTask | None:
        for entry in self.care:
            if entry.task == task:
                return entry
        return None
