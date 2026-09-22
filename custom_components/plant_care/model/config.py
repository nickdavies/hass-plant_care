"""Parsing the `plant_care:` config into the model.

Three layers, all reached from `CONFIG_SCHEMA`, so that `check_config` — and
therefore the CI of the repo holding the config — runs every one of them:

1. `schema()` — a voluptuous schema for the shape. Unknown keys are rejected
   everywhere (`PREVENT_EXTRA` is the default): a typo is an error, not a
   silently ignored field.
2. `__post_init__` on the model dataclasses — value invariants. These live on
   the type so that no second construction path can skip them: a field
   capacity below its dry point, a band with low above high, a window that
   runs backwards.
3. `parse()` — cross-references. Every name is resolved against its table, and
   every reference that would otherwise fail quietly at runtime is refused here
   with a message naming the plant or fixture.

The config has four lookup tables — probe models, care tasks, DLI categories,
owners — so a fact is written once and referenced by name. Everything a plant
names outside this file is a Home Assistant entity id, written explicitly:
nothing is derived from a naming convention that another integration might not
share.

Uses plain voluptuous rather than `homeassistant.helpers.config_validation`, so
this module stays importable without Home Assistant and its tests need no mocks.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import time
from typing import Any

import voluptuous as vol

from .dli import Band, DliObjective
from .light import (
    AwakeAwareWindow,
    BinarySensorMatcher,
    FixedWindow,
    LightFixture,
    LightWindow,
    LuxFixture,
    PresenceMatcher,
    QuietMatcher,
    Weekday,
)
from .owners import Owner, Owners
from .plant import (
    Calibrated,
    Calibrating,
    Calibration,
    CareTask,
    Moisture,
    Plant,
    ProbeFacts,
    SourceEntity,
)

# Field names, named once. A typo in a key then breaks the schema rather than
# quietly reading a field that is always absent.

FIELD_PROBE_MODELS = "probe_models"
FIELD_CARE_TASKS = "care_tasks"
FIELD_DLI_CATEGORIES = "dli_categories"
FIELD_LIGHTS = "lights"
FIELD_LUX_SENSORS = "lux_sensors"
FIELD_PLANTS = "plants"
FIELD_OWNERS = "owners"
FIELD_GROUPS = "groups"
FIELD_SYSTEM_NOTIFY = "system_notify"
FIELD_OWNER = "owner"
FIELD_ACTION = "action"
RESERVED_OWNER_ALL = "all"
"""The dashboard's shared tab path, so not an owner."""

FIELD_NAME = "name"
FIELD_DISPLAY = "display"
FIELD_ICON = "icon"
FIELD_ENTITIES = "entities"

FIELD_HEARTBEAT_MINUTES = "heartbeat_minutes"
FIELD_DEADBAND_PP = "deadband_pp"

FIELD_DETECTED_BY = "detected_by"
DETECTED_BY_CALIBRATED_MOISTURE = "calibrated_moisture"

FIELD_LOW = "low"
FIELD_HIGH = "high"

FIELD_SWITCH = "switch"
FIELD_LUX_TO_PPFD = "lux_to_ppfd"
FIELD_WINDOW = "window"
FIELD_FIXED = "fixed"
FIELD_AWAKE_AWARE = "awake_aware"
FIELD_DAYS = "days"
FIELD_FROM = "from"
FIELD_TO = "to"
FIELD_PRESENCE = "presence"
FIELD_QUIET_WHEN = "quiet_when"
FIELD_BINARY_SENSOR = "binary_sensor"
FIELD_ON_IF_AWAKE_AFTER = "on_if_awake_after"
FIELD_ON_AFTER = "on_after"
FIELD_ON_EVEN_IF_ASLEEP_UNTIL = "on_even_if_asleep_until"
FIELD_ON_UNTIL = "on_until"

FIELD_SUN_LUX_TO_PPFD = "sun_lux_to_ppfd"
DEFAULT_SUN_LUX_TO_PPFD = 0.0185
"""The commonly cited daylight lux→PPFD figure. Override with a measurement."""

FIELD_SPECIES = "species"
FIELD_MOISTURE = "moisture"
FIELD_MODEL = "model"
FIELD_CALIBRATION = "calibration"
FIELD_CARE = "care"
FIELD_LUX = "lux"
FIELD_DLI = "dli"

