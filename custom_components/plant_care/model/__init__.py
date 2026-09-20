"""The plant care domain model — pure Python, no Home Assistant.

Everything in this package can be imported and tested without Home Assistant
installed, which is why the unit tests need no framework mocking. The Home
Assistant edge lives in the platform modules one level up.
"""

from . import markdown
from .config import (
    InvalidPlantConfig,
    PlantCareConfig,
    parse,
    parse_calibration,
    schema,
)
from .dli import (
    Band,
    BurnAlert,
    BurnKind,
    DailyDli,
    Direction,
    DliAccumulator,
    DliObjective,
    outside_survival,
    today_certainty,
    unstable,
)
from .light import (
    AwakeAwareWindow,
    FixedWindow,
    LightFixture,
    LightWindow,
    Lit,
    LuxFixture,
    OnTimeDeviation,
    Weekday,
    is_asleep,
    on_time_deviation,
    ppfd_factor,
)
from .owners import Owners
from .plant import (
    Calibrated,
    Calibrating,
    Calibration,
    CareTask,
    Domain,
    Entity,
    Moisture,
    Plant,
    ProbeFacts,
    SourceEntity,
)
from .policy import DEFAULT_POLICY, Policy

__all__ = [
    "DEFAULT_POLICY",
    "AwakeAwareWindow",
    "Band",
    "BurnAlert",
    "BurnKind",
    "Calibrated",
    "Calibrating",
    "Calibration",
    "CareTask",
    "DailyDli",
    "Direction",
    "DliAccumulator",
    "DliObjective",
    "Domain",
    "Entity",
    "FixedWindow",
    "InvalidPlantConfig",
    "LightFixture",
    "LightWindow",
    "Lit",
    "LuxFixture",
    "Moisture",
    "OnTimeDeviation",
    "Owners",
    "Plant",
    "PlantCareConfig",
    "Policy",
    "ProbeFacts",
    "SourceEntity",
    "Weekday",
    "is_asleep",
    "markdown",
    "on_time_deviation",
    "outside_survival",
    "parse",
    "parse_calibration",
    "ppfd_factor",
    "schema",
    "today_certainty",
    "unstable",
]