FIELD_ENTITY_MOISTURE = "moisture"
FIELD_ENTITY_TEMPERATURE = "temperature"
FIELD_ENTITY_BATTERY = "battery"

FIELD_FIELD_CAPACITY = "field_capacity"
FIELD_DRY_POINT = "dry_point"
FIELD_FC_TOLERANCE = "fc_tolerance"
FIELD_WATERLOGGED_BUDGET_PCT = "waterlogged_budget_pct"
CALIBRATING = "calibrating"

FIELD_TASK = "task"
FIELD_EVERY_DAYS = "every_days"

FIELD_CATEGORY = "category"
FIELD_PREFERRED = "preferred"
FIELD_SURVIVAL = "survival"
FIELD_WINDOW_DAYS = "window_days"
FIELD_BUDGET = "budget"


class InvalidPlantConfig(vol.Invalid):
    """Raised when the config cannot be turned into a model.

    A `vol.Invalid`, so that raising it from inside `CONFIG_SCHEMA` reports the
    way any other config error does. Carries the plant or fixture name wherever
    one is known: these messages reach a human reading Home Assistant's log,
    and "unknown task" without saying which plant is most of the way to useless
    in a file with a dozen in it.
    """


# ---- Shape --------------------------------------------------------------

_SLUG = re.compile(r"^[a-z0-9][a-z0-9_]*$")
_ENTITY_ID = re.compile(r"^[a-z][a-z0-9_]*\.[a-z0-9_]+$")
_MDI_ICON = re.compile(r"^mdi:[a-z0-9]+(-[a-z0-9]+)*$")


def _slug(value: Any) -> str:
    """A lowercase identifier. Entity ids are built from these, so the
    character set is what Home Assistant allows in an object id."""
    if not isinstance(value, str) or not _SLUG.match(value):
        raise vol.Invalid(
            f"expected a lowercase identifier like 'ficus_alii', got {value!r}"
        )
    return value


def _entity_id(value: Any) -> str:
    if not isinstance(value, str) or not _ENTITY_ID.match(value):
        raise vol.Invalid(f"expected an entity id like 'sensor.foo', got {value!r}")
    return value


_NOTIFY_ACTION = re.compile(r"^notify\.[a-z0-9_]+$")


def _notify_action(value: Any) -> str:
    """A notify action, `notify.<target>`. Whether the target exists is, like
    an entity id, something only a running Home Assistant can say."""
    if not isinstance(value, str) or not _NOTIFY_ACTION.match(value):
        raise vol.Invalid(f"expected a notify action like 'notify.nick', got {value!r}")
    return value


def _mdi_icon(value: Any) -> str:
    """An icon Home Assistant cannot resolve renders as a blank square, so a
    dashboard with `mdi-nutrition` on it looks unfinished rather than broken."""
    if not isinstance(value, str) or not _MDI_ICON.match(value):
        raise vol.Invalid(f"expected an icon like 'mdi:watering-can', got {value!r}")
    return value


def _display(value: Any) -> str:
    """Prose, so anything goes — except blank or padded, both of which render
    as a card with no readable title and look like an error nowhere."""
    if not isinstance(value, str) or not value.strip():
        raise vol.Invalid("display name may not be blank")
    if value.strip() != value:
        raise vol.Invalid(f"display name {value!r} has leading or trailing whitespace")
    return value


def _time_of_day(value: Any) -> time:
    if not isinstance(value, str):
        # YAML reads an unquoted `06:00` as the sexagesimal integer 360, which
        # is the single most likely way a time ends up not being a string.
        raise vol.Invalid(
            f'expected a quoted HH:MM time, got {value!r} (quote it: "06:00")'
        )
    try:
        hours, minutes = value.split(":")
        if len(hours) != 2 or len(minutes) != 2:
            raise ValueError
        return time(hour=int(hours), minute=int(minutes))
    except (ValueError, TypeError) as err:
        raise vol.Invalid(f"{value!r} is not a zero-padded HH:MM time") from err


def _weekday(value: Any) -> Weekday:
    try:
        return Weekday(value)
    except ValueError as err:
        raise vol.Invalid(f"{value!r} is not a weekday like 'mon'") from err


_POSITIVE_INT = vol.All(int, vol.Range(min=1))
_POSITIVE_FLOAT = vol.All(vol.Coerce(float), vol.Range(min=0.0, min_included=False))
_NON_NEGATIVE_FLOAT = vol.All(vol.Coerce(float), vol.Range(min=0.0))


def _probe_model_schema() -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(FIELD_HEARTBEAT_MINUTES): _POSITIVE_INT,
            vol.Required(FIELD_DEADBAND_PP): _NON_NEGATIVE_FLOAT,
        }
    )


def _care_task_def_schema() -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(FIELD_DISPLAY): _display,
            vol.Required(FIELD_ICON): _mdi_icon,
            vol.Optional(FIELD_DETECTED_BY): vol.In([DETECTED_BY_CALIBRATED_MOISTURE]),
        }
    )


def _owner_schema() -> vol.Schema:
    """`icon` is optional and people-only; the group case is refused in
    `Owners.__post_init__`, where whether a name is a group is known."""
    return vol.Schema(
        {
            vol.Required(FIELD_ACTION): _notify_action,
            vol.Optional(FIELD_ICON): _mdi_icon,
        }
    )


def _band_schema() -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(FIELD_LOW): vol.Coerce(float),
            vol.Required(FIELD_HIGH): vol.Coerce(float),
        }
    )


def _fixed_window_schema() -> vol.Schema:
    return vol.Schema(
        {
            vol.Optional(FIELD_DAYS): vol.All([_weekday], vol.Length(min=1)),
            vol.Required(FIELD_FROM): _time_of_day,
            vol.Required(FIELD_TO): _time_of_day,
        }
    )


def _exactly_one_matcher(value: Mapping[str, Any]) -> Mapping[str, Any]:
    if not value:
        raise vol.Invalid(
            f"{FIELD_QUIET_WHEN} must be one of '{FIELD_PRESENCE}' or "
            f"'{FIELD_BINARY_SENSOR}'"
        )
    return value


def _quiet_when_schema() -> vol.Schema:
    # Another one-key sum type, for the same reason as the window: each matcher
    # kind reads its entity differently, so the kind has to be stated.
    msg = "a quiet_when names exactly one matcher"
    return vol.All(
        vol.Schema(
            {
                vol.Exclusive(FIELD_PRESENCE, "matcher", msg=msg): _entity_id,
                vol.Exclusive(FIELD_BINARY_SENSOR, "matcher", msg=msg): _entity_id,
            }
        ),
        _exactly_one_matcher,
    )


def _whose_sleep_is_stated(value: Mapping[str, Any]) -> Mapping[str, Any]:
    if FIELD_PRESENCE not in value and FIELD_QUIET_WHEN not in value:
        raise vol.Invalid(
            f"an awake_aware window needs '{FIELD_PRESENCE}' or "
            f"'{FIELD_QUIET_WHEN}': whose sleep it answers to"
        )
    return value


def _awake_aware_window_schema() -> vol.Schema:
    # Whose sleep lives here and only here: a fixed window has nowhere to put
    # it, so a fixture cannot claim sleep-sensitivity without saying whose
    # sleep, and cannot name a sleeper it will never consult.
    #
    # `presence` is shorthand for `quiet_when: {presence: ...}` — the case that
    # needs no other component's rules — so it stays the short spelling.
    msg = f"'{FIELD_PRESENCE}' is shorthand for a '{FIELD_QUIET_WHEN}'; give one"
    return vol.All(
        vol.Schema(
            {
                vol.Optional(FIELD_DAYS): vol.All([_weekday], vol.Length(min=1)),
                vol.Exclusive(FIELD_PRESENCE, "sleepers", msg=msg): _entity_id,
                vol.Exclusive(
                    FIELD_QUIET_WHEN, "sleepers", msg=msg
                ): _quiet_when_schema(),
                vol.Required(FIELD_ON_IF_AWAKE_AFTER): _time_of_day,
                vol.Required(FIELD_ON_AFTER): _time_of_day,
                vol.Required(FIELD_ON_EVEN_IF_ASLEEP_UNTIL): _time_of_day,
                vol.Required(FIELD_ON_UNTIL): _time_of_day,
            }
        ),
        _whose_sleep_is_stated,
    )


def _exactly_one_shape(value: Mapping[str, Any]) -> Mapping[str, Any]:
    if not value:
        raise vol.Invalid(
            f"window must be one of '{FIELD_FIXED}' or '{FIELD_AWAKE_AWARE}'"
        )
    return value


def _window_schema() -> vol.Schema:
    # A sum type spelled as a one-key map. `Exclusive` refuses both; the
    # callable refuses neither.
    return vol.All(
        vol.Schema(
            {
                vol.Exclusive(
                    FIELD_FIXED,
                    "shape",
                    msg="a fixture is either sleep-sensitive or it is not",
                ): _fixed_window_schema(),
                vol.Exclusive(
                    FIELD_AWAKE_AWARE,
                    "shape",
                    msg="a fixture is either sleep-sensitive or it is not",
                ): _awake_aware_window_schema(),
            }
        ),
        _exactly_one_shape,
    )


def _light_schema() -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(FIELD_NAME): _slug,
            vol.Required(FIELD_SWITCH): _entity_id,
            vol.Optional(FIELD_LUX_TO_PPFD): _POSITIVE_FLOAT,
            vol.Required(FIELD_WINDOW): _window_schema(),
        }
    )


def _lux_schema() -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(FIELD_NAME): _slug,
            vol.Required(FIELD_ENTITIES): vol.All([_entity_id], vol.Length(min=1)),
            vol.Optional(
                FIELD_SUN_LUX_TO_PPFD, default=DEFAULT_SUN_LUX_TO_PPFD
            ): _POSITIVE_FLOAT,
        }
    )


def _entities_schema() -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(FIELD_ENTITY_MOISTURE): _entity_id,
            vol.Optional(FIELD_ENTITY_TEMPERATURE): _entity_id,
            vol.Optional(FIELD_ENTITY_BATTERY): _entity_id,
        }
    )


def _calibration_schema() -> vol.Schema:
    # Both endpoints required together: a calibration with only one is the
    # precise thing this format exists to refuse, because a threshold derived
    # from one endpoint is a fabricated number.
    return vol.Schema(
        {
            vol.Required(FIELD_FIELD_CAPACITY): vol.Coerce(float),
            vol.Required(FIELD_DRY_POINT): vol.Coerce(float),
            vol.Optional(FIELD_FC_TOLERANCE): vol.Coerce(float),
            vol.Optional(FIELD_WATERLOGGED_BUDGET_PCT): vol.All(
                vol.Coerce(float), vol.Range(min=0.0, min_included=False, max=100.0)
            ),
        }
    )


def _calibration(value: Any) -> Any:
    """`calibrating`, or a map of the measured endpoints. Nothing else.

    Hand-written rather than `vol.Any`, so the failure that matters most — a
    half-written calibration — says what was expected instead of "not a valid
    value".
    """
    if value == CALIBRATING:
        return value
    if isinstance(value, Mapping):
        return _calibration_schema()(value)
    raise vol.Invalid(
        f"calibration must be '{CALIBRATING}', or a map with "
        f"{FIELD_FIELD_CAPACITY} and {FIELD_DRY_POINT}"
    )


def _moisture_schema() -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(FIELD_MODEL): str,
            vol.Required(FIELD_ENTITIES): _entities_schema(),
            # Required, not defaulted: a plant with a probe has to say whether
            # it has been calibrated. Absence would be ambiguous between "not
            # yet" and "forgot".
            vol.Required(FIELD_CALIBRATION): _calibration,
        }
    )


def _care_schema() -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(FIELD_TASK): str,
            vol.Required(FIELD_EVERY_DAYS): _POSITIVE_INT,
        }
    )


def _dli_schema() -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(FIELD_CATEGORY): str,
            vol.Optional(FIELD_PREFERRED): _band_schema(),
            vol.Optional(FIELD_SURVIVAL): _band_schema(),
            vol.Optional(FIELD_WINDOW_DAYS): _POSITIVE_INT,
            vol.Optional(FIELD_BUDGET): _POSITIVE_FLOAT,
        }
    )


def _plant_schema() -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(FIELD_NAME): _slug,
            vol.Optional(FIELD_DISPLAY): _display,
            vol.Optional(FIELD_SPECIES): str,
            vol.Required(FIELD_OWNER): str,
            vol.Optional(FIELD_MOISTURE): _moisture_schema(),
            vol.Optional(FIELD_CARE): [_care_schema()],
            vol.Optional(FIELD_LIGHTS): [str],
            vol.Optional(FIELD_LUX): str,
            vol.Optional(FIELD_DLI): _dli_schema(),
        }
    )


def schema() -> vol.Schema:
    """The whole `plant_care:` block.

    Table keys are checked as identifiers in `parse` rather than here, so a
    bad one is reported by name instead of as "extra keys not allowed".
    """
    return vol.Schema(
        {
            vol.Optional(FIELD_PROBE_MODELS, default={}): {str: _probe_model_schema()},
            vol.Optional(FIELD_CARE_TASKS, default={}): {str: _care_task_def_schema()},
            vol.Optional(FIELD_DLI_CATEGORIES, default={}): {str: _band_schema()},
            vol.Optional(FIELD_LIGHTS, default=[]): [_light_schema()],
            vol.Optional(FIELD_LUX_SENSORS, default=[]): [_lux_schema()],
            vol.Required(FIELD_PLANTS): [_plant_schema()],
            vol.Optional(FIELD_OWNERS, default={}): {str: _owner_schema()},
            # The shared household file; members are checked only for groups
            # that `owners` names.
            vol.Optional(FIELD_GROUPS, default={}): {str: [str]},
            vol.Required(FIELD_SYSTEM_NOTIFY): _notify_action,
        }
    )


# ---- Tables -------------------------------------------------------------


@dataclass(frozen=True)
class CareTaskDef:
    """One row of `care_tasks`: what a task is called, and whether something
    already detects it happening."""

    display: str
    icon: str
    detected_by: str | None


def _table_key(table: str, key: str) -> str:
    try:
        return _slug(key)
    except vol.Invalid as err:
        raise InvalidPlantConfig(f"{table} key {key!r}: {err.msg}") from err


def _parse_probe_models(data: Mapping[str, Any]) -> dict[str, ProbeFacts]:
    models: dict[str, ProbeFacts] = {}
    for key, spec in data.get(FIELD_PROBE_MODELS, {}).items():
        name = _table_key(FIELD_PROBE_MODELS, key)
        try:
            models[name] = ProbeFacts(
                heartbeat_minutes=spec[FIELD_HEARTBEAT_MINUTES],
                deadband_pp=spec[FIELD_DEADBAND_PP],
            )
        except ValueError as err:
            raise InvalidPlantConfig(f"probe model '{name}': {err}") from err
    return models


def _parse_care_tasks(data: Mapping[str, Any]) -> dict[str, CareTaskDef]:
    return {
        _table_key(FIELD_CARE_TASKS, key): CareTaskDef(
            display=spec[FIELD_DISPLAY],
            icon=spec[FIELD_ICON],
            detected_by=spec.get(FIELD_DETECTED_BY),
        )
        for key, spec in data.get(FIELD_CARE_TASKS, {}).items()
    }


def _parse_dli_categories(data: Mapping[str, Any]) -> dict[str, Band]:
    categories: dict[str, Band] = {}
    for key, spec in data.get(FIELD_DLI_CATEGORIES, {}).items():
        name = _table_key(FIELD_DLI_CATEGORIES, key)
        try:
            categories[name] = Band(low=spec[FIELD_LOW], high=spec[FIELD_HIGH])
        except ValueError as err:
            raise InvalidPlantConfig(f"dli category '{name}': {err}") from err
    return categories


def _parse_owners(data: Mapping[str, Any]) -> Owners:
    """Owner keys become entity ids and tab paths, so they are checked as
    identifiers. Group keys are another component's too, so they are not."""
    entries = {
        _table_key(FIELD_OWNERS, key): Owner(
            action=spec[FIELD_ACTION],
            icon=spec.get(FIELD_ICON),
        )
        for key, spec in data.get(FIELD_OWNERS, {}).items()
    }
    if RESERVED_OWNER_ALL in entries:
        raise InvalidPlantConfig(
            f"owner '{RESERVED_OWNER_ALL}' is reserved for the shared dashboard tab"
        )
    try:
        return Owners(
            entries=entries,
            groups={
                key: tuple(members)
                for key, members in data.get(FIELD_GROUPS, {}).items()
            },
            system_notify=data[FIELD_SYSTEM_NOTIFY],
        )
    except ValueError as err:
        raise InvalidPlantConfig(str(err)) from err


# ---- Fixtures -----------------------------------------------------------


def _parse_quiet_when(spec: Mapping[str, Any]) -> QuietMatcher:
    if FIELD_PRESENCE in spec:
        return PresenceMatcher(spec[FIELD_PRESENCE])
    matcher = spec[FIELD_QUIET_WHEN]
    if FIELD_PRESENCE in matcher:
        return PresenceMatcher(matcher[FIELD_PRESENCE])
    return BinarySensorMatcher(matcher[FIELD_BINARY_SENSOR])


def _parse_window(data: Mapping[str, Any], fixture: str) -> LightWindow:
    try:
        if FIELD_FIXED in data:
            spec = data[FIELD_FIXED]
            return FixedWindow(
                start=spec[FIELD_FROM],
                end=spec[FIELD_TO],
                days=frozenset(spec[FIELD_DAYS]) if FIELD_DAYS in spec else None,
            )
        spec = data[FIELD_AWAKE_AWARE]
        return AwakeAwareWindow(
            on_if_awake_after=spec[FIELD_ON_IF_AWAKE_AFTER],
            on_after=spec[FIELD_ON_AFTER],
            on_even_if_asleep_until=spec[FIELD_ON_EVEN_IF_ASLEEP_UNTIL],
            on_until=spec[FIELD_ON_UNTIL],
            quiet_when=_parse_quiet_when(spec),
            days=frozenset(spec[FIELD_DAYS]) if FIELD_DAYS in spec else None,
        )
    except ValueError as err:
        raise InvalidPlantConfig(f"light fixture '{fixture}': {err}") from err


def _parse_lights(data: Mapping[str, Any]) -> tuple[LightFixture, ...]:
    return tuple(
        LightFixture(
            name=entry[FIELD_NAME],
            switch_entity=entry[FIELD_SWITCH],
            window=_parse_window(entry[FIELD_WINDOW], entry[FIELD_NAME]),
            lux_to_ppfd=entry.get(FIELD_LUX_TO_PPFD),
        )
        for entry in data.get(FIELD_LIGHTS, [])
    )


def _parse_lux(data: Mapping[str, Any]) -> tuple[LuxFixture, ...]:
    return tuple(
        LuxFixture(
            name=entry[FIELD_NAME],
            entities=tuple(entry[FIELD_ENTITIES]),
            sun_lux_to_ppfd=entry.get(FIELD_SUN_LUX_TO_PPFD, DEFAULT_SUN_LUX_TO_PPFD),
        )
        for entry in data.get(FIELD_LUX_SENSORS, [])
    )


# ---- Plants -------------------------------------------------------------


def _title_case(slug: str) -> str:
    """`ficus_alii` becomes `Ficus Alii`. The default label when none is given."""
    return " ".join(word[:1].upper() + word[1:] for word in slug.split("_"))


def parse_calibration(data: Any, plant_name: str) -> Calibration:
    """`calibrating` means exactly that — never a default.

    A default substituted here would invent the number the input format exists
    to refuse, and the plant would be judged against it without anybody
    choosing it.
    """
    if data == CALIBRATING:
        return Calibrating()
    try:
        return Calibrated(
            field_capacity=data[FIELD_FIELD_CAPACITY],
            dry_point=data[FIELD_DRY_POINT],
            fc_tolerance=data.get(FIELD_FC_TOLERANCE),
            waterlogged_budget_pct=data.get(FIELD_WATERLOGGED_BUDGET_PCT),
        )
    except ValueError as err:
        raise InvalidPlantConfig(f"plant '{plant_name}': {err}") from err


def _parse_moisture(
    data: Mapping[str, Any], plant_name: str, probe_models: Mapping[str, ProbeFacts]
) -> Moisture:
    model = data[FIELD_MODEL]
    if model not in probe_models:
        raise InvalidPlantConfig(
            f"plant '{plant_name}' names probe model '{model}', which is not in "
            f"{FIELD_PROBE_MODELS}"
        )

    entities = data[FIELD_ENTITIES]
    temperature = entities.get(FIELD_ENTITY_TEMPERATURE)
    battery = entities.get(FIELD_ENTITY_BATTERY)

    return Moisture(
        moisture_entity=SourceEntity(entities[FIELD_ENTITY_MOISTURE]),
        temperature_entity=SourceEntity(temperature) if temperature else None,
        battery_entity=SourceEntity(battery) if battery else None,
        probe=probe_models[model],
        calibration=parse_calibration(data[FIELD_CALIBRATION], plant_name),
    )


def _parse_care(
    entries: list[Mapping[str, Any]],
    plant_name: str,
    calibrated: bool,
    care_tasks: Mapping[str, CareTaskDef],
) -> tuple[CareTask, ...]:
    care: list[CareTask] = []
    scheduled: set[str] = set()
    for entry in entries:
        task = entry[FIELD_TASK]
        definition = care_tasks.get(task)
        if definition is None:
            raise InvalidPlantConfig(
                f"plant '{plant_name}' schedules care task '{task}', which is not "
                f"in {FIELD_CARE_TASKS}"
            )
        if task in scheduled:
            raise InvalidPlantConfig(
                f"plant '{plant_name}' schedules care task '{task}' more than once"
            )
        scheduled.add(task)

        # No "I did a thing" button for anything a sensor already sees. A
        # calibrated probe detects every watering, whoever did it and whether
        # or not they had a phone; a reminder beside it would be a second,
        # worse source of truth.
        if calibrated and definition.detected_by == DETECTED_BY_CALIBRATED_MOISTURE:
            raise InvalidPlantConfig(
                f"plant '{plant_name}' schedules '{task}', but its calibrated "
                "moisture probe already detects it. Remove the care entry, or the "
                "reminder will compete with the detector"
            )

        care.append(
            CareTask(
                task=task,
                display=definition.display,
                icon=definition.icon,
                every_days=entry[FIELD_EVERY_DAYS],
            )
        )
    return tuple(care)


def _parse_dli(
    data: Mapping[str, Any] | None,
    plant_name: str,
    dli_categories: Mapping[str, Band],
) -> DliObjective | None:
    if data is None:
        return None

    category = data[FIELD_CATEGORY]
    band = dli_categories.get(category)
    if band is None:
        known = ", ".join(sorted(dli_categories)) or "nothing"
        raise InvalidPlantConfig(
            f"plant '{plant_name}' names dli category '{category}', which is not "
            f"in {FIELD_DLI_CATEGORIES} (known: {known})"
        )

    # Only what was written is passed on, so the objective's own defaults
    # apply to anything left out rather than a second copy of them here.
    kwargs: dict[str, Any] = {}
    if FIELD_SURVIVAL in data:
        kwargs["survival"] = Band(**data[FIELD_SURVIVAL])
    if FIELD_WINDOW_DAYS in data:
        kwargs["window_days"] = data[FIELD_WINDOW_DAYS]
    if FIELD_BUDGET in data:
        kwargs["budget"] = data[FIELD_BUDGET]

    try:
        if FIELD_PREFERRED in data:
            preferred = Band(**data[FIELD_PREFERRED])
            overridden = True
        else:
            preferred = band
            overridden = False
        return DliObjective(
            category=category,
            preferred=preferred,
            preferred_overridden=overridden,
            **kwargs,
        )
    except ValueError as err:
        raise InvalidPlantConfig(f"plant '{plant_name}': {err}") from err


def _parse_plant(
    data: Mapping[str, Any],
    probe_models: Mapping[str, ProbeFacts],
    care_tasks: Mapping[str, CareTaskDef],
    dli_categories: Mapping[str, Band],
) -> Plant:
    name = data[FIELD_NAME]
    moisture_data = data.get(FIELD_MOISTURE)
    moisture = (
        _parse_moisture(moisture_data, name, probe_models) if moisture_data else None
    )

    return Plant(
        name=name,
        display=data.get(FIELD_DISPLAY, _title_case(name)),
        species=data.get(FIELD_SPECIES),
        moisture=moisture,
        owner=data[FIELD_OWNER],
        care=_parse_care(
            data.get(FIELD_CARE, []),
            name,
            calibrated=moisture is not None and moisture.is_calibrated,
            care_tasks=care_tasks,
        ),
        lights=tuple(data.get(FIELD_LIGHTS, [])),
        lux=data.get(FIELD_LUX),
        dli=_parse_dli(data.get(FIELD_DLI), name, dli_categories),
    )


# ---- The whole document -------------------------------------------------


@dataclass(frozen=True)
class PlantCareConfig:
    """The whole config, parsed.

    Fixtures are separate from plants because they are shared: one lamp serves
    several plants, so repeating it per plant would let two copies disagree.
    """

    plants: tuple[Plant, ...]
    owners: Owners
    lights: tuple[LightFixture, ...] = ()
    lux_sensors: tuple[LuxFixture, ...] = ()

    def light(self, name: str) -> LightFixture | None:
        return next((f for f in self.lights if f.name == name), None)

    def lux(self, name: str) -> LuxFixture | None:
        return next((f for f in self.lux_sensors if f.name == name), None)

    def fixtures_for(self, plant: Plant) -> tuple[LightFixture, ...]:
        return tuple(f for name in plant.lights if (f := self.light(name)) is not None)

    def plants_for(self, person: str) -> tuple[Plant, ...]:
        """Owned outright, or through a group."""
        return tuple(p for p in self.plants if person in self.owners.members(p.owner))

    def fixtures_for_person(self, person: str) -> tuple[LightFixture, ...]:
        wanted = {name for plant in self.plants_for(person) for name in plant.lights}
        return tuple(f for f in self.lights if f.name in wanted)

    def unreferenced_lights(self) -> tuple[LightFixture, ...]:
        """Fixtures no plant sits under.

        Not an error — the lamp still runs its window. But nothing under it
        means no correct on-time, so its outcome checks are skipped, and it is
        usually a typo. Worth saying once at startup.
        """
        referenced = {name for plant in self.plants for name in plant.lights}
        return tuple(f for f in self.lights if f.name not in referenced)


def _reject_duplicates(names: list[str], what: str) -> None:
    seen: set[str] = set()
    for name in names:
        if name in seen:
            raise InvalidPlantConfig(f"{what} '{name}' is defined more than once")
        seen.add(name)


def parse(data: Mapping[str, Any]) -> PlantCareConfig:
    """Build the model from an already schema-validated config.

    Everything a name refers to is resolved here, so nothing downstream ever
    holds a reference that might not exist. Duplicate names are refused
    because every entity id this component creates is built from the name, so
    two plants sharing one would collide silently and the second would win.
    """
    probe_models = _parse_probe_models(data)
    care_tasks = _parse_care_tasks(data)
    dli_categories = _parse_dli_categories(data)

    config = PlantCareConfig(
        plants=tuple(
            _parse_plant(entry, probe_models, care_tasks, dli_categories)
            for entry in data[FIELD_PLANTS]
        ),
        owners=_parse_owners(data),
        lights=_parse_lights(data),
        lux_sensors=_parse_lux(data),
    )

    _reject_duplicates([p.name for p in config.plants], "plant")
    _reject_duplicates([f.name for f in config.lights], "light fixture")
    _reject_duplicates([f.name for f in config.lux_sensors], "lux fixture")

    fixture_names = {f.name for f in config.lights}
    lux_names = {f.name for f in config.lux_sensors}

    for plant in config.plants:
        if plant.owner not in config.owners:
            raise InvalidPlantConfig(
                f"plant '{plant.name}' names owner '{plant.owner}', which is not "
                f"in {FIELD_OWNERS}"
            )
        for name in plant.lights:
            if name not in fixture_names:
                raise InvalidPlantConfig(
                    f"plant '{plant.name}' names light fixture '{name}', which is "
                    f"not in {FIELD_LIGHTS}"
                )
        if plant.lux is not None and plant.lux not in lux_names:
            raise InvalidPlantConfig(
                f"plant '{plant.name}' names lux fixture '{plant.lux}', which is "
                f"not in {FIELD_LUX_SENSORS}"
            )
        if plant.dli is not None and plant.lux is None:
            raise InvalidPlantConfig(
                f"plant '{plant.name}' has a dli objective with no lux fixture, so "
                "nothing would measure it"
            )
        # A mixed plant's lux reading is sun for part of the day and lamp for the
        # rest, so a fixture over it that cannot say what its own light is worth
        # makes every DLI figure for this plant wrong — quietly, and in a
        # direction nobody can guess. Refuse rather than compute it.
        if plant.is_mixed_light:
            for fixture in config.fixtures_for(plant):
                if fixture.lux_to_ppfd is None:
                    raise InvalidPlantConfig(
                        f"plant '{plant.name}' is lit by both sun and lamp, but "
                        f"fixture '{fixture.name}' declares no {FIELD_LUX_TO_PPFD}, "
                        "so its contribution cannot be converted"
                    )

    return config
